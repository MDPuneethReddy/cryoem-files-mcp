"""Regression tests for the fixes to the 0.1.0 QA report (docs/dev/QA-REPORT.md): one section per finding ID."""

import os
import subprocess
import sys
import threading
import time

import anyio
import mrcfile
import numpy as np
import pytest
import tifffile

from cryoem_files_mcp import inspect as insp
from cryoem_files_mcp import server
from cryoem_files_mcp.gain import VARIANTS, bin2, check_orientation, convert_gain, inspect_gain, transform
from cryoem_files_mcp.inspect import identify_file, inspect_mrc, scan_directory
from cryoem_files_mcp.meta import summarize_star
from cryoem_files_mcp.paths import PathNotAllowed, resolve

from .conftest import make_gain
from .test_damaged import err, good_stack_mrc


def no_hang(fn, *a, seconds=10, **kw):
    """Run fn in a thread; fail (instead of hanging the test run) if it blocks."""
    box = {}

    def target():
        try:
            box["value"] = fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"{fn.__name__} blocked for more than {seconds} s"
    if "error" in box:
        raise box["error"]
    return box["value"]


# ------------------------------------------------------------------ H1: special files

@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_fifo_is_refused_without_opening(tmp_path):
    os.mkfifo(tmp_path / "fifo.mrc")
    for fn in (identify_file, inspect_mrc, summarize_star, inspect_gain):
        msg = err(no_hang, fn, tmp_path / "fifo.mrc")
        assert "named pipe (FIFO), not a regular file" in msg and "Fix:" in msg
    r = no_hang(scan_directory, tmp_path)
    assert r["skipped"]["special_files"] == 1
    assert any("special file" in p and "fifo.mrc" in p for p in r["problems"])


def test_tool_call_timeout_returns_error(monkeypatch):
    monkeypatch.setattr(server, "TIMEOUT_S", 0.2)

    async def go():
        with pytest.raises(server.ToolError, match="did not finish within 0.2 s"):
            await server._run(lambda: time.sleep(2))

    anyio.run(go)


# ------------------------------------------------------------------ H2 / M3: orientation significance

def _movie(path, g, k, flip, dose, frames, seed, bin_to=False):
    rng = np.random.default_rng(seed)
    m = transform(g, k, flip).astype(np.float64)
    lam = dose / np.where(m > 0, m, 1.0)
    if bin_to:
        h, w = lam.shape
        lam = lam.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3)) / 4
    tifffile.imwrite(path, np.stack([rng.poisson(lam).astype(np.uint8) for _ in range(frames)]))
    return path


def _gain(shape, var, seed):
    rng = np.random.default_rng(seed)
    return (1 + var * rng.standard_normal(shape)).astype(np.float32)


def test_unrelated_gain_never_recommends_a_conversion(tmp_path):
    for t in range(20):
        _movie(tmp_path / "m.tif", _gain((256, 256), 0.08, t), *VARIANTS[t % 8], 3.0, 20, 100 + t)
        tifffile.imwrite(tmp_path / "other.gain", _gain((256, 256), 0.08, 500 + t))
        r = check_orientation(tmp_path / "other.gain", tmp_path / "m.tif")
        assert r["confidence"] == "no_match", r
        assert "next_step" not in r and "best" not in r
        assert "does not match this movie" in r["problem"] and "Fix:" in r["problem"]
        assert r["z_best"] < 6 and r["pixels_compared"] > 60_000


@pytest.mark.parametrize("var", [0.02, 0.01])
def test_weak_realistic_gain_is_high(tmp_path, var):
    g = _gain((512, 512), var, 7)
    _movie(tmp_path / "m.tif", g, 3, True, 0.5, 20, 8)
    tifffile.imwrite(tmp_path / "g.gain", g)
    r = check_orientation(tmp_path / "g.gain", tmp_path / "m.tif")
    assert r["confidence"] == "high" and r["z_best"] >= 10 and r["z_gap"] >= 6
    assert np.array_equal(transform(g, r["rot90_ccw"], r["flip_y"]), transform(g, 3, True))
    assert r["next_step"]["motioncor"].startswith("convert_gain")


