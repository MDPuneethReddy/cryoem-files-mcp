"""Independent regression tests for docs/RELEASE-PLAN.md acceptance criteria (Tester).

Written from the acceptance criteria and docs/RESEARCH-NOTES.md, not from tests/test_fixes.py.
One or more tests per code-testable ID, named test_<ID>_<what>. Known product bugs are marked
xfail(strict=True) with reason "BUG: ..." so the suite stays green while the bug stays visible.

Facts used for the MotionCor3 / RELION simulators below (RESEARCH-NOTES A1-A3):
  * TIFF movie rows are flipped (np.flipud) on load by MotionCor3 and RELION; MRC and EER rows are used as stored.
  * A TIFF gain read by their TIFF reader is flipped too; RELION reverts that flip for a .gain with EER movies.
  * MotionCor: -RotGain k == np.rot90(g, -k), then -FlipGain 1 == flipud / 2 == fliplr, then -InvGain (1/g);
    the frame is multiplied by the resulting gain.
  * RELION with EER movies: multiplies by a '.gain' file, divides by any other gain (MRC included).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

import anyio
import mrcfile
import numpy as np
import pytest
import tifffile

from cryoem_files_mcp import gain as gain_mod
from cryoem_files_mcp import inspect as insp
from cryoem_files_mcp import meta, server
from cryoem_files_mcp.paths import resolve, set_roots

from mcp import Client
from mcp.client.stdio import StdioServerParameters

HAS_FIFO = hasattr(os, "mkfifo")
ALL_VARIANTS = [(k, f) for k in range(4) for f in (False, True)]


# ============================================================================ helpers

def guarded(fn, *a, seconds: float = 5.0, **kw):
    """Run fn in a daemon thread. Returns (value, exception, elapsed). Fails the test if fn blocks."""
    box: dict = {}

    def target():
        try:
            box["v"] = fn(*a, **kw)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    t0 = time.monotonic()
    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(seconds)
    elapsed = time.monotonic() - t0
    assert not th.is_alive(), f"{getattr(fn, '__name__', fn)} still blocked after {seconds} s"
    return box.get("v"), box.get("e"), elapsed


def message_of(fn, *a, **kw) -> str:
    v, e, elapsed = guarded(fn, *a, **kw)
    assert e is not None, f"expected an error, got {v!r}"
    assert elapsed < 2.0, f"took {elapsed:.2f} s"
    return str(e)


def console_cmd() -> list[str]:
    exe = shutil.which("cryoem-files-mcp")
    return [exe] if exe else [sys.executable, "-m", "cryoem_files_mcp.server"]


def stdio_params(root: Path, *extra: str, env: dict | None = None) -> StdioServerParameters:
    cmd = console_cmd()
    return StdioServerParameters(command=cmd[0], args=[*cmd[1:], "--root", str(root), *extra], env=env)


def tif_write(path, data, **kw):
    """Write frames as separate grey pages (never as RGB planes)."""
    tifffile.imwrite(path, data, photometric="minisblack", **kw)


def text_of(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


def write_mrc(path: Path, data: np.ndarray, voxel: float | None = None) -> Path:
    with mrcfile.new(path, overwrite=True) as m:
        m.set_data(np.ascontiguousarray(data))
        if voxel:
            m.voxel_size = voxel
    return path


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    set_roots([str(r)])
    return r


@pytest.fixture
def outside(tmp_path):
    o = tmp_path / "OUTSIDE_SECRET_DIR"
    o.mkdir()
    return o


# ============================================================================ H1: special files never opened

READERS = {
    "identify_file": lambda p: insp.identify_file(resolve(p)),
    "inspect_mrc": lambda p: insp.inspect_mrc(resolve(p)),
    "inspect_movie": lambda p: insp.inspect_movie(resolve(p)),
    "inspect_gain": lambda p: gain_mod.inspect_gain(resolve(p)),
    "check_gain_orientation(gain)": lambda p: gain_mod.check_orientation(resolve(p), resolve("ok_movie.tif")),
    "check_gain_orientation(movie)": lambda p: gain_mod.check_orientation(resolve("ok_gain.mrc"), resolve(p)),
    "convert_gain": lambda p: gain_mod.convert_gain(resolve(p)),
    "summarize_star": lambda p: meta.summarize_star(resolve(p)),
    "parse_mdoc": lambda p: meta.parse_mdoc(resolve(p)),
}


def _ok_pair(root: Path):
    rng = np.random.default_rng(3)
    write_mrc(root / "ok_gain.mrc", (1 + 0.05 * rng.standard_normal((32, 32))).astype(np.float32))
    tif_write(root / "ok_movie.tif", rng.poisson(3, (4, 32, 32)).astype(np.uint16))


@pytest.mark.skipif(not HAS_FIFO, reason="needs mkfifo")
@pytest.mark.parametrize("ext", [".mrc", ".tif", ".gain", ".star", ".mdoc", ".eer"])
@pytest.mark.parametrize("reader", list(READERS))
def test_H1_fifo_rejected_without_blocking(root, reader, ext):
    _ok_pair(root)
    os.mkfifo(root / f"pipe{ext}")
    msg = message_of(READERS[reader], f"pipe{ext}")
    assert "named pipe" in msg and "not a regular file" in msg, msg
    assert str(root) not in msg


@pytest.mark.skipif(not HAS_FIFO, reason="needs mkfifo")
def test_H1_symlink_to_fifo_inside_root_rejected(root):
    os.mkfifo(root / "real_pipe")
    (root / "innocent.mrc").symlink_to(root / "real_pipe")
    for name, fn in READERS.items():
        if "check_gain" in name:
            continue
        assert "named pipe" in message_of(fn, "innocent.mrc")


@pytest.mark.parametrize("reader", list(READERS))
def test_H1_directory_named_like_a_file_rejected(root, reader):
    _ok_pair(root)
    (root / "folder.mrc").mkdir()
    (root / "folder.mrc" / "inside.mrc").write_bytes(b"x" * 2000)
    msg = message_of(READERS[reader], "folder.mrc")
    assert "is a folder" in msg, msg
    assert "damaged" not in msg


def test_H1_dev_null_symlink_inside_root_refused(root):
    _ok_pair(root)
    for dev in ("/dev/null", "/dev/zero", "/dev/urandom"):
        if not os.path.exists(dev):
            continue
        link = root / (Path(dev).name + ".mrc")
        link.symlink_to(dev)
        for fn in READERS.values():
            msg = message_of(fn, link.name)
            assert "outside the allowed folders" in msg or "not a regular file" in msg, msg
            assert dev not in msg  # the link target is not revealed


def test_H1_device_inside_allowed_root_refused_by_type(root):
    """Adversarial: /dev itself is an allowed root, so only the file-type check stands between us and /dev/zero."""
    if not os.path.exists("/dev/zero"):
        pytest.skip("no /dev/zero")
    set_roots([str(root), "/dev"])
    for dev in ("/dev/zero", "/dev/urandom", "/dev/null"):
        if not os.path.exists(dev):
            continue
        for name, fn in READERS.items():
            if "check_gain" in name:
                continue
            msg = message_of(fn, dev)
            assert "character device" in msg and "not a regular file" in msg, (name, msg)


def _bind_socket(path: Path) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    return s


def test_H1_socket_rejected(root):
    _ok_pair(root)
    if len(str(root / "sock.mrc")) > 100:
        pytest.skip("socket path too long for AF_UNIX")
    s = _bind_socket(root / "sock.mrc")
    try:
        for fn in READERS.values():
            assert "socket" in message_of(fn, "sock.mrc")
        r, e, t = guarded(insp.scan_directory, root)
        assert e is None and t < 2.0
        assert r["skipped"]["special_files"] == 1
    finally:
        s.close()


@pytest.mark.skipif(not HAS_FIFO, reason="needs mkfifo")
def test_H1_scan_directory_with_fifos_completes_and_counts(root):
    (root / "Data" / "deep").mkdir(parents=True)
    os.mkfifo(root / "fifo.mrc")
    os.mkfifo(root / "Data" / "movie_001_fractions.tif")  # looks like a small movie: would be opened by the heuristic
    os.mkfifo(root / "Data" / "deep" / "gain.mrc")  # looks like a gain
    (root / "link_to_fifo.mrc").symlink_to(root / "fifo.mrc")
    tif_write(root / "Data" / "real_movie.tif", np.zeros((3, 8, 8), np.uint8))
    r, e, t = guarded(insp.scan_directory, root)
    assert e is None, e
    assert t < 2.0
    assert r["skipped"]["special_files"] == 4
    assert r["files_scanned"] >= 1
    text = " ".join(r["problems"])
    assert "4 special files" in text and "skipped" in text
    assert "gain.mrc" not in r["gain_references_found"] and "Data/deep/gain.mrc" not in r["gain_references_found"]
    r1, e1, _ = guarded(insp.scan_directory, root / "Data" / "deep")
    assert e1 is None and "1 special file " in " ".join(r1["problems"])


@pytest.mark.skipif(not HAS_FIFO, reason="needs mkfifo")
def test_H1_mcp_session_answers_after_fifo_calls(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    os.mkfifo(root / "fifo.mrc")
    (root / "a.star").write_text("data_x\nloop_\n_rlnA #1\n1\n")

    async def go():
        async with Client(stdio_params(root)) as c:
            for tool, args in [("identify_file", {"path": "fifo.mrc"}), ("inspect_mrc", {"path": "fifo.mrc"}),
                               ("inspect_movie", {"path": "fifo.mrc"}), ("inspect_gain", {"path": "fifo.mrc"}),
                               ("check_gain_orientation", {"gain": "fifo.mrc", "movie": "fifo.mrc"}),
                               ("convert_gain", {"gain": "fifo.mrc"}), ("summarize_star", {"path": "fifo.mrc"}),
                               ("parse_mdoc", {"path": "fifo.mrc"})]:
                with anyio.fail_after(2):
                    res = await c.call_tool(tool, args)
                assert res.is_error and "named pipe" in text_of(res), (tool, text_of(res))
            with anyio.fail_after(2):
                res = await c.call_tool("scan_directory", {"path": "."})
            assert not res.is_error and "1 special file" in text_of(res)
            with anyio.fail_after(5):
                res = await c.call_tool("summarize_star", {"path": "a.star"})
            assert not res.is_error

    anyio.run(go)


# ============================================================================ H2 / M3: statistical calibration

def _sensitivity(shape, var, rng, stripes=0.0, stripe_seed=None, hot=0, hot_seed=None, gradient=0.0):
    """Detector sensitivity (counts per unit dose). Optional shared column/row stripes, hot pixels, gradient."""
    s = 1 + var * rng.standard_normal(shape)
    if stripes:
        srng = np.random.default_rng(stripe_seed)
        s = s + stripes * srng.standard_normal((1, shape[1])) + stripes * srng.standard_normal((shape[0], 1))
    if gradient:
        yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
        s = s * (1 + gradient * (xx / shape[1] + yy / shape[0]))
    s = np.clip(s, 0.2, None)
    if hot:
        hrng = np.random.default_rng(hot_seed)
        idx = hrng.choice(s.size, hot, replace=False)
        s.flat[idx] = 8.0  # hot pixels: far more counts
    return s


def _poisson_movie(path: Path, s_file: np.ndarray, dose: float, frames: int, rng, kind: str = "tiff"):
    stack = rng.poisson(dose * s_file, size=(frames, *s_file.shape)).astype(np.uint16)
    if kind == "mrc":
        write_mrc(path, stack)
    else:
        tif_write(path, stack, bigtiff=(kind == "bigtiff"), compression="zlib")


def _unrelated_trials():
    shapes = [(128, 128), (256, 256), (192, 256), (96, 160)]
    variations = [0.01, 0.03, 0.08, 0.25]
    doses = [0.5, 2.0, 6.0]
    out = []
    for i in range(60):
        out.append((i, shapes[i % 4], variations[(i // 4) % 4], doses[i % 3]))
    return out


def test_H2_unrelated_gains_never_high_or_medium(root):
    """60 trials (mixed sizes, variations, doses): movie from gain A, checked against an unrelated gain B."""
    bad = []
    for i, shape, var, dose in _unrelated_trials():
        rng = np.random.default_rng(1000 + i)
        sa = _sensitivity(shape, var, rng)
        sb = _sensitivity(shape, var, rng)
        _poisson_movie(root / "m.tif", sa, dose, 20, rng)
        write_mrc(root / "b.mrc", (1 / sb).astype(np.float32))
        r = gain_mod.check_orientation(root / "b.mrc", root / "m.tif")
        if r["confidence"] in ("high", "medium") or "next_step" in r:
            bad.append((i, shape, var, dose, r["confidence"], r["z_best"]))
        assert "z_best" in r and "pixels_compared" in r
    assert not bad, f"unrelated gain gave a recommendation in {len(bad)}/60 trials: {bad[:5]}"


def test_H2_no_match_result_is_explicit(root):
    rng = np.random.default_rng(7)
    _poisson_movie(root / "m.tif", _sensitivity((256, 256), 0.08, rng), 3.0, 20, rng)
    write_mrc(root / "other_gain.mrc", (1 / _sensitivity((256, 256), 0.08, rng)).astype(np.float32))
    r = gain_mod.check_orientation(root / "other_gain.mrc", root / "m.tif")
    assert r["confidence"] == "no_match"
    assert "next_step" not in r and "best" not in r and "motioncor_flags_for_original_gain" not in r
    assert "does not match" in r["problem"] and "convert_gain" not in r["problem"]
    # significance score is reported and consistent with z = |r| * sqrt(n)
    n = r["pixels_compared"]
    for v in r["all_variants"].values():
        if isinstance(v, dict):
            assert abs(v["z"] - abs(v["r"]) * np.sqrt(n)) <= 0.05 + 0.00005 * np.sqrt(n)


def test_H2_adversarial_shared_stripes_hot_pixels_gradient_no_recommendation(root):
    """Unrelated gains that share strong row/column stripes, the same hot pixels and the same illumination
    gradient (e.g. same detector model): the pixel pattern is unrelated, so no orientation may be recommended."""
    bad = []
    for i in range(12):
        rng = np.random.default_rng(5000 + i)
        shape = (192, 192) if i % 2 else (160, 224)
        common = dict(stripes=0.06, stripe_seed=99, hot=20, hot_seed=98, gradient=0.3)
        sa = _sensitivity(shape, 0.05, rng, **common)
        sb = _sensitivity(shape, 0.05, rng, **common)
        _poisson_movie(root / "m.tif", sa, 3.0, 20, rng)
        write_mrc(root / "b.mrc", (1 / sb).astype(np.float32))
        r = gain_mod.check_orientation(root / "b.mrc", root / "m.tif")
        if r["confidence"] in ("high", "medium") or "next_step" in r:
            bad.append((i, r["confidence"], r["z_best"], r.get("best")))
    assert not bad, bad


@pytest.mark.parametrize("var,size", [(0.01, 1024), (0.02, 512), (0.02, 1024)])
def test_H2_M3_weak_correct_gain_is_high(root, var, size):
    """1-2% pixel variation, 0.5 e/px/frame, 20 frames: the correct (non-trivial) variant at high confidence."""
    rng = np.random.default_rng(int(var * 1000) + size)
    k, flip = 3, True
    s_file = _sensitivity((size, size), var, rng)
    gain_file = _gain_file_for(1 / s_file, k, flip)
    _poisson_movie(root / "m.tif", s_file, 0.5, 20, rng)
    write_mrc(root / "g.mrc", gain_file.astype(np.float32))
    r = gain_mod.check_orientation(root / "g.mrc", root / "m.tif")
    assert r["confidence"] == "high", r
    assert (r["rot90_ccw"], r["flip_y"]) == (k, flip)
    assert r["gain_convention"].startswith("multiply")


def test_M3_falcon_style_2x_gain_vs_counting_movie_is_rot0_high(root):
    """Scaled-down QA case: an 8K-style gain (2x the movie size, 4% variation) against a 4K-style movie."""
    rng = np.random.default_rng(42)
    s_fine = _sensitivity((1024, 1024), 0.04, rng)
    s_movie = gain_mod.bin2(s_fine)
    _poisson_movie(root / "m.tif", s_movie, 1.0, 20, rng)
    tif_write(root / "Falcon.gain", (1 / s_fine).astype(np.float32))
    r = gain_mod.check_orientation(root / "Falcon.gain", root / "m.tif")
    assert r["confidence"] == "high" and r["best"] == "rot0", r
    assert "binned" in r.get("scaling", "")


def test_M3_instructions_use_returned_field_values():
    """Server instructions and prompts speak in the confidence values the tool really returns."""
    returned = {"high", "medium", "low", "no_match"}
    instr = server.mcp.instructions or ""
    for word in returned:
        assert word in instr, word
    assert "confidence" in instr and "next_step" in instr
    words = set(re.findall(r"confidence (\w+)", instr))
    assert words <= returned, words


# ============================================================================ research facts end to end

def _gain_file_for(g_movie: np.ndarray, k: int, flip: bool) -> np.ndarray:
    """Gain as stored in its file such that the tool's transform(file, k, flip) equals g_movie."""
    g = np.flipud(g_movie) if flip else g_movie
    return np.ascontiguousarray(np.rot90(g, -k))


