"""Damaged, mislabelled and unusual files. Each case asserts the user gets a precise message, not a crash
or a silently wrong answer. CASES also feeds docs/error-messages.md (see scripts/error_catalog.py)."""

import os
import struct
from pathlib import Path

import mrcfile
import numpy as np
import pytest
import tifffile

from cryoem_files_mcp.formats import FileProblem
from cryoem_files_mcp.gain import check_orientation, convert_gain, inspect_gain
from cryoem_files_mcp.inspect import identify_file, inspect_movie, inspect_mrc, scan_directory
from cryoem_files_mcp.meta import parse_mdoc, summarize_star
from cryoem_files_mcp.paths import PathNotAllowed, resolve

from .conftest import make_gain, make_movie

RNG = np.random.default_rng(42)


# ------------------------------------------------------------------ builders

def good_stack_mrc(p: Path, n=10, shape=(64, 64)):
    with mrcfile.new(p, overwrite=True) as m:
        m.set_data(RNG.poisson(2, (n, *shape)).astype(np.float32))
        m.voxel_size = 1.0
    return p


def good_movie_tif(p: Path, n=10, compression="zlib"):
    tifffile.imwrite(p, RNG.poisson(2, (n, 64, 64)).astype(np.uint8), compression=compression)
    return p


def cut(p: Path, frac=0.5, keep=None):
    b = p.read_bytes()
    p.write_bytes(b[: keep if keep is not None else int(len(b) * frac)])
    return p


def poke(p: Path, offset: int, data: bytes):
    b = bytearray(p.read_bytes())
    b[offset : offset + len(data)] = data
    p.write_bytes(bytes(b))
    return p