def test_super_resolution_gain_binned_to_counting_movie_is_high(tmp_path):
    g = _gain((1024, 1024), 0.04, 11)  # Falcon-style: gain at 2x the movie size
    _movie(tmp_path / "m.tif", g, 0, False, 0.5, 20, 12, bin_to=True)
    tifffile.imwrite(tmp_path / "g.gain", g)
    r = check_orientation(tmp_path / "g.gain", tmp_path / "m.tif")
    assert r["confidence"] == "high" and r["best"] == "rot0" and "binned" in r["scaling"]


def test_orientation_rejects_bad_n_frames(tmp_path):
    assert "n_frames must be at least 1" in err(check_orientation, tmp_path / "a", tmp_path / "b", n_frames=0)


# ------------------------------------------------------------------ M1 / L4: symlinks and paths

def test_scan_skips_symlinks_outside_roots(tmp_path):
    os.symlink("/etc/passwd", tmp_path / "leak.mrc")
    os.symlink("/etc", tmp_path / "etcdir")
    good_stack_mrc(tmp_path / "ok.mrc")
    os.symlink(tmp_path / "ok.mrc", tmp_path / "inside_link.mrc")
    r = scan_directory(tmp_path)
    text = repr(r)
    assert "root:x" not in text and "72 6f 6f 74" not in text and "/etc/passwd" not in text
    assert r["skipped"]["outside_symlinks"] == 2 and r["by_extension"][".mrc"]["count"] == 2
    assert any("pointing outside" in n for n in r["notes"])


def test_symlink_error_does_not_reveal_target_and_missing_is_relative(tmp_path):
    os.symlink("/etc/hostname", tmp_path / "sneaky.mrc")
    with pytest.raises(PathNotAllowed) as e:
        resolve("sneaky.mrc")
    assert "/etc" not in str(e.value).split("(")[0] and str(e.value).startswith("sneaky.mrc: is outside")
    with pytest.raises(FileNotFoundError) as e:
        resolve("sub/missing.mrc")
    msg = str(e.value)
    assert msg.startswith("sub/missing.mrc: does not exist.") and "Likely cause:" in msg and "Fix:" in msg


# ------------------------------------------------------------------ M2: bin before invert

def test_convert_bins_before_inverting(tmp_path):
    g = _gain((64, 64), 0.05, 3)
    g[10, 10] = 0  # defect
    with mrcfile.new(tmp_path / "eer_gain.mrc") as m:
        m.set_data(g)
    out = convert_gain(tmp_path / "eer_gain.mrc", invert=True, bin=2)
    with mrcfile.open(out["output"]) as m:
        got = np.array(m.data)
    assert np.allclose(got, 1 / bin2(g), rtol=1e-5)
    assert out["applied"] == ["binned 2x (mean)", "inverted (1/gain; zero/defect pixels stay 0)"]


# ------------------------------------------------------------------ M4 / M5 / L3 / L5: scan limits and heuristics

