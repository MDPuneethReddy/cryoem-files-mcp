"""Regression tests for the fixes to the 0.1.0 QA report (docs/QA-REPORT.md): one section per finding ID."""

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
    assert r["next_step"].startswith("convert_gain")


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