def corrupt_frame(p: Path, frame: int):
    with tifffile.TiffFile(p) as t:
        off, cnt = t.pages[frame].dataoffsets[0], t.pages[frame].databytecounts[0]
    return poke(p, off + cnt // 3, os.urandom(max(8, cnt // 3)))


def err(fn, *args, **kw) -> str:
    with pytest.raises((FileProblem, ValueError, OSError)) as e:
        fn(*args, **kw)
    return str(e.value)


# ------------------------------------------------------------------ MRC

def test_mrc_empty(tmp_path):
    (tmp_path / "e.mrc").write_bytes(b"")
    assert "is empty (0 bytes)" in err(inspect_mrc, tmp_path / "e.mrc")


def test_mrc_header_cut(tmp_path):
    p = cut(good_stack_mrc(tmp_path / "h.mrc"), keep=500)
    m = err(inspect_mrc, p)
    assert "only 500 bytes" in m and "1024" in m and "re-copy" in m


def test_mrc_truncated_stack_reports_complete_frames(tmp_path):
    p = cut(good_stack_mrc(tmp_path / "t.mrc", n=10), frac=0.5)
    m = err(inspect_mrc, p)
    assert "is truncated" in m and "64x64x10" in m and "of 10 frames (sections) are complete" in m


def test_mrc_random_bytes(tmp_path):
    (tmp_path / "g.mrc").write_bytes(os.urandom(5000))
    m = err(inspect_mrc, tmp_path / "g.mrc")
    assert "not a valid MRC file" in m and "first bytes" in m


def test_tiff_renamed_to_mrc(tmp_path):
    good_movie_tif(tmp_path / "x.tif")
    os.rename(tmp_path / "x.tif", tmp_path / "x.mrc")
    assert "named .mrc but its contents are TIFF" in err(inspect_mrc, tmp_path / "x.mrc")


def test_mrc_bad_mode(tmp_path):
    p = poke(good_stack_mrc(tmp_path / "m.mrc"), 12, struct.pack("<i", 99))
    assert "unknown data mode 99" in err(inspect_mrc, p)


def test_mrc_bad_dimensions(tmp_path):
    p = poke(good_stack_mrc(tmp_path / "d.mrc"), 0, struct.pack("<i", -5))
    m = err(inspect_mrc, p)
    assert "nx=-5" in m or "not a valid MRC" in m


def test_mrc_bad_extended_header(tmp_path):
    p = poke(good_stack_mrc(tmp_path / "x.mrc"), 92, struct.pack("<i", 10**9))
    assert "extended header" in err(inspect_mrc, p)


def test_mrc_extra_bytes_is_a_warning_not_error(tmp_path):
    p = good_stack_mrc(tmp_path / "x.mrc")
    p.write_bytes(p.read_bytes() + b"\0" * 4096)
    r = inspect_mrc(p)
    assert any("extra bytes" in x for x in r["problems"])


def test_mrc_nan_and_zero_pixel_size(tmp_path):
    a = np.ones((32, 32), np.float32)
    a[:4] = np.nan
    with mrcfile.new(tmp_path / "n.mrc") as m:
        m.set_data(a)
    r = inspect_mrc(tmp_path / "n.mrc")
    assert any("NaN" in x for x in r["problems"]) and any("pixel size" in x for x in r["problems"])


def test_mrc_without_map_stamp_still_reads(tmp_path):
    p = poke(good_stack_mrc(tmp_path / "old.mrc"), 208, b"\0\0\0\0")
    r = identify_file(p)
    assert r["format"] == "MRC" and any("MAP" in n for n in r.get("notes", []))


def test_big_endian_mrc(tmp_path):
    a = np.arange(64 * 64, dtype=">f4").reshape(64, 64)
    with mrcfile.new(tmp_path / "le.mrc") as m:
        m.set_data(a.astype("<f4"))
    raw = bytearray((tmp_path / "le.mrc").read_bytes())
    hdr = np.frombuffer(bytes(raw[:1024]), dtype=mrcfile.dtypes.HEADER_DTYPE).copy()
    be = hdr.astype(mrcfile.dtypes.HEADER_DTYPE.newbyteorder(">"))
    be["machst"] = np.frombuffer(b"\x11\x11\x00\x00", dtype="u1")
    (tmp_path / "be.mrc").write_bytes(be.tobytes() + a.tobytes())
    r = inspect_mrc(tmp_path / "be.mrc", validate=False)
    assert r["shape_zyx"] == [64, 64] and r["stats"]["max"] == 4095


# ------------------------------------------------------------------ TIFF / movies

@pytest.mark.parametrize("compression", ["zlib", None])
def test_tiff_truncated_is_not_silently_shorter(tmp_path, compression):
    p = cut(good_movie_tif(tmp_path / "t.tif", compression=compression), 0.5)
    m = err(inspect_movie, p)
    assert ("truncated" in m or "damaged" in m) and "re-copy" in m


def test_tiff_corrupted_frame_is_named(tmp_path):
    p = corrupt_frame(good_movie_tif(tmp_path / "c.tif"), 6)
    r = inspect_movie(p, sample_frames=3, verify_all_frames=True)  # first 3 frames fine
    assert any("1 of 10 frames cannot be decoded (frames 7)" in x for x in r["problems"])
    assert "frame 7 of 10 cannot be decoded" in err(inspect_movie, p, sample_frames=10)
    ident = identify_file(p, verify_data=True)
    assert ident["integrity"] == "damaged"


def test_mrc_renamed_to_tif(tmp_path):
    good_stack_mrc(tmp_path / "x.mrc")
    os.rename(tmp_path / "x.mrc", tmp_path / "x.tif")
    assert "named .tif but its contents are MRC" in err(inspect_movie, tmp_path / "x.tif")


def test_text_file_named_tif(tmp_path):
    (tmp_path / "notes.tif").write_text("this is not an image\n" * 10)
    assert "not a valid BigTIFF or TIFF file" in err(inspect_movie, tmp_path / "notes.tif")


def test_tiff_header_only(tmp_path):
    p = cut(good_movie_tif(tmp_path / "h.tif"), keep=8)
    m = err(inspect_movie, p)
    assert "damaged" in m or "no pages" in m or "truncated" in m


def test_movie_frames_of_different_sizes(tmp_path):
    with tifffile.TiffWriter(tmp_path / "mix.tif") as w:
        w.write(np.zeros((64, 64), np.uint8))
        w.write(np.zeros((32, 32), np.uint8))
    assert "frames of different sizes" in err(inspect_movie, tmp_path / "mix.tif")


def test_blank_movie_and_single_frame(tmp_path):
    tifffile.imwrite(tmp_path / "blank.tif", np.zeros((5, 32, 32), np.uint8))
    r = inspect_movie(tmp_path / "blank.tif")
    assert any("no counts" in x for x in r["problems"])
    tifffile.imwrite(tmp_path / "one.tif", np.ones((32, 32), np.uint8))
    assert any("only 1 frame" in x for x in inspect_movie(tmp_path / "one.tif")["problems"])


def test_bad_sample_frames_argument(tmp_path):
    p = good_movie_tif(tmp_path / "m.tif")
    assert "at least 1" in err(inspect_movie, p, sample_frames=0)
    assert inspect_movie(p, sample_frames=10_000)["sampled_frames"] == 10  # clamped


# ------------------------------------------------------------------ gain

def test_movie_passed_as_gain(tmp_path):
    p = good_movie_tif(tmp_path / "movie.tif")
    assert "has 10 pages; a gain reference is a single 2D image" in err(inspect_gain, p)


def test_mrc_stack_passed_as_gain(tmp_path):
    p = good_stack_mrc(tmp_path / "stack.mrc", n=3)
    assert "stack of 3 images" in err(convert_gain, p)


def test_gain_all_nan_all_zero_negative(tmp_path):
    tifffile.imwrite(tmp_path / "nan.gain", np.full((32, 32), np.nan, np.float32))
    assert any("every gain pixel is NaN" in x for x in inspect_gain(tmp_path / "nan.gain")["problems"])
    assert "every gain pixel is NaN" in err(convert_gain, tmp_path / "nan.gain")
    tifffile.imwrite(tmp_path / "zero.gain", np.zeros((32, 32), np.float32))
    make_movie(tmp_path / "m.tif", make_gain((32, 32)), 0, False)
    assert "same value" in err(check_orientation, tmp_path / "zero.gain", tmp_path / "m.tif")
    neg = make_gain((32, 32))
    neg[:2] = -1
    tifffile.imwrite(tmp_path / "neg.gain", neg)
    assert any("negative" in x for x in inspect_gain(tmp_path / "neg.gain")["problems"])


def test_gain_shape_mismatch_message(tmp_path):
    tifffile.imwrite(tmp_path / "g.gain", make_gain((100, 60)))
    p = good_movie_tif(tmp_path / "m.tif")
    m = err(check_orientation, tmp_path / "g.gain", p)
    assert "60x100 pixels but the movie frames are 64x64" in m and "different detector" in m


def test_blank_movie_for_orientation(tmp_path):
    tifffile.imwrite(tmp_path / "g.gain", make_gain((32, 32)))
    tifffile.imwrite(tmp_path / "blank.tif", np.zeros((5, 32, 32), np.uint8))
    assert "frames are blank" in err(check_orientation, tmp_path / "g.gain", tmp_path / "blank.tif")


def test_gain_normalised_movie_gives_low_confidence(tmp_path):
    g = make_gain((128, 128))
    tifffile.imwrite(tmp_path / "g.gain", g)
    tifffile.imwrite(tmp_path / "norm.tif", RNG.poisson(3, (10, 128, 128)).astype(np.float32))
    r = check_orientation(tmp_path / "g.gain", tmp_path / "norm.tif")
    assert r["confidence"] == "low" and "warning" in r and "note" in r


def test_complex_mrc_as_gain(tmp_path):
    with mrcfile.new(tmp_path / "c.mrc") as m:
        m.set_data(np.ones((16, 16), np.complex64))
    assert "complex" in err(inspect_gain, tmp_path / "c.mrc")


def test_eer_movie_named_like_gain_rejected_cleanly(tmp_path):
    tifffile.imwrite(tmp_path / "stack.gain", RNG.poisson(1, (4, 32, 32)).astype(np.uint8))
    assert "stack of 4 images" in err(inspect_gain, tmp_path / "stack.gain")
    r = inspect_movie(tmp_path / "stack.gain")  # single-page 3D TIFF read as a 4-frame movie
    assert r["frames"] == 4


def test_convert_out_outside_root_and_bad_args(tmp_path):
    tifffile.imwrite(tmp_path / "g.gain", make_gain((16, 16)))
    with pytest.raises(PathNotAllowed):
        convert_gain(tmp_path / "g.gain", out="/tmp/elsewhere.mrc")
    assert "rotate must be" in err(convert_gain, tmp_path / "g.gain", rotate=5)
    assert "flip must be" in err(convert_gain, tmp_path / "g.gain", flip="z")
    assert "n_frames must be" in err(check_orientation, tmp_path / "g.gain", tmp_path / "g.gain", n_frames=0)


def test_convert_leaves_no_partial_file(tmp_path):
    tifffile.imwrite(tmp_path / "g.gain", make_gain((16, 16)))
    convert_gain(tmp_path / "g.gain")
    assert not list(tmp_path.glob("*.partial"))


# ------------------------------------------------------------------ DM4

def test_dm4_garbage_and_truncated(tmp_path):
    (tmp_path / "g.dm4").write_bytes(b"\x00\x00\x00\x04" + os.urandom(4000))
    m = err(inspect_gain, tmp_path / "g.dm4")
    assert "damaged or unsupported Gatan DM file" in m and "dm2mrc" in m
    (tmp_path / "t.dm4").write_bytes(b"\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x10\x00")
    assert "Gatan DM" in err(inspect_gain, tmp_path / "t.dm4")


def test_dm4_extension_but_tiff_inside(tmp_path):
    tifffile.imwrite(tmp_path / "g.dm4", make_gain((16, 16)))
    assert "named .dm4 but its contents are TIFF" in err(inspect_gain, tmp_path / "g.dm4")


# ------------------------------------------------------------------ STAR

STAR_HEAD = "data_particles\n\nloop_\n_rlnDefocusU #1\n_rlnDefocusV #2\n_rlnClassNumber #3\n"


def test_star_empty_whitespace_nodata_binary(tmp_path):
    (tmp_path / "e.star").write_text("")
    assert "is empty (0 bytes)" in err(summarize_star, tmp_path / "e.star")
    (tmp_path / "w.star").write_text("   \n\n")
    assert "only whitespace" in err(summarize_star, tmp_path / "w.star")
    (tmp_path / "n.star").write_text("# a comment\nfoo bar\n")
    assert "no 'data_' block" in err(summarize_star, tmp_path / "n.star")
    (tmp_path / "b.star").write_bytes(b"data_\x00\x01\x02" + os.urandom(100))
    assert "binary data" in err(summarize_star, tmp_path / "b.star")


def test_star_row_with_too_many_values(tmp_path):
    (tmp_path / "x.star").write_text(STAR_HEAD + "100 200 1\n100 200 1 9 9\n150 250 2\n")
    m = err(summarize_star, tmp_path / "x.star")
    assert "malformed: rows with the wrong number of values" in m and "line 8: 5 values but 3 columns" in m


def test_star_row_with_too_few_values_and_cut_off(tmp_path):
    (tmp_path / "x.star").write_text(STAR_HEAD + "100 200 1\n150 250 2\n175 2")  # cut mid-row
    r = summarize_star(tmp_path / "x.star")
    assert any("line 9: 2 values but 3 columns" in x for x in r["problems"])


def test_star_text_in_numeric_column(tmp_path):
    (tmp_path / "x.star").write_text(STAR_HEAD + "100 200 1\nabc 250 2\n150 250 2\n")
    r = summarize_star(tmp_path / "x.star")
    assert any("rlnDefocusU: 1 of 3 values is missing or not a number" in x for x in r["problems"])


def test_star_block_without_rows(tmp_path):
    (tmp_path / "x.star").write_text(STAR_HEAD)
    r = summarize_star(tmp_path / "x.star")
    assert any("no rows" in x for x in r["problems"])


# ------------------------------------------------------------------ mdoc

def test_mdoc_empty_garbage_malformed(tmp_path):
    (tmp_path / "e.mdoc").write_text("")
    assert "is empty" in err(parse_mdoc, tmp_path / "e.mdoc")
    (tmp_path / "g.mdoc").write_text("hello world\nnot an mdoc\n")
    assert "no [ZValue = n] sections" in err(parse_mdoc, tmp_path / "g.mdoc")
    (tmp_path / "m.mdoc").write_text("PixelSpacing = 1\n[ZValue = 0]\nTiltAngle = 0\nthis line is broken\n")
    probs = parse_mdoc(tmp_path / "m.mdoc")["problems"]
    assert any("line 4: 'this line is broken'" in x for x in probs)


def test_mdoc_missing_tilts_zero_dose_duplicates(tmp_path):
    (tmp_path / "x.mdoc").write_text(
        "PixelSpacing = 1\n[ZValue = 0]\nTiltAngle = 0\nExposureDose = 0\n"
        "[ZValue = 1]\nTiltAngle = 0\nExposureDose = 0\n[ZValue = 2]\nExposureDose = 0\n"
    )
    probs = " | ".join(parse_mdoc(tmp_path / "x.mdoc")["problems"])
    assert "1 of 3 sections has no TiltAngle" in probs and "duplicate tilt" in probs and "not calibrated" in probs


# ------------------------------------------------------------------ paths and folders

def test_folder_passed_as_file_and_missing_file(tmp_path):
    (tmp_path / "sub").mkdir()
    assert "is a folder, not a file" in err(inspect_mrc, tmp_path / "sub")
    with pytest.raises(FileNotFoundError):
        resolve("missing.mrc")


def test_symlink_and_dotdot_escape_refused(tmp_path):
    os.symlink("/etc/hostname", tmp_path / "sneaky.mrc")
    with pytest.raises(PathNotAllowed):
        resolve("sneaky.mrc")
    with pytest.raises(PathNotAllowed):
        resolve("../../etc/hostname")


def test_scan_problem_folder(tmp_path):
    for i in range(6):
        good_movie_tif(tmp_path / f"movie_{i}.tif", n=20, compression=None)
    cut(tmp_path / "movie_5.tif", 0.2)  # incomplete copy
    (tmp_path / "movie_6.tif").write_bytes(b"")  # 0-byte
    cut(good_stack_mrc(tmp_path / "mic_1.mrc"), 0.5)
    r = scan_directory(tmp_path)
    probs = " | ".join(r["problems"])
    assert "1 empty (0-byte) file" in probs
    assert "movie_5.tif" in probs and "possibly truncated" in probs
    assert "1 damaged MRC file" in probs and "is truncated" in probs
    assert "no gain reference" in probs


def test_scan_empty_missing_and_file(tmp_path):
    (tmp_path / "empty").mkdir()
    assert "folder is empty" in scan_directory(tmp_path / "empty")["problems"][0]
    assert "does not exist" in err(scan_directory, tmp_path / "nope")
    (tmp_path / "f.txt").write_text("x")
    assert "is a file, not a folder" in err(scan_directory, tmp_path / "f.txt")


def test_identify_never_raises_on_damage(tmp_path):
    (tmp_path / "e.mrc").write_bytes(b"")
    r = identify_file(tmp_path / "e.mrc")
    assert r["integrity"] == "damaged" and "is empty" in r["problems"][0]
    p = cut(good_stack_mrc(tmp_path / "t.mrc"), 0.3)
    r = identify_file(p)
    assert r["integrity"] == "damaged" and "truncated" in r["problems"][0]