def _read_gain_like_mc(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".mrc":
        with mrcfile.open(path, permissive=True) as m:
            d = np.array(m.data, dtype=np.float64)
        return d[0] if d.ndim == 3 else d
    return np.flipud(tifffile.imread(path).astype(np.float64))  # MotionCor/RELION TIFF reader flips rows


def motioncor3(frame_file_order: np.ndarray, movie_type: str, gain_path: Path, rot: int, flipg: int, inv: bool):
    """Gain-corrected frame as MotionCor3 computes it, or None if the gain does not fit the frame."""
    m = np.flipud(frame_file_order) if movie_type == "tiff" else frame_file_order
    g = np.rot90(_read_gain_like_mc(gain_path), -rot)
    if flipg == 1:
        g = np.flipud(g)
    elif flipg == 2:
        g = np.fliplr(g)
    if inv:
        g = np.where(g != 0, 1 / np.where(g != 0, g, 1), 0)
    if g.shape != m.shape:
        return None
    return m * g


def relion(frame_file_order: np.ndarray, movie_type: str, gain_path: Path):
    """RELION motion correction gain step (no gain rotation/flip given)."""
    m = np.flipud(frame_file_order) if movie_type == "tiff" else frame_file_order
    g = _read_gain_like_mc(gain_path)
    is_dotgain = gain_path.name.endswith(".gain")
    if movie_type == "eer" and is_dotgain and gain_path.suffix.lower() != ".mrc":
        g = np.flipud(g)  # renderEER.h: "Revert Y flip in TIFF reader"
    if g.shape != m.shape:
        return None
    if movie_type == "eer" and not is_dotgain:
        return np.where(g != 0, m / np.where(g != 0, g, 1), 0)
    return m * g


def is_flat(c) -> bool:
    if c is None:
        return False
    v = c[np.isfinite(c) & (c != 0)]
    return v.size > 0.9 * c.size and float(v.std() / v.mean()) < 1e-3


CONVERT_RE = re.compile(r"convert_gain\(rotate=(\d), flip='(none|y|x)'(, invert=true)?(?:, bin=(\d))?, movie_type='(\w+)'\)")


def _run_advised_convert(text: str, gain_path: Path, out_name: str) -> tuple[Path, dict]:
    m = CONVERT_RE.search(text)
    assert m, f"no convert_gain call in advice: {text}"
    rot, flip, inv, b, mt = int(m.group(1)), m.group(2), bool(m.group(3)), int(m.group(4) or 1), m.group(5)
    res = gain_mod.convert_gain(gain_path, out=out_name, rotate=rot, flip=flip, invert=inv, bin=b, movie_type=mt)
    return Path(res["output"]), res


def _mc_flags(text: str) -> tuple[int, int, bool]:
    m = re.search(r"-RotGain (\d) -FlipGain (\d)", text)
    assert m, text
    inv = "-InvGain" in text and "no -InvGain" not in text
    return int(m.group(1)), int(m.group(2)), inv


GAIN_KINDS = {
    # name: (file name, convention, writer)
    "mrc_mult": ("gain.mrc", "multiply"),
    "mrc_div": ("gain.mrc", "divide"),
    "tif_mult": ("gain_ref.tif", "multiply"),
    "dotgain_mult": ("Falcon.gain", "multiply"),
}
CASES = [("tiff", "mrc_mult"), ("tiff", "mrc_div"), ("tiff", "tif_mult"), ("eer", "dotgain_mult"),
         ("eer", "mrc_div"), ("eer", "mrc_mult"), ("mrc", "mrc_mult"), ("mrc", "dotgain_mult")]


def _setup_case(root: Path, monkeypatch, movie_type: str, gain_kind: str, k: int, flip: bool, shape=(128, 128)):
    rng = np.random.default_rng(zlib.crc32(repr((movie_type, gain_kind, k, flip, shape)).encode()))
    s_file = _sensitivity(shape, 0.08, rng)  # detector sensitivity in movie file-array order
    fname, conv = GAIN_KINDS[gain_kind]
    g_movie = 1 / s_file if conv == "multiply" else s_file
    g_file = _gain_file_for(g_movie, k, flip).astype(np.float32)
    gpath = root / fname
    if gpath.suffix == ".mrc":
        write_mrc(gpath, g_file)
    else:
        tif_write(gpath, g_file)
    if movie_type == "tiff":
        mpath = root / "movie.tif"
        _poisson_movie(mpath, s_file, 3.0, 20, rng, "tiff")
    elif movie_type == "mrc":
        mpath = root / "movie.mrc"
        _poisson_movie(mpath, s_file, 3.0, 20, rng, "mrc")
    else:
        # No EER writer exists: a BigTIFF holds the same frames in file order (what an EER decoder yields),
        # and the tool is told that BigTIFF movies are EER.
        mpath = root / "movie.tiff"
        _poisson_movie(mpath, s_file, 3.0, 20, rng, "bigtiff")
        monkeypatch.setitem(gain_mod.MOVIE_TYPES, "bigtiff", "eer")
    expected_frame = 3.0 * s_file  # noise-free counts in file order
    return gpath, mpath, expected_frame, conv


def _mc_transform(a, rot, f):
    t = np.rot90(a, -rot)
    return np.flipud(t) if f == 1 else (np.fliplr(t) if f == 2 else t)


def _wrong_mc_flags(shape, good=(0, 0)):
    """All (rot, flip) MotionCor flag pairs whose transform differs from the advised one's for this shape."""
    probe = np.arange(shape[0] * shape[1], dtype=float).reshape(shape)
    ref = _mc_transform(probe, *good)
    out = []
    for rot in range(4):
        for f in (0, 1, 2):
            t = _mc_transform(probe, rot, f)
            if t.shape != ref.shape or not np.array_equal(t, ref):
                out.append((rot, f))
    return out


@pytest.mark.parametrize("k,flip", ALL_VARIANTS)
@pytest.mark.parametrize("movie_type,gain_kind", CASES)
def test_RESEARCH_motioncor_advice_gives_flat_correction(root, monkeypatch, movie_type, gain_kind, k, flip):
    gpath, mpath, frame, conv = _setup_case(root, monkeypatch, movie_type, gain_kind, k, flip)
    r = gain_mod.check_orientation(gpath, mpath)
    assert r["confidence"] == "high", r
    assert (r["rot90_ccw"], r["flip_y"]) == (k, flip)
    assert r["movie_type"] == movie_type
    assert r["gain_convention"].startswith("divide" if conv == "divide" else "multiply")
    advice = r["next_step"]["motioncor"]
    out, res = _run_advised_convert(advice, gpath, "for_motioncor.mrc")
    rot, fl, inv = _mc_flags(advice)
    assert is_flat(motioncor3(frame, movie_type, out, rot, fl, inv)), (advice, res["applied"])
    # convert_gain's own usage line must agree with next_step
    assert (rot, fl, inv) == _mc_flags(res["use_with_motioncor"])
    # every other MotionCor orientation, and a wrong -InvGain, leave a fixed pattern
    for wr, wf in _wrong_mc_flags(frame.shape):
        assert not is_flat(motioncor3(frame, movie_type, out, wr, wf, inv)), (wr, wf)
    assert not is_flat(motioncor3(frame, movie_type, out, rot, fl, not inv))


@pytest.mark.parametrize("k,flip", ALL_VARIANTS)
@pytest.mark.parametrize("movie_type", ["tiff", "mrc", "eer"])
@pytest.mark.parametrize("conv_kind", ["mrc_mult", "mrc_div"])
def test_RESEARCH_motioncor_flags_for_original_mrc_gain(root, monkeypatch, movie_type, conv_kind, k, flip):
    gpath, mpath, frame, conv = _setup_case(root, monkeypatch, movie_type, conv_kind, k, flip)
    r = gain_mod.check_orientation(gpath, mpath)
    assert r["confidence"] == "high"
    flags = r["motioncor_flags_for_original_gain"]
    rot, fl, inv = _mc_flags(flags)
    assert is_flat(motioncor3(frame, movie_type, gpath, rot, fl, inv)), flags
    stored_shape = _read_gain_like_mc(gpath).shape
    for wr, wf in _wrong_mc_flags(stored_shape, (rot, fl)):
        assert not is_flat(motioncor3(frame, movie_type, gpath, wr, wf, inv)), (wr, wf, flags)


@pytest.mark.parametrize("k,flip", ALL_VARIANTS)
@pytest.mark.parametrize("movie_type,gain_kind", CASES)
def test_RESEARCH_relion_advice_gives_flat_correction(root, monkeypatch, movie_type, gain_kind, k, flip):
    """RELION rule: EER + '.gain' multiplies, EER + any other gain divides; TIFF/MRC movies multiply."""
    gpath, mpath, frame, conv = _setup_case(root, monkeypatch, movie_type, gain_kind, k, flip)
    r = gain_mod.check_orientation(gpath, mpath)
    assert r["confidence"] == "high"
    advice = r["next_step"]["relion"]
    if "original .gain" in advice:
        assert gpath.name.endswith(".gain") and movie_type == "eer"
        used = gpath
    else:
        used, _ = _run_advised_convert(advice, gpath, "for_relion.mrc")
    assert is_flat(relion(frame, movie_type, used)), advice
    if movie_type == "eer" and used.suffix == ".mrc":
        # the MotionCor file (multiplicative MRC) would be inverted again by RELION: must differ and not be flat
        mc_out, _ = _run_advised_convert(r["next_step"]["motioncor"], gpath, "for_mc.mrc")
        assert not is_flat(relion(frame, movie_type, mc_out))


@pytest.mark.parametrize("k,flip", ALL_VARIANTS)
def test_RESEARCH_rectangular_tiff_movie_all_orientations(root, monkeypatch, k, flip):
    """K3-like non-square frames: a 90-degree variant changes the gain's shape; advice must still be flat."""
    gpath, mpath, frame, _ = _setup_case(root, monkeypatch, "tiff", "mrc_mult", k, flip, shape=(96, 144))
    r = gain_mod.check_orientation(gpath, mpath)
    assert r["confidence"] == "high" and (r["rot90_ccw"] % 2, r["flip_y"]) in {(k % 2, flip), (k % 2, not flip)}
    out, _ = _run_advised_convert(r["next_step"]["motioncor"], gpath, "o.mrc")
    assert is_flat(motioncor3(frame, "tiff", out, *_mc_flags(r["next_step"]["motioncor"])))
    rot, fl, inv = _mc_flags(r["motioncor_flags_for_original_gain"])
    assert is_flat(motioncor3(frame, "tiff", gpath, rot, fl, inv))


def test_RESEARCH_converting_twice_is_not_flat_for_tiff(root, monkeypatch):
    """Guard against double-flip: converting the already converted gain again with the same advice breaks it."""
    gpath, mpath, frame, _ = _setup_case(root, monkeypatch, "tiff", "mrc_mult", 0, False)
    r = gain_mod.check_orientation(gpath, mpath)
    out, res = _run_advised_convert(r["next_step"]["motioncor"], gpath, "once.mrc")
    out2, _ = _run_advised_convert(r["next_step"]["motioncor"], out, "twice.mrc")
    assert is_flat(motioncor3(frame, "tiff", out, 0, 0, False))
    assert not is_flat(motioncor3(frame, "tiff", out2, 0, 0, False))
    # the tool tells the user that re-checking the converted file against the TIFF movie reports rot0+flip_y
    r2 = gain_mod.check_orientation(out, mpath)
    assert r2["best"] == "rot0+flip_y" and "rot0+flip_y" in res["row_order"]


def test_RESEARCH_super_resolution_bin_advice_flat(root):
    """Gain 2x the movie: advice mentions bin=2; converting with bin=2 and the advised flags gives a flat image."""
    rng = np.random.default_rng(11)
    s_fine = _sensitivity((256, 256), 0.08, rng)
    s_movie = gain_mod.bin2(s_fine)
    k, flip = 1, False
    write_mrc(root / "g.mrc", _gain_file_for(1 / s_fine, k, flip).astype(np.float32))
    _poisson_movie(root / "m.tif", s_movie, 3.0, 20, rng)
    r = gain_mod.check_orientation(root / "g.mrc", root / "m.tif")
    assert r["confidence"] == "high" and (r["rot90_ccw"], r["flip_y"]) == (k, flip)
    assert "bin=2" in r["next_step"].get("size", "")
    m = CONVERT_RE.search(r["next_step"]["motioncor"])
    res = gain_mod.convert_gain(root / "g.mrc", out="b.mrc", rotate=int(m.group(1)), flip=m.group(2),
                                invert=bool(m.group(3)), bin=2, movie_type="tiff")
    c = motioncor3(3.0 * s_movie, "tiff", Path(res["output"]), 0, 0, False)
    # binning a multiplicative gain (mean of 1/s) is not exactly 1/mean(s): flat to ~ variance level only
    v = c / c.mean()
    assert v.std() < 0.01


# ============================================================================ PM-4: verify on one movie first

def test_PM4_next_step_says_check_one_movie_first(root, monkeypatch):
    gpath, mpath, _, _ = _setup_case(root, monkeypatch, "tiff", "mrc_mult", 2, True)
    r = gain_mod.check_orientation(gpath, mpath)
    assert "one movie" in r["next_step"]["check_first"]
    res = gain_mod.convert_gain(gpath, out="x.mrc", rotate=2, flip="y", movie_type="tiff")
    assert "one movie" in res["check_first"]


# ============================================================================ M2: bin before invert

@pytest.mark.parametrize("shape", [(8, 8), (7, 9), (6, 10)])
@pytest.mark.parametrize("k,flip", [(0, "none"), (1, "y"), (3, "x")])
@pytest.mark.parametrize("movie_type", [None, "tiff", "eer"])
def test_M2_invert_after_bin_with_defects(root, shape, k, flip, movie_type):
    rng = np.random.default_rng(1)
    g = (1 + 0.1 * rng.standard_normal(shape)).astype(np.float32)
    g[1, 2] = 0  # one defect pixel
    g[4:6, 4:6] = 0  # a whole 2x2 block dead (in file coordinates)
    write_mrc(root / "g.mrc", g)
    res = gain_mod.convert_gain(root / "g.mrc", out="o.mrc", invert=True, bin=2, rotate=k, flip=flip,
                                movie_type=movie_type)
    t = np.rot90(g.astype(np.float64), k)
    t = np.flipud(t) if flip == "y" else (np.fliplr(t) if flip == "x" else t)
    b = gain_mod.bin2(t)
    exp = np.where(b != 0, 1 / np.where(b != 0, b, 1), 0)
    if movie_type == "tiff":
        exp = np.flipud(exp)
    with mrcfile.open(res["output"]) as m:
        got = np.array(m.data)
    assert got.dtype == np.float32 and got.shape == exp.shape
    assert np.isfinite(got).all()
    np.testing.assert_allclose(got, exp, rtol=1e-6, atol=1e-7)
    a = res["applied"]
    bi = next(i for i, s in enumerate(a) if s.startswith("binned"))
    ii = next(i for i, s in enumerate(a) if s.startswith("inverted"))
    assert bi < ii
    if k:
        assert a[0].startswith("rotated")
    if movie_type == "tiff":
        assert "TIFF" in a[-1]


def test_M2_defect_error_is_about_one_percent(root):
    """QA's simulation: divisive 2x gain with one defect; binned counts corrected with the tool's output."""
    rng = np.random.default_rng(0)
    g = (1 + 0.05 * rng.standard_normal((64, 64))).astype(np.float64)
    g[10, 10] = 0.02  # near-dead defect pixel in a divisive gain
    write_mrc(root / "div.mrc", g.astype(np.float32))
    counts = gain_mod.bin2(1000 * g) * 4  # 2x2-summed raw counts (proportional to the divisive gain)
    res = gain_mod.convert_gain(root / "div.mrc", out="mult.mrc", invert=True, bin=2)
    with mrcfile.open(res["output"]) as m:
        corrected = counts * np.array(m.data, dtype=np.float64)
    err = abs(corrected[5, 5] / np.median(corrected) - 1)
    assert err < 0.02, err


# ============================================================================ M1 / L4: symlinks to outside

SECRET = b"root:x:0:0:SECRET-CONTENT-DO-NOT-LEAK\n" * 300  # starts with 72 6f 6f 74
SECRET_SIZES = (str(len(SECRET)), insp._size(len(SECRET)))


def _leak_setup(root: Path, outside: Path):
    (outside / "passwd_like").write_bytes(SECRET)
    (outside / "sub").mkdir()
    (outside / "sub" / "movie_frames.mrc").write_bytes(SECRET)
    (root / "leak.mrc").symlink_to(outside / "passwd_like")
    (root / "leak_frames.tif").symlink_to(outside / "passwd_like")
    (root / "hop1.mrc").symlink_to(root / "hop2.mrc")  # inside -> inside -> outside
    (root / "hop2.mrc").symlink_to(outside / "passwd_like")
    (root / "dangling_out.mrc").symlink_to(outside / "does_not_exist_secret_name.mrc")
    (root / "outdir").symlink_to(outside / "sub")
    write_mrc(root / "inside_target.mrc", np.zeros((4, 4), np.float32))
    (root / "good_link.mrc").symlink_to(root / "inside_target.mrc")


def _assert_no_leak(text: str, outside: Path):
    assert "72 6f 6f 74" not in text and "root:x" not in text and "SECRET" not in text
    assert str(outside) not in text and "OUTSIDE_SECRET_DIR" not in text and "passwd_like" not in text
    assert "does_not_exist_secret_name" not in text
    assert all(x not in text for x in SECRET_SIZES)


def test_M1_scan_does_not_read_or_reveal_outside_targets(root, outside):
    _leak_setup(root, outside)
    r = insp.scan_directory(root)
    text = json.dumps(r)
    _assert_no_leak(text, outside)
    assert r["skipped"]["outside_symlinks"] == 6  # leak.mrc, leak_frames.tif, hop1, hop2, dangling_out, outdir
    assert any("symlink" in n and "outside the allowed folders" in n for n in r.get("notes", []))


def test_M1_inside_symlink_still_scanned(root, outside):
    _leak_setup(root, outside)
    r = insp.scan_directory(root)
    assert r["by_extension"][".mrc"]["count"] == 2  # inside_target.mrc + good_link.mrc
    assert "damaged" not in json.dumps(r)


def test_M1_outside_symlinked_folder_not_entered(root, outside):
    (outside / "sub").mkdir()
    for i in range(3):
        (outside / "sub" / f"movie_{i}.tif").write_bytes(SECRET)
    (root / "mounted").symlink_to(outside / "sub")
    r = insp.scan_directory(root)
    _assert_no_leak(json.dumps(r), outside)
    assert r["likely_movies"] == 0 and r["skipped"]["outside_symlinks"] == 1


@pytest.mark.parametrize("name", ["leak.mrc", "hop1.mrc", "dangling_out.mrc", "outdir/movie_frames.mrc"])
def test_M1_L4_readers_refuse_outside_links_without_revealing_target(root, outside, name):
    _leak_setup(root, outside)
    for reader, fn in READERS.items():
        if "check_gain" in reader:
            continue
        msg = message_of(fn, name)
        assert "outside the allowed folders" in msg, (reader, msg)
        _assert_no_leak(msg, outside)


def test_M1_convert_out_cannot_write_through_outside_link(root, outside):
    _leak_setup(root, outside)
    write_mrc(root / "g.mrc", (1 + 0.1 * np.random.default_rng(0).standard_normal((8, 8))).astype(np.float32))
    for out in ("leak.mrc", "dangling_out.mrc", "outdir/x.mrc", "outdir"):
        msg = message_of(gain_mod.convert_gain, resolve("g.mrc"), out=out, overwrite=True)
        assert "outside the allowed folders" in msg
        _assert_no_leak(msg, outside)
    assert (outside / "passwd_like").read_bytes() == SECRET
    assert not (outside / "does_not_exist_secret_name.mrc").exists()
    assert sorted(p.name for p in (outside / "sub").iterdir()) == ["movie_frames.mrc"]


def test_M1_over_mcp_no_leak(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "OUTSIDE_SECRET_DIR"
    root.mkdir()
    outside.mkdir()
    _leak_setup(root, outside)

    async def go():
        async with Client(stdio_params(root)) as c:
            texts = [text_of(await c.call_tool("scan_directory", {"path": "."}))]
            for tool in ("identify_file", "inspect_mrc", "inspect_gain"):
                res = await c.call_tool(tool, {"path": "leak.mrc"})
                assert res.is_error
                texts.append(text_of(res))
            return texts

    for t in anyio.run(go):
        _assert_no_leak(t, outside)


def test_L4_missing_file_error_relative_with_cause_and_fix(root):
    (root / "sub").mkdir()
    msg = message_of(READERS["identify_file"], "sub/missing_movie.tif")
    assert "sub/missing_movie.tif" in msg and "does not exist" in msg
    assert "Likely cause:" in msg and "Fix:" in msg
    assert str(root / "sub") not in msg


# ============================================================================ M4: depth

def _nest(root: Path, depth_levels: int, name: str = "hidden_movie.tif") -> Path:
    """File inside folder level `depth_levels` (root = level 1)."""
    d = root
    for i in range(depth_levels - 1):
        d = d / f"L{i + 2}"
    d.mkdir(parents=True, exist_ok=True)
    tif_write(d / name, np.zeros((3, 8, 8), np.uint8))
    return d / name


def test_M4_default_depth_finds_qa_file(root):
    p = root / "a/b/c/d/e/f"
    p.mkdir(parents=True)
    tif_write(p / "hidden_movie.tif", np.zeros((3, 8, 8), np.uint8))
    r = insp.scan_directory(root)
    assert r["files_scanned"] == 1 and r["likely_movies"] == 1
    assert not any("empty" in x for x in r["problems"])


def test_M4_default_depth_is_at_least_8_and_boundary(root):
    _nest(root, 8, "at_level_8.tif")
    _nest(root, 9, "at_level_9.tif")
    r = insp.scan_directory(root)
    assert r["files_scanned"] == 1
    assert r["skipped"]["deeper_folders"] == 1
    assert any("deeper than max_depth=8" in x for x in r["problems"])


@pytest.mark.parametrize("max_depth", [1, 2, 3, 5])
def test_M4_max_depth_boundary_and_report(root, max_depth):
    for lvl in range(1, 7):
        _nest(root, lvl, f"f{lvl}.tif")
    (root / "L2" / "side").mkdir()  # a second folder at level 3
    r = insp.scan_directory(root, max_depth=max_depth)
    assert r["files_scanned"] == max_depth
    deeper = 1 + (1 if max_depth == 2 else 0)
    assert r["skipped"]["deeper_folders"] == deeper
    msg = " ".join(r["problems"])
    assert f"{deeper} folder" in msg and "not scanned" in msg and "empty" not in msg


def test_M4_only_deeper_files_not_reported_as_empty(root):
    _nest(root, 4)
    r = insp.scan_directory(root, max_depth=2)
    assert r["files_scanned"] == 0
    msg = " ".join(r["problems"])
    assert "deeper folder" in msg and "not scanned" in msg and "folder is empty" not in msg


def test_M4_max_depth_in_tool_schema():
    tools = {t.name: t for t in anyio.run(server.mcp.list_tools)}
    props = tools["scan_directory"].input_schema["properties"] if hasattr(tools["scan_directory"], "input_schema") \
        else tools["scan_directory"].inputSchema["properties"]
    assert "max_depth" in props and props["max_depth"]["default"] >= 8 and props["max_depth"].get("minimum") == 1


# ============================================================================ M5: header-check cap

def _tiny_mrc(path: Path, truncated: bool = False):
    write_mrc(path, np.zeros((4, 16, 16), np.float32))
    if truncated:
        with open(path, "r+b") as f:
            f.truncate(1024 + 100)


def test_M5_cap_reported_with_exact_counts(root, monkeypatch):
    monkeypatch.setattr(insp, "MRC_HEADER_CHECKS", 5)
    for i in range(12):
        _tiny_mrc(root / f"img_{i:03d}.mrc", truncated=(i == 8))
    r = insp.scan_directory(root)
    text = json.dumps(r)
    assert r["mrc_headers_checked"] == "5 of 12"
    assert "header-checked 5 of 12 MRC files" in text


def test_M5_real_default_cap_501_files(root):
    for i in range(501):
        _tiny_mrc(root / f"m{i:04d}.mrc", truncated=(i == 300))
    r = insp.scan_directory(root)
    text = json.dumps(r)
    damaged_reported = "damaged MRC file" in text
    capped = "header-checked 500 of 501 MRC files" in text
    assert capped and (damaged_reported or "possibly truncated" in text)


@pytest.mark.parametrize("n", [5, 4])
def test_M5_no_cap_note_when_all_checked(root, monkeypatch, n):
    monkeypatch.setattr(insp, "MRC_HEADER_CHECKS", 5)
    for i in range(n):
        _tiny_mrc(root / f"x{i}.mrc", truncated=(i == n - 1))
    r = insp.scan_directory(root)
    assert "header-checked" not in json.dumps(r)
    assert r["mrc_headers_checked"] == f"{n} of {n}"
    assert any("damaged MRC file" in p for p in r["problems"])


# ============================================================================ M6: STAR quoting

def _star(rows: list[str], cols=("rlnMicrographName", "rlnDefocusU")) -> str:
    head = "\n# version 30001\n\ndata_micrographs\n\nloop_\n" + "".join(f"_{c} #{i + 1}\n" for i, c in enumerate(cols))
    return head + "\n".join(rows) + "\n"


@pytest.mark.parametrize("row", [
    "'my mic 1.mrc' 12000",
    '"my mic 1.mrc" 12000',
    '"" 12000',
    "''  12000",
    "plain.mrc 12000 # trailing comment with three words",
    "plain.mrc 12000 #comment",
    '"mic #1.mrc" 12000',
    "'mic # 1.mrc' 12000 # and a comment",
    "it's.mrc 12000",
    "\t'tab\tsep.mrc'\t12000\t",
])
def test_M6_legal_star_rows_no_problem(root, row):
    (root / "s.star").write_text(_star([row, "other.mrc 13000"]))
    r = meta.summarize_star(root / "s.star")
    assert "problems" not in r, r.get("problems")
    assert r["blocks"]["micrographs"]["rows"] == 2


def test_M6_hash_inside_unquoted_value(root):
    (root / "s.star").write_text(_star(["Movies/grid#2_001.mrc 12000", "other.mrc 13000"]))
    r = meta.summarize_star(root / "s.star")
    assert "problems" not in r and r["blocks"]["micrographs"]["rows"] == 2


def test_M6_value_starting_with_hash_inside_quotes_counts(root):
    (root / "s.star").write_text(_star(['"#notacomment" 12000 ""'], cols=("rlnA", "rlnB", "rlnC")))
    assert "problems" not in meta.summarize_star(root / "s.star")


@pytest.mark.parametrize("row,n", [("a.mrc 1 2", 3), ("'my mic 1.mrc' 1 2", 3), ('"x y" 1 2 # c', 3),
                                   ("onlyone.mrc", 1), ('"a b c"', 1)])
def test_M6_real_mismatch_reported_with_line_number(root, row, n):
    rows = ["ok1.mrc 1", "ok2.mrc 2", row, "ok3.mrc 3"]
    (root / "s.star").write_text(_star(rows))
    text = _star(rows)
    line_no = text.splitlines().index(row) + 1
    try:
        r = meta.summarize_star(root / "s.star")
        msg = " ".join(r.get("problems", []))
    except ValueError as e:
        msg = str(e)
    assert f"line {line_no}: {n} values but 2 columns" in msg, msg


# ============================================================================ M7: role guess

@pytest.mark.parametrize("name,shape,role", [
    ("map.mrc", (64, 64, 64), "3D map"),
    ("one.mrc", (1, 256, 256), "2D image"),
    ("parts.mrcs", (100, 64, 64), "particle stack"),
    ("stack.mrc", (40, 128, 128), "movie"),
])
def test_M7_role_guess_real_files(root, name, shape, role):
    write_mrc(root / name, np.zeros(shape, np.int8))
    r = insp.identify_file(root / name)
    assert role in r["role_guess"], r["role_guess"]
    if role == "2D image":
        assert "movie" not in r["role_guess"]


def test_M7_full_size_movie_shape():
    assert "movie" in insp._mrc_role("frames.mrc", (40, 4096, 4096))
    assert "movie" in insp._mrc_role("frames.mrc", (40, 4092, 5760))
    assert "3D map" in insp._mrc_role("big.map", (256, 256, 256))
    assert "particle" not in insp._mrc_role("vol.mrc", (64, 64, 64))


def test_M7_mrcs_cube_is_particle_stack(root):
    write_mrc(root / "parts.mrcs", np.zeros((64, 64, 64), np.int8))
    assert "particle stack" in insp.identify_file(root / "parts.mrcs")["role_guess"]


# ============================================================================ M8 / PM-3: startup refusal

def _start(args: list[str], cwd: str | None = None, env_extra: dict | None = None, timeout=10):
    env = {k: v for k, v in os.environ.items() if k != "CRYOEM_MCP_ROOTS"}
    env.update(env_extra or {})
    t0 = time.monotonic()
    p = subprocess.run(console_cmd() + args, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
                       stdin=subprocess.DEVNULL)
    return p, time.monotonic() - t0


@pytest.mark.parametrize("cwd", ["/", "~", "tmp"])
def test_PM3_no_root_refuses_from_any_cwd(tmp_path, cwd):
    d = {"/": "/", "~": os.path.expanduser("~"), "tmp": str(tmp_path)}[cwd]
    p, t = _start([], cwd=d)
    assert p.returncode != 0 and "--root" in p.stderr
    assert t < 5


@pytest.mark.parametrize("env_value", ["", os.pathsep, os.pathsep * 3])
def test_PM3_empty_env_roots_refused(tmp_path, env_value):
    p, _ = _start([], cwd="/", env_extra={"CRYOEM_MCP_ROOTS": env_value})
    assert p.returncode != 0 and "--root" in p.stderr


def test_M8_nonexistent_root_refused_fast(tmp_path):
    p, t = _start(["--root", str(tmp_path / "nonexistent")])
    assert p.returncode != 0 and t < 5
    assert "does not exist" in p.stderr and "nonexistent" in p.stderr


def test_M8_file_as_root_refused(tmp_path):
    (tmp_path / "some_file.txt").write_text("x")
    p, _ = _start(["--root", str(tmp_path / "some_file.txt")])
    assert p.returncode != 0 and "not a folder" in p.stderr


def test_M8_env_roots_validated_too(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    for bad, words in ((tmp_path / "nope", "does not exist"), (tmp_path / "f.txt", "not a folder")):
        p, _ = _start([], env_extra={"CRYOEM_MCP_ROOTS": str(bad)})
        assert p.returncode != 0 and words in p.stderr and "CRYOEM_MCP_ROOTS" in p.stderr


def test_M8_one_bad_root_among_good_refused(tmp_path):
    p, _ = _start(["--root", str(tmp_path), "--root", str(tmp_path / "missing")])
    assert p.returncode != 0 and "missing" in p.stderr
    p, _ = _start([], env_extra={"CRYOEM_MCP_ROOTS": f"{tmp_path}{os.pathsep}{tmp_path / 'missing'}"})
    assert p.returncode != 0 and "missing" in p.stderr


@pytest.mark.parametrize("value", ["0", "-1"])
def test_M8_bad_timeout_refused(tmp_path, value):
    p, _ = _start(["--root", str(tmp_path), "--timeout", value])
    assert p.returncode != 0 and "--timeout" in p.stderr


def test_M8_valid_root_starts_and_serves(tmp_path):
    for args, env in (([ "--root", str(tmp_path)], None), ([], {"CRYOEM_MCP_ROOTS": str(tmp_path)})):
        cmd = console_cmd()

        async def go():
            params = StdioServerParameters(command=cmd[0], args=[*cmd[1:], *args], env=env)
            async with Client(params) as c:
                with anyio.fail_after(10):
                    res = await c.read_resource("cryoem://config")
                return str(res)

        assert str(tmp_path) in anyio.run(go)


def test_M8_explicit_root_slash_behaviour(tmp_path):
    """--root / is explicit (PM-3 only forbids it as an implicit default). It must at least not crash at start."""
    proc = subprocess.Popen(console_cmd() + ["--root", "/"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    try:
        time.sleep(1.0)
        alive = proc.poll() is None
        if not alive:
            err = proc.stderr.read().decode()
            assert proc.returncode != 0 and "/" in err  # a refusal is acceptable too, if it explains itself
    finally:
        proc.kill()
        proc.wait()


# ============================================================================ PM-5: internal error format

@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyError("frame_index"), MemoryError(),
                                 RuntimeError("line one\nTraceback (most recent call last):\n  File \"x.py\"")])
def test_PM5_unexpected_exception_reported_cleanly(root, monkeypatch, exc):
    write_mrc(root / "g.mrc", np.ones((8, 8), np.float32))

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(gain_mod, "inspect_gain", boom)

    async def go():
        async with Client(server.mcp) as c:
            res = await c.call_tool("inspect_gain", {"path": "g.mrc"})
            ok = await c.call_tool("identify_file", {"path": "g.mrc"})
            return res, ok

    res, ok = anyio.run(go)
    msg = text_of(res)
    assert res.is_error
    assert type(exc).__name__ in msg and "internal error in inspect_gain" in msg
    assert "please report" in msg.lower() and "/issues" in msg
    assert "\n" not in msg and "server.py" not in msg and "gain.py" not in msg  # no traceback frames
    if "Traceback" not in str(exc):  # (otherwise it is the exception's own text, flattened to one line)
        assert "Traceback" not in msg
    assert not ok.is_error  # the session still works


def test_PM5_expected_errors_are_not_internal(root):
    async def go():
        async with Client(server.mcp) as c:
            return await c.call_tool("identify_file", {"path": "nope.mrc"})

    res = anyio.run(go)
    assert res.is_error and "internal error" not in text_of(res) and "does not exist" in text_of(res)


# ============================================================================ timeout

def test_TIMEOUT_blocking_tool_returns_error_promptly(root, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(server, "TIMEOUT_S", 0.5)

    def stuck(*a, **k):
        release.wait(20)
        return {}

    monkeypatch.setattr(insp, "identify_file", stuck)
    (root / "a.star").write_text("data_x\nloop_\n_rlnA #1\n1\n")

    async def go():
        async with Client(server.mcp) as c:
            t0 = time.monotonic()
            with anyio.fail_after(5):
                res = await c.call_tool("identify_file", {"path": "a.star"})
            dt = time.monotonic() - t0
            with anyio.fail_after(5):
                nxt = await c.call_tool("summarize_star", {"path": "a.star"})
            return res, dt, nxt

    try:
        res, dt, nxt = anyio.run(go)
    finally:
        release.set()
    assert res.is_error and dt < 2.0
    msg = text_of(res)
    assert "did not finish within 0.5 s" in msg and "CRYOEM_MCP_TIMEOUT" in msg
    assert not nxt.is_error


def test_TIMEOUT_cli_flag_over_stdio(tmp_path):
    """A real --timeout on the console script: a call that needs longer than the limit errors promptly."""
    rng = np.random.default_rng(0)
    tif_write(tmp_path / "m.tif", rng.poisson(2, (40, 1024, 1024)).astype(np.uint16), compression="zlib")
    write_mrc(tmp_path / "g.mrc", (1 + 0.05 * rng.standard_normal((1024, 1024))).astype(np.float32))

    async def go():
        async with Client(stdio_params(tmp_path, "--timeout", "0.05")) as c:
            t0 = time.monotonic()
            with anyio.fail_after(10):
                res = await c.call_tool("check_gain_orientation", {"gain": "g.mrc", "movie": "m.tif", "n_frames": 40})
            dt = time.monotonic() - t0
            ok = await c.call_tool("identify_file", {"path": "g.mrc"})
            return res, dt, ok

    res, dt, ok = anyio.run(go)
    assert res.is_error and "did not finish within 0.05 s" in text_of(res)
    assert dt < 3.0 and not ok.is_error


def test_TIMEOUT_env_variable_respected(tmp_path):
    code = ("import os; os.environ['CRYOEM_MCP_TIMEOUT']='7.5'; "
            "import cryoem_files_mcp.server as s; print(s.TIMEOUT_S)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).stdout
    assert out.strip() == "7.5"


# ============================================================================ L1: titles and annotations

def test_L1_every_tool_has_title_over_stdio(tmp_path):
    async def go():
        async with Client(stdio_params(tmp_path)) as c:
            return (await c.list_tools()).tools

    tools = anyio.run(go)
    assert len(tools) == 9
    for t in tools:
        assert t.title and t.title.strip() and t.title != t.name, t.name
        ann = t.annotations
        assert ann is not None and ann.open_world_hint is False
        if t.name == "convert_gain":
            assert ann.read_only_hint is False and ann.destructive_hint is False
        else:
            assert ann.read_only_hint is True


# ============================================================================ L2: identify_file wording

def test_L2_identify_folder_is_a_folder_error(root):
    (root / "session").mkdir()
    msg = message_of(READERS["identify_file"], "session")
    assert "is a folder" in msg and "damaged" not in msg


@pytest.mark.parametrize("name,content", [("notes.txt", b"hello world\n"), ("blob.bin", bytes(range(256)) * 8),
                                          ("noext", b"\x00\x01\x02" * 400)])
def test_L2_unrecognised_never_header_ok(root, name, content):
    (root / name).write_bytes(content)
    r = insp.identify_file(root / name)
    assert "header ok" not in r["integrity"] and r["integrity"] != "ok"
    assert "unrecognised" in r["integrity"] or "unknown" in r["integrity"]
    r2 = insp.identify_file(root / name, verify_data=True)
    assert r2["integrity"] != "ok"


# ============================================================================ L3: argument validation over MCP

BAD_ARGS = [
    ("scan_directory", {"path": ".", "limit": -5}, "limit"),
    ("scan_directory", {"path": ".", "limit": 0}, "limit"),
    ("scan_directory", {"path": ".", "limit": 1_000_001}, "limit"),
    ("scan_directory", {"path": ".", "max_depth": 0}, "max_depth"),
    ("scan_directory", {"path": ".", "max_depth": 65}, "max_depth"),
    ("scan_directory", {"path": ".", "limit": "lots"}, "limit"),
    ("inspect_movie", {"path": "m.tif", "sample_frames": 0}, "sample_frames"),
    ("inspect_movie", {"path": "m.tif", "sample_frames": -1}, "sample_frames"),
    ("check_gain_orientation", {"gain": "g.mrc", "movie": "m.tif", "n_frames": 0}, "n_frames"),
    ("check_gain_orientation", {"gain": "g.mrc", "movie": "m.tif", "n_frames": 10_001}, "n_frames"),
    ("convert_gain", {"gain": "g.mrc", "rotate": 4}, "rotate"),
    ("convert_gain", {"gain": "g.mrc", "rotate": -1}, "rotate"),
    ("convert_gain", {"gain": "g.mrc", "bin": 3}, "bin"),
    ("convert_gain", {"gain": "g.mrc", "flip": "z"}, "flip"),
    ("convert_gain", {"gain": "g.mrc", "pixel_size": 0}, "pixel_size"),
    ("convert_gain", {"gain": "g.mrc", "pixel_size": -1.5}, "pixel_size"),
    ("convert_gain", {"gain": "g.mrc", "movie_type": "dm4"}, "movie_type"),
    ("identify_file", {}, "path"),
]


def test_L3_bad_arguments_rejected_with_short_messages(tmp_path):
    write_mrc(tmp_path / "g.mrc", np.ones((8, 8), np.float32))
    tif_write(tmp_path / "m.tif", np.ones((2, 8, 8), np.uint8))

    async def go():
        out = []
        async with Client(stdio_params(tmp_path)) as c:
            for tool, args, field in BAD_ARGS:
                res = await c.call_tool(tool, args)
                out.append((tool, args, field, res.is_error, text_of(res)))
        return out

    for tool, args, field, is_err, msg in anyio.run(go):
        assert is_err, (tool, args, msg)
        assert field in msg, (tool, args, msg)
        assert "pydantic" not in msg and "http" not in msg and "\n" not in msg, msg
        assert len(msg) < 300, msg
    assert not (tmp_path / "g_converted.mrc").exists()  # nothing written by a rejected call


@pytest.mark.parametrize("kw", [{"limit": 0}, {"limit": -5}, {"max_depth": 0}, {"max_depth": 65}])
def test_L3_direct_scan_validation(root, kw):
    with pytest.raises(ValueError):
        insp.scan_directory(root, **kw)


def test_L3_direct_frames_validation(root):
    tif_write(root / "m.tif", np.ones((2, 8, 8), np.uint8))
    write_mrc(root / "g.mrc", np.ones((8, 8), np.float32) * 1.1)
    with pytest.raises(ValueError):
        insp.inspect_movie(root / "m.tif", sample_frames=0)
    with pytest.raises(ValueError):
        gain_mod.check_orientation(root / "g.mrc", root / "m.tif", n_frames=0)


# ============================================================================ L5 / L6 / L7 (non-blocking)

def test_L5_movie_heuristic(root):
    write_mrc(root / "FoilHole_1_Data_2_fractions.mrc", np.zeros((3, 8, 8), np.int8))
    (root / "readme.tif").write_bytes(b"abc")
    r = insp.scan_directory(root)
    assert r["likely_movies"] == 1


def test_L6_no_relion30_note_for_model_or_job_star(root):
    (root / "job.star").write_text("data_job\n\n_rlnJobTypeLabel relion.class2d\n_rlnJobIsContinue 0\n")
    r = meta.summarize_star(root / "job.star")
    assert "RELION 3.0" not in json.dumps(r)
    (root / "old.star").write_text(_star(["a.mrc 1"]))
    assert "RELION 3.0" in json.dumps(meta.summarize_star(root / "old.star"))


def test_L7_format_name_consistent(root):
    tif_write(root / "gain_ref.tif", np.ones((8, 8), np.float32))
    a = insp.identify_file(root / "gain_ref.tif")["format"]
    b = gain_mod.inspect_gain(root / "gain_ref.tif")["format"]
    assert a == b


# ============================================================================ PM-8

def test_PM8_pandas_declared_dependency():
    import tomllib

    py = Path(__file__).resolve().parents[1] / "pyproject.toml"
    deps = tomllib.loads(py.read_text())["project"]["dependencies"]
    assert any(re.match(r"pandas\b", d) for d in deps), deps