def test_scan_depth_default_and_reporting(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f"
    deep.mkdir(parents=True)
    tifffile.imwrite(deep / "hidden_movie.tif", np.zeros((2, 8, 8), np.uint8))
    assert scan_directory(tmp_path)["likely_movies"] == 1  # default max_depth=8 reaches it
    r = scan_directory(tmp_path, max_depth=3)
    assert r["skipped"]["deeper_folders"] == 1
    assert "not scanned" in r["problems"][0] and "max_depth" in r["problems"][0]
    assert "folder is empty" not in " ".join(r["problems"])


def test_scan_reports_header_check_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(insp, "MRC_HEADER_CHECKS", 3)
    for i in range(5):
        good_stack_mrc(tmp_path / f"m{i}.mrc", n=2, shape=(8, 8))
    r = scan_directory(tmp_path)
    assert r["mrc_headers_checked"] == "3 of 5"
    assert any("header-checked 3 of 5 MRC files" in n for n in r["notes"])


def test_scan_rejects_bad_limits(tmp_path):
    assert "limit must be at least 1" in err(scan_directory, tmp_path, limit=-5)
    assert "max_depth must be at least 1" in err(scan_directory, tmp_path, max_depth=0)


def test_scan_movie_heuristic(tmp_path):
    good_stack_mrc(tmp_path / "FoilHole_1_Data_2_fractions.mrc")
    (tmp_path / "notes.tif").write_text("abc")  # not a TIFF
    (tmp_path / "x.eer").write_bytes(b"II+\x00" + b"\x00" * 100)
    r = scan_directory(tmp_path)
    assert r["likely_movies"] == 2
    assert any("no gain reference" in p for p in r["problems"])


# ------------------------------------------------------------------ M6 / L6: STAR

STAR_QUOTED = """
data_micrographs

loop_
_rlnMicrographName #1
_rlnDefocusU #2
'my mic 1.mrc' 12000
"my mic 2.mrc" 13000
plain.mrc 14000
"""

MODEL_STAR = """
data_model_general

_rlnReferenceDimensionality 3
_rlnNrClasses 2

data_model_classes

loop_
_rlnReferenceImage #1
_rlnClassDistribution #2
run_class001.mrc 0.6
run_class002.mrc 0.4
"""


def test_star_quoted_values_are_not_damaged(tmp_path):
    (tmp_path / "q.star").write_text(STAR_QUOTED)
    r = summarize_star(tmp_path / "q.star")
    assert "problems" not in r and r["blocks"]["micrographs"]["rows"] == 3


def test_optics_note_only_for_image_star(tmp_path):
    (tmp_path / "run_model.star").write_text(MODEL_STAR)
    assert "notes" not in summarize_star(tmp_path / "run_model.star")
    (tmp_path / "q.star").write_text(STAR_QUOTED)
    assert "no optics block" in summarize_star(tmp_path / "q.star")["notes"][0]


# ------------------------------------------------------------------ M7 / L2: identify_file

@pytest.mark.parametrize("shape,name,role", [
    ((64, 64, 64), "x.mrc", "3D map/volume"),
    ((1, 256, 256), "x.mrc", "micrograph or 2D image"),
    ((200, 128, 128), "x.mrc", "particle stack"),
    ((5, 64, 64), "x.mrcs", "particle stack"),
    ((30, 300, 400), "x.mrc", "movie or image stack"),
])
def test_mrc_role_guess(tmp_path, shape, name, role):
    with mrcfile.new(tmp_path / name) as m:
        m.set_data(np.zeros(shape, np.float32))
    assert identify_file(tmp_path / name)["role_guess"] == role


def test_identify_folder_raises_and_unrecognised_is_not_ok(tmp_path):
    (tmp_path / "sub").mkdir()
    assert "is a folder, not a file" in err(identify_file, tmp_path / "sub")
    (tmp_path / "blob.bin").write_bytes(os.urandom(2000))
    r = identify_file(tmp_path / "blob.bin")
    assert r["format"] == "unrecognised" and r["integrity"].startswith("unrecognised")


# ------------------------------------------------------------------ L7: format names

def test_format_names_consistent(tmp_path):
    tifffile.imwrite(tmp_path / "g.gain", make_gain((32, 32)))
    assert inspect_gain(tmp_path / "g.gain")["format"] == "TIFF"
    assert identify_file(tmp_path / "g.gain")["format"] == "TIFF"
    assert convert_gain(tmp_path / "g.gain")["input_format"] == "TIFF"


# ------------------------------------------------------------------ M8: bad roots

def test_bad_root_refused_at_startup(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    for bad in (tmp_path / "nope", tmp_path / "f.txt"):
        p = subprocess.run([sys.executable, "-m", "cryoem_files_mcp.server", "--root", str(bad)],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        assert p.returncode == 2 and "cannot start" in p.stderr
    env = dict(os.environ, CRYOEM_MCP_ROOTS=str(tmp_path / "nope"))
    p = subprocess.run([sys.executable, "-m", "cryoem_files_mcp.server"], capture_output=True, text=True,
                       timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert p.returncode == 2 and "CRYOEM_MCP_ROOTS" in p.stderr and "does not exist" in p.stderr


# ------------------------------------------------------------------ L1 / L8: tool metadata and argument errors

def test_every_tool_has_a_title():
    tools = anyio.run(server.mcp.list_tools)
    assert len(tools) == 9 and all(t.title for t in tools)


def test_argument_errors_are_short(tmp_path):
    async def go():
        with pytest.raises(server.ToolError) as e:
            await server.mcp.call_tool("scan_directory", {"path": ".", "limit": -5})
        return str(e.value)

    msg = anyio.run(go)
    assert "limit: input should be greater than or equal to 1 (got -5)" in msg
    assert "pydantic" not in msg and "http" not in msg and "\n" not in msg


# ================================================================== pass 2 (docs/dev/RESEARCH-NOTES.md, docs/dev/RELEASE-PLAN.md)

import re  # noqa: E402

from cryoem_files_mcp.gain import _advice  # noqa: E402


def _motioncor_view_of_gain(g_file: np.ndarray, flags: str) -> np.ndarray:
    """What MotionCor3 does to an MRC gain: -RotGain k == numpy.rot90(g, -k), then -FlipGain 1 == flipud."""
    k = int(re.search(r"-RotGain (\d)", flags).group(1))
    f = int(re.search(r"-FlipGain (\d)", flags).group(1))
    out = np.rot90(g_file, -k)
    return np.flipud(out) if f == 1 else out


@pytest.mark.parametrize("k,flip", VARIANTS)
def test_tiff_movie_gain_is_written_in_motioncor_row_order(tmp_path, k, flip):
    """Item 1/3: MotionCor and RELION read TIFF movie rows bottom-up and MRC gain rows as stored."""
    g = make_gain((96, 128), seed=k * 2 + flip)
    with mrcfile.new(tmp_path / "g.mrc") as m:
        m.set_data(g)
    _movie(tmp_path / "m.tif", g, k, flip, 3.0, 20, 5)
    r = check_orientation(tmp_path / "g.mrc", tmp_path / "m.tif")
    assert r["confidence"] == "high" and r["movie_type"] == "tiff"
    assert "movie_type='tiff'" in r["next_step"]["motioncor"]
    # the fixed pattern MotionCor sees in the TIFF movie is the tifffile pattern flipped upside down
    seen_by_motioncor = np.flipud(transform(g, k, flip))
    out = convert_gain(tmp_path / "g.mrc", rotate=r["rot90_ccw"], flip="y" if r["flip_y"] else "none", movie_type="tiff")
    with mrcfile.open(out["output"]) as m:
        assert np.array_equal(np.array(m.data), seen_by_motioncor)
    assert any("TIFF movies" in a for a in out["applied"])
    # flags for the original MRC gain: (4-k)%4 and the extra flip
    assert np.array_equal(_motioncor_view_of_gain(g, r["motioncor_flags_for_original_gain"]), seen_by_motioncor)


@pytest.mark.parametrize("k,flip", [(1, False), (3, True)])
def test_mrc_movie_gain_kept_in_file_order(tmp_path, k, flip):
    g = make_gain((64, 64), seed=4)
    with mrcfile.new(tmp_path / "g.mrc") as m:
        m.set_data(g)
    lam = 3.0 / np.where(transform(g, k, flip) > 0, transform(g, k, flip), 1.0)
    with mrcfile.new(tmp_path / "movie_frames.mrc") as m:
        m.set_data(np.random.default_rng(1).poisson(lam, (20, 64, 64)).astype(np.int16))
    r = check_orientation(tmp_path / "g.mrc", tmp_path / "movie_frames.mrc")
    assert r["movie_type"] == "mrc" and "movie_type='mrc'" in r["next_step"]["motioncor"]
    flags = r["motioncor_flags_for_original_gain"]
    assert flags.startswith(f"-RotGain {(4 - k) % 4} ")
    assert np.array_equal(_motioncor_view_of_gain(g, flags), transform(g, k, flip))
    out = convert_gain(tmp_path / "g.mrc", rotate=k, flip="y" if flip else "none", movie_type="mrc")
    with mrcfile.open(out["output"]) as m:
        assert np.array_equal(np.array(m.data), transform(g, k, flip))


def test_convert_without_movie_type_explains_row_order(tmp_path):
    tifffile.imwrite(tmp_path / "g.gain", make_gain((32, 32)))
    out = convert_gain(tmp_path / "g.gain")
    assert "movie_type='tiff'" in out["row_order"]
    assert "check" in out["check_first"] and "-RotGain 0 -FlipGain 0" in out["use_with_motioncor"]
    assert "movie_type must be" in err(convert_gain, tmp_path / "g.gain", movie_type="dm4")


def test_relion_eer_advice_depends_on_extension_and_convention(tmp_path):
    """Item 2: RELION multiplies by .gain for EER and divides by anything else (MRC included)."""
    falcon = _advice(tmp_path / "Gain.gain", "tiff", "eer", 0, False, False, None)
    assert "original .gain" in falcon["relion"]
    assert "invert" not in falcon["motioncor"] and "movie_type='eer'" in falcon["motioncor"]
    rotated = _advice(tmp_path / "Gain.gain", "tiff", "eer", 1, False, False, None)
    assert "invert=true" in rotated["relion"] and "divisive MRC" in rotated["relion"]
    old_mrc = _advice(tmp_path / "eer_gain.mrc", "mrc", "eer", 0, False, True, None)
    assert "invert=true" in old_mrc["motioncor"] and "invert=true" not in old_mrc["relion"]
    tiff = _advice(tmp_path / "k3.dm4", "dm4", "tiff", 2, True, False, None)
    assert tiff["relion"].startswith(tiff["motioncor"].split(" writes")[0]) and "bottom-up" in tiff["relion"]
    assert "one movie" in tiff["check_first"]


# ------------------------------------------------------------------ item 4: STAR "" and comments

STAR_RELION_STYLE = """
# version 50001

data_micrographs

loop_
_rlnMicrographName #1
_rlnDefocusU #2
_rlnComment #3
"my mic 1.mrc" 12000 ""
plain.mrc 13000 "" # flagged by user
other.mrc 14000 ok
"""


def test_star_empty_quotes_and_trailing_comments(tmp_path):
    (tmp_path / "r.star").write_text(STAR_RELION_STYLE)
    r = summarize_star(tmp_path / "r.star")
    assert "problems" not in r and r["blocks"]["micrographs"]["rows"] == 3
    (tmp_path / "bad.star").write_text(STAR_RELION_STYLE.replace("other.mrc 14000 ok", "other.mrc 14000 ok extra # c"))
    assert "4 values but 3 columns" in err(summarize_star, tmp_path / "bad.star")


# ------------------------------------------------------------------ item 5 (PM-3): no implicit root

def test_no_root_refuses_to_start(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "CRYOEM_MCP_ROOTS"}
    for cwd in ("/", os.path.expanduser("~"), str(tmp_path)):
        p = subprocess.run([sys.executable, "-m", "cryoem_files_mcp.server"], capture_output=True, text=True,
                           timeout=60, env=env, cwd=cwd, stdin=subprocess.DEVNULL)
        assert p.returncode == 2 and "--root" in p.stderr and "CRYOEM_MCP_ROOTS" in p.stderr


# ------------------------------------------------------------------ item 7 (PM-5): unexpected errors

def test_unexpected_exception_is_reported_without_traceback(tmp_path, monkeypatch, capsys):
    (tmp_path / "x.mrc").write_bytes(b"x")

    def boom(*a, **k):
        raise RuntimeError("boom\nsecond line")

    monkeypatch.setattr(insp, "identify_file", boom)

    async def go():
        with pytest.raises(server.ToolError) as e:
            await server.mcp.call_tool("identify_file", {"path": "x.mrc"})
        return str(e.value)

    msg = anyio.run(go)
    assert "internal error in identify_file: RuntimeError: boom second line." in msg
    assert server.ISSUES_URL in msg and "Traceback" not in msg and "\n" not in msg
    assert "Traceback" in capsys.readouterr().err


# ================================================================== pass 3 (docs/dev/QA-REPORT-v2.md)

import json  # noqa: E402

from cryoem_files_mcp import formats as fmt_mod  # noqa: E402
from cryoem_files_mcp import gain as gain_mod  # noqa: E402
from cryoem_files_mcp.inspect import inspect_movie  # noqa: E402
from cryoem_files_mcp.meta import parse_mdoc  # noqa: E402
from mrcfile.dtypes import HEADER_DTYPE  # noqa: E402


def _call_tool(name, args):
    async def go():
        try:
            res = await server.mcp.call_tool(name, args)
            return False, res
        except server.ToolError as e:
            return True, str(e)

    return anyio.run(go)


# ------------------------------------------------------------------ N1: non-UTF-8 names

@pytest.mark.skipif(sys.platform == "darwin", reason="macOS refuses non-UTF-8 names")
def test_non_utf8_filename_never_breaks_scan(tmp_path):
    with open(os.path.join(os.fsencode(tmp_path), b"bad\xffutf8.gain"), "wb") as f:
        f.write(b"x")
    is_err, res = _call_tool("scan_directory", {"path": "."})
    assert not is_err
    text = res.content[0].text
    assert "bad\\xffutf8.gain" in json.loads(text)["gain_references_found"][0]
    assert server.safe({"a": "x\udcffy", "n": float("nan")}) == {"a": "x\\xffy", "n": None}


def test_serialisation_failure_is_reported_as_internal_error(monkeypatch):
    monkeypatch.setattr(server, "safe", lambda x: x)  # let a non-serialisable result reach the SDK
    monkeypatch.setattr(insp, "scan_directory", lambda *a, **k: {"x": "bad\udcffname"})
    is_err, msg = _call_tool("scan_directory", {"path": "."})
    assert is_err and "internal error in scan_directory" in msg and server.ISSUES_URL in msg


# ------------------------------------------------------------------ N2: MRC modes 101 and 3

def _raw_mrc(path, mode, nx, ny, nz, data: bytes):
    h = np.zeros(1, HEADER_DTYPE)
    h["nx"], h["ny"], h["nz"], h["mode"] = nx, ny, nz, mode
    h["mapc"], h["mapr"], h["maps"] = 1, 2, 3
    h["cella"]["x"], h["cella"]["y"], h["cella"]["z"] = nx, ny, nz
    h["mx"], h["my"], h["mz"] = nx, ny, nz
    h["map"] = b"MAP "
    h["machst"] = [0x44, 0x44, 0, 0]
    h["nversion"] = 20140
    path.write_bytes(h.tobytes() + data)
    return path


def test_mode_101_is_decoded_not_damaged(tmp_path):
    nx, ny, nz = 63, 64, 10
    pix = np.random.default_rng(0).integers(0, 16, (nz, ny, nx), dtype=np.uint8)
    padded = np.concatenate([pix, np.zeros((nz, ny, 1), np.uint8)], axis=2)  # rows padded to whole bytes
    packed = (padded[..., 0::2] | (padded[..., 1::2] << 4)).astype(np.uint8)
    p = _raw_mrc(tmp_path / "frames_4bit.mrc", 101, nx, ny, nz, packed.tobytes())
    r = identify_file(p, verify_data=True)
    assert r["integrity"] == "ok", r
    m = fmt_mod.open_mrc(p)
    try:
        assert np.array_equal(np.asarray(m.data), pix) and np.array_equal(m.data[3], pix[3])
    finally:
        m.close()
    mi = inspect_mrc(p)
    assert mi["mrc2014_valid"] and "mode 101" in mi["note"] and "problems" not in mi
    assert inspect_movie(p)["frames"] == 10
    cut_p = _raw_mrc(tmp_path / "cut.mrc", 101, nx, ny, nz, packed.tobytes()[:-100])
    assert "100 bytes missing" in identify_file(cut_p)["problems"][0]


def test_mode_3_complex_int16_is_decoded(tmp_path):
    a = np.random.default_rng(1).integers(-50, 50, (8, 8, 2), dtype=np.int16)
    p = _raw_mrc(tmp_path / "c.mrc", 3, 8, 8, 1, a.tobytes())
    assert identify_file(p, verify_data=True)["integrity"] == "ok"
    m = fmt_mod.open_mrc(p)
    try:
        assert np.allclose(np.asarray(m.data), a[..., 0] + 1j * a[..., 1])
    finally:
        m.close()


# ------------------------------------------------------------------ N3: scan names flagged files

def test_scan_names_damaged_and_truncated_files(tmp_path):
    from .test_damaged import cut, good_movie_tif

    for i in range(3):
        cut(good_stack_mrc(tmp_path / f"bad_{i}.mrc"), 0.5)
    for i in range(6):
        good_movie_tif(tmp_path / f"movie_{i}.tif", n=20, compression=None)
    cut(tmp_path / "movie_5.tif", 0.2)
    r = scan_directory(tmp_path)
    text = " | ".join(r["problems"])
    assert "3 damaged MRC files: bad_0.mrc, bad_1.mrc, bad_2.mrc" in text
    assert r["flagged_files"]["damaged_mrc"] == ["bad_0.mrc", "bad_1.mrc", "bad_2.mrc"]
    assert r["flagged_files"]["possibly_truncated"] == ["movie_5.tif"]


# ------------------------------------------------------------------ N4 / N5: gain size and CryoSPARC advice

def test_double_size_gain_advice_bins_for_tiff_movies(tmp_path):
    g = _gain((512, 512), 0.04, 21)
    _movie(tmp_path / "m.tif", g, 0, False, 0.5, 20, 22, bin_to=True)
    tifffile.imwrite(tmp_path / "g.gain", g)
    r = check_orientation(tmp_path / "g.gain", tmp_path / "m.tif")
    ns = r["next_step"]
    assert "bin=2" in ns["motioncor"] and "bin=2" in ns["relion"] and "bin=2" in ns["cryosparc"]
    out = convert_gain(tmp_path / "g.gain", rotate=0, bin=2, movie_type="tiff")
    assert out["shape_yx"] == [256, 256]


def test_cryosparc_entry_is_hedged_and_prompt_matches(tmp_path):
    adv = _advice(tmp_path / "k3.dm4", "dm4", "tiff", 1, False, False, None)
    assert "Not researched" in adv["cryosparc"] and "one movie" in adv["cryosparc"]
    assert "movie_type" not in adv["cryosparc"]
    eer = _advice(tmp_path / "old.mrc", "mrc", "eer", 0, False, True, "gain binned 2x to match movie")
    assert "invert=true, bin=2" in eer["cryosparc"] and "4096x4096" in eer["cryosparc"] and "forum" in eer["cryosparc"]
    assert "-EerSampling" in eer["motioncor"] and "bin=2" not in eer["relion"]
    prompt = server.prepare_for("CryoSPARC", "/data")
    assert "'cryosparc'" in prompt


# ------------------------------------------------------------------ item 7 / N7: STAR '#' inside tokens and ';' lines

def test_star_hash_inside_value_and_semicolon_lines(tmp_path):
    (tmp_path / "s.star").write_text("\ndata_micrographs\n\nloop_\n_rlnMicrographName #1\n_rlnDefocusU #2\n"
                                     "Movies/grid#2_001.mrc 12000\n; a text line\nother.mrc 13000 # note\n")
    r = summarize_star(tmp_path / "s.star")
    assert "problems" not in r and r["blocks"]["micrographs"]["rows"] == 2
    assert r["blocks"]["micrographs"]["stats"]["defocus_U_A"]["max"] == 13000


# ------------------------------------------------------------------ N6, N8, N9, N11, N12, N14

def test_convert_replaces_nan_pixels_and_says_so(tmp_path):
    g = make_gain((32, 32))
    g[3] = np.nan
    tifffile.imwrite(tmp_path / "nanrows.gain", g)
    out = convert_gain(tmp_path / "nanrows.gain")
    assert "32 gain pixels" in out["problems"][0] and "set to 0" in out["problems"][0]
    with mrcfile.open(out["output"]) as m:
        assert np.isfinite(m.data).all() and (m.data[3] == 0).all()


def test_library_errors_are_readable(tmp_path, monkeypatch):
    tifffile.imwrite(tmp_path / "rgb.tif", np.zeros((64, 64, 3), np.uint8))
    assert "colour/multi-channel TIFF (3 samples per pixel)" in err(inspect_movie, tmp_path / "rgb.tif")
    (tmp_path / "bad.mdoc").write_text("PixelSpacing = 1\n[ZValue = 0]\nTiltAngle = abc\n")
    msg = err(parse_mdoc, tmp_path / "bad.mdoc")
    assert "TiltAngle" in msg and "http" not in msg and "\n" not in msg
    tifffile.imwrite(tmp_path / "g.gain", make_gain((16, 16)))
    is_err, msg = _call_tool("convert_gain", {"gain": "g.gain", "out": "x" * 300 + ".mrc"})
    assert is_err and "File name too long" in msg and str(tmp_path) not in msg and "Fix:" in msg
    good_stack_mrc(tmp_path / "s.mrc")
    tifffile.imwrite(tmp_path / "m.tif", np.zeros((2, 8, 8), np.uint8))
    assert "Fix: use inspect_movie" in err(inspect_mrc, tmp_path / "m.tif")


def test_memory_error_while_decoding_is_not_called_corruption(tmp_path, monkeypatch):
    tifffile.imwrite(tmp_path / "m.tif", np.zeros((2, 8, 8), np.uint8), compression="zlib")

    def no_memory(*a, **k):
        raise MemoryError

    monkeypatch.setattr(tifffile.TiffPage, "asarray", no_memory)
    msg = err(inspect_movie, tmp_path / "m.tif")
    assert "not enough memory to decode it" in msg and "corrupted" not in msg


def test_out_equal_to_input_is_refused(tmp_path):
    with mrcfile.new(tmp_path / "g.mrc") as m:
        m.set_data(make_gain((16, 16)))
    msg = err(convert_gain, tmp_path / "g.mrc", out="g.mrc", overwrite=True)
    assert "is the input file" in msg
    assert not (tmp_path / "g_converted.mrc").exists()


def test_concurrent_converts_to_same_out_all_succeed(tmp_path):
    tifffile.imwrite(tmp_path / "g.gain", make_gain((256, 256)))
    errors = []

    def one():
        try:
            convert_gain(tmp_path / "g.gain", out="same.mrc", overwrite=True)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))

    threads = [threading.Thread(target=one) for _ in range(12)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors and not list(tmp_path.glob("*.partial"))
    with mrcfile.open(tmp_path / "same.mrc") as m:
        assert m.data.shape == (256, 256)


def test_heavy_operations_are_limited(monkeypatch):
    monkeypatch.setattr(server, "TIMEOUT_S", 1.2)
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # one slot, already taken by a "stuck" call
    monkeypatch.setattr(server, "_heavy", sem)
    with pytest.raises(server.ToolError, match="server busy"):
        server._call(lambda: 1, "convert_gain", heavy=True)
    assert server._call(lambda: 2, "inspect_mrc") == 2  # light calls are not limited


def test_no_match_mentions_low_signal(tmp_path):
    tifffile.imwrite(tmp_path / "g.gain", make_gain((64, 64), seed=1))
    _movie(tmp_path / "m.tif", make_gain((64, 64), seed=2), 0, False, 3.0, 5, 3)
    r = check_orientation(tmp_path / "g.gain", tmp_path / "m.tif")
    assert r["confidence"] == "no_match" and "raise n_frames" in r["problem"]
