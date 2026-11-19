import mrcfile
import numpy as np
import pytest
import tifffile

from cryoem_files_mcp.gain import VARIANTS, check_orientation, convert_gain, inspect_gain, transform
from cryoem_files_mcp.paths import PathNotAllowed, resolve

from .conftest import make_gain, make_movie


@pytest.mark.parametrize("k,flip", VARIANTS)
def test_orientation_recovers_every_variant(tmp_path, k, flip):
    g = make_gain()
    tifffile.imwrite(tmp_path / "ref.gain", g)
    make_movie(tmp_path / "movie.tif", g, k, flip)
    r = check_orientation(tmp_path / "ref.gain", tmp_path / "movie.tif", n_frames=20)
    assert (r["rot90_ccw"], r["flip_y"]) == (k, flip) or np.array_equal(
        transform(g, r["rot90_ccw"], r["flip_y"]), transform(g, k, flip)
    )
    assert r["confidence"] == "high"
    assert r["gain_convention"].startswith("multiply")


def test_divisive_gain_detected_and_inverted(tmp_path):
    g = make_gain(seed=3)
    with mrcfile.new(tmp_path / "eer_gain.mrc") as m:
        m.set_data(g)
    make_movie(tmp_path / "movie.tif", g, 1, True, convention="divide")
    r = check_orientation(tmp_path / "eer_gain.mrc", tmp_path / "movie.tif")
    assert r["gain_convention"].startswith("divide")
    assert "invert=true" in r["next_step"]["motioncor"]  # next_step is per program since 0.1.1
    out = convert_gain(tmp_path / "eer_gain.mrc", rotate=r["rot90_ccw"], flip="y" if r["flip_y"] else "none", invert=True)
    # the converted gain must now be multiplicative and aligned: check again against the movie
    r2 = check_orientation(resolve(out["output"]), tmp_path / "movie.tif")
    assert r2["best"] == "rot0" and r2["gain_convention"].startswith("multiply")


def test_8k_gain_binned_against_4k_style_movie(tmp_path):
    g = make_gain(shape=(256, 256), seed=5)
    tifffile.imwrite(tmp_path / "big.gain", g)
    make_movie(tmp_path / "movie.tif", g, 2, False, scale_to="half", frames=40)
    r = check_orientation(tmp_path / "big.gain", tmp_path / "movie.tif", n_frames=40)
    assert r["best"] == "rot180" and "binned" in r["scaling"]


def test_convert_preserves_values_and_names_safely(tmp_path):
    g = make_gain(shape=(64, 96))
    tifffile.imwrite(tmp_path / "D.TIF", g)
    tifffile.imwrite(tmp_path / "tiff_gain_v2.tiff", g)
    a = convert_gain(tmp_path / "D.TIF")
    b = convert_gain(tmp_path / "tiff_gain_v2.tiff")
    assert a["output"].endswith("D.mrc")  # Magellon bug: became D.TIF
    assert b["output"].endswith("tiff_gain_v2.mrc")  # Magellon bug: became mrc_gain_v2.mrc
    with mrcfile.open(a["output"]) as m:
        assert m.data.dtype == np.float32 and np.array_equal(m.data, g)


def test_convert_refuses_overwrite_and_stacks(tmp_path):
    g = make_gain(shape=(32, 32))
    tifffile.imwrite(tmp_path / "x.gain", g)
    convert_gain(tmp_path / "x.gain")
    with pytest.raises(FileExistsError):
        convert_gain(tmp_path / "x.gain")
    tifffile.imwrite(tmp_path / "stack.tif", np.stack([g, g]))
    with pytest.raises(ValueError, match="pages"):
        convert_gain(tmp_path / "stack.tif")


def test_gain_eer_double_extension_and_mrc_input_not_overwritten(tmp_path):
    g = make_gain(shape=(32, 32))
    tifffile.imwrite(tmp_path / "20250205_EER_GainReference.gain.eer", g, bigtiff=True)
    out = convert_gain(tmp_path / "20250205_EER_GainReference.gain.eer")
    assert out["output"].endswith("20250205_EER_GainReference_gain.mrc")
    with mrcfile.new(tmp_path / "g.mrc") as m:
        m.set_data(g)
    out2 = convert_gain(tmp_path / "g.mrc", flip="x")
    assert out2["output"].endswith("g_converted.mrc")


def test_tiff_is_not_mistaken_for_mrc(tmp_path):
    g = make_gain(shape=(32, 32))
    tifffile.imwrite(tmp_path / "a.gain", g)
    info = inspect_gain(tmp_path / "a.gain")
    assert info["format"] == "TIFF"  # Magellon's is_mrc() said MRC (display names since 0.1.1, QA L7)
    assert info["zero_or_defect_pixels_pct"] > 0


def test_paths_outside_root_refused(tmp_path):
    with pytest.raises(PathNotAllowed):
        resolve("/etc/passwd")
