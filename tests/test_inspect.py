import json

import mrcfile
import numpy as np
import tifffile

from cryoem_files_mcp.inspect import identify_file, inspect_movie, inspect_mrc, scan_directory
from cryoem_files_mcp.meta import parse_mdoc, summarize_star

from .conftest import make_gain, make_movie

STAR = """
data_optics

loop_
_rlnOpticsGroupName #1
_rlnOpticsGroup #2
_rlnMicrographOriginalPixelSize #3
_rlnVoltage #4
_rlnSphericalAberration #5
_rlnAmplitudeContrast #6
opticsGroup1 1 0.83 300 2.7 0.1

data_micrographs

loop_
_rlnMicrographName #1
_rlnOpticsGroup #2
_rlnDefocusU #3
_rlnDefocusV #4
_rlnCtfMaxResolution #5
mic1.mrc 1 12000 11800 3.1
mic2.mrc 1 15000 14900 4.5
mic3.mrc 1 9000 8800 2.9
"""

MDOC = """PixelSpacing = 1.35
ImageFile = ts01.mrc

[ZValue = 0]
TiltAngle = 0.0
ExposureDose = 3.0
NumSubFrames = 8
SubFramePath = D:\\data\\ts01_000.tif

[ZValue = 1]
TiltAngle = 3.0
ExposureDose = 3.0
NumSubFrames = 8
SubFramePath = D:\\data\\ts01_001.tif

[ZValue = 2]
TiltAngle = -3.0
ExposureDose = 3.0
NumSubFrames = 8
SubFramePath = D:\\data\\ts01_002.tif
"""


def small(d):
    return len(json.dumps(d)) < 1500


def test_mrc_and_identify(tmp_path):
    with mrcfile.new(tmp_path / "mic.mrc") as m:
        m.set_data(np.random.default_rng(0).random((128, 128), dtype=np.float32))
        m.voxel_size = 0.83
    r = inspect_mrc(tmp_path / "mic.mrc")
    assert r["voxel_size_A"][0] == 0.83 and r["mrc2014_valid"] and small(r)
    assert identify_file(tmp_path / "mic.mrc")["role_guess"].startswith("micrograph")


def test_movie(tmp_path):
    make_movie(tmp_path / "m.tif", make_gain(), 0, False)
    r = inspect_movie(tmp_path / "m.tif")
    assert r["frames"] == 20 and "no" in r["gain_normalised_guess"] and small(r)
    assert identify_file(tmp_path / "m.tif")["role_guess"] == "movie"


def test_star(tmp_path):
    (tmp_path / "mics.star").write_text(STAR)
    r = summarize_star(tmp_path / "mics.star")
    assert r["blocks"]["optics"]["optics_groups"][0]["voltage_kV"] == 300
    assert r["blocks"]["micrographs"]["rows"] == 3
    assert r["blocks"]["micrographs"]["stats"]["ctf_max_resolution_A"]["max"] == 4.5
    assert small(r)


def test_mdoc(tmp_path):
    (tmp_path / "ts01.mrc.mdoc").write_text(MDOC)
    r = parse_mdoc(tmp_path / "ts01.mrc.mdoc")
    assert r["tilt_range_deg"] == [-3.0, 3.0] and r["tilt_step_deg"] == 3.0
    assert r["total_dose_e_per_A2"] == 9.0 and r["pixel_size_A"] == 1.35
    assert r["subframe_files_first3"][0] == "ts01_000.tif"


def test_scan_flags_missing_gain(tmp_path):
    for i in range(3):
        tifffile.imwrite(tmp_path / f"movie_{i}.tif", np.zeros((2, 8, 8), np.uint8))
    r = scan_directory(tmp_path)
    assert r["likely_movies"] == 3 and r["problems"]
    tifffile.imwrite(tmp_path / "gain.tif", make_gain(shape=(8, 8)))
    assert not scan_directory(tmp_path)["problems"]
