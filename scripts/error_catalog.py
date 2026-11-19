"""Build damaged files, run the tools on them and write docs/error-messages.md with the exact messages users see."""

import os
import sys
import tempfile
from pathlib import Path

import mrcfile
import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.conftest import make_gain, make_movie  # noqa: E402
from tests.test_damaged import STAR_HEAD, corrupt_frame, cut, good_movie_tif, good_stack_mrc, poke  # noqa: E402
from cryoem_files_mcp.gain import check_orientation, convert_gain, inspect_gain  # noqa: E402
from cryoem_files_mcp.inspect import identify_file, inspect_movie, inspect_mrc, scan_directory  # noqa: E402
from cryoem_files_mcp.meta import parse_mdoc, summarize_star  # noqa: E402
from cryoem_files_mcp.paths import resolve, set_roots  # noqa: E402
import struct  # noqa: E402

d = Path(tempfile.mkdtemp())
set_roots([str(d)])
rows = []


def case(title, fn, *a, **k):
    try:
        r = fn(*a, **k)
        msg = "; ".join(r.get("problems", []) + [r[k] for k in ("problem", "warning") if k in r]) or "(no problems reported)"
        kind = "reported"
    except Exception as e:
        msg, kind = str(e), "error"
    rows.append((title, kind, msg.replace("|", "\\|").replace("\n", " ")))


(d / "e.mrc").write_bytes(b"")
case("Empty MRC (0 bytes)", inspect_mrc, d / "e.mrc")
case("MRC cut inside the header", inspect_mrc, cut(good_stack_mrc(d / "h.mrc"), keep=500))
case("MRC movie cut in half", inspect_mrc, cut(good_stack_mrc(d / "t.mrc"), 0.5))
(d / "g.mrc").write_bytes(os.urandom(5000))
case("Random bytes named .mrc", inspect_mrc, d / "g.mrc")
good_movie_tif(d / "x.tif"); os.rename(d / "x.tif", d / "x.mrc")
case("TIFF renamed to .mrc", inspect_mrc, d / "x.mrc")
case("MRC header with invalid mode", inspect_mrc, poke(good_stack_mrc(d / "m.mrc"), 12, struct.pack("<i", 99)))
case("MRC header with impossible extended header", inspect_mrc, poke(good_stack_mrc(d / "x2.mrc"), 92, struct.pack("<i", 10**9)))
a = np.ones((32, 32), np.float32); a[:4] = np.nan
with mrcfile.new(d / "n.mrc") as m:
    m.set_data(a)
case("MRC with NaN pixels and no pixel size", inspect_mrc, d / "n.mrc")
case("TIFF movie cut in half (compressed)", inspect_movie, cut(good_movie_tif(d / "tz.tif"), 0.5))
case("TIFF movie cut in half (uncompressed)", inspect_movie, cut(good_movie_tif(d / "tu.tif", compression=None), 0.5))
p = corrupt_frame(good_movie_tif(d / "c.tif"), 6)
case("TIFF movie with one corrupted frame (verify_all_frames)", inspect_movie, p, sample_frames=3, verify_all_frames=True)
case("TIFF movie with one corrupted frame (frame read)", inspect_movie, p, sample_frames=10)
(d / "notes.tif").write_text("this is not an image\n" * 10)
case("Text file named .tif", inspect_movie, d / "notes.tif")
case("Movie passed as a gain", inspect_gain, good_movie_tif(d / "movie.tif"))
tifffile.imwrite(d / "nan.gain", np.full((32, 32), np.nan, np.float32))
case("Gain that is all NaN (convert)", convert_gain, d / "nan.gain")
tifffile.imwrite(d / "gm.gain", make_gain((100, 60)))
case("Gain from a different detector", check_orientation, d / "gm.gain", good_movie_tif(d / "m2.tif"))
tifffile.imwrite(d / "blank.tif", np.zeros((5, 64, 64), np.uint8))
tifffile.imwrite(d / "g64.gain", make_gain((64, 64)))
case("Blank movie in orientation check", check_orientation, d / "g64.gain", d / "blank.tif")
(d / "bad.dm4").write_bytes(b"\x00\x00\x00\x04" + os.urandom(4000))
case("Corrupted .dm4 gain", inspect_gain, d / "bad.dm4")
(d / "w.star").write_text("   \n")
case("STAR file with only whitespace", summarize_star, d / "w.star")
(d / "r.star").write_text(STAR_HEAD + "100 200 1\n100 200 1 9 9\n")
case("STAR row with extra values", summarize_star, d / "r.star")
(d / "c.star").write_text(STAR_HEAD + "100 200 1\n150 25")
case("STAR file cut off mid-row", summarize_star, d / "c.star")
(d / "t.star").write_text(STAR_HEAD + "100 200 1\nabc 250 2\n")
case("STAR with text in a number column", summarize_star, d / "t.star")
(d / "g.mdoc").write_text("hello\n")
case("Not an mdoc", parse_mdoc, d / "g.mdoc")
(d / "z.mdoc").write_text("PixelSpacing = 1\n[ZValue = 0]\nTiltAngle = 0\nExposureDose = 0\n[ZValue = 1]\nExposureDose = 0\nbroken line\n")
case("mdoc with missing tilt, zero dose, broken line", parse_mdoc, d / "z.mdoc")
s = d / "session"; s.mkdir()
for i in range(6):
    good_movie_tif(s / f"movie_{i}.tif", n=20, compression=None)
cut(s / "movie_5.tif", 0.2); (s / "movie_6.tif").write_bytes(b"")
case("Session folder with incomplete copies", scan_directory, s)
case("Folder passed as a file", inspect_mrc, s)
case("Missing file", resolve, "session/missing.mrc")
os.symlink("/etc/hostname", d / "outside.mrc")
case("Symlink pointing outside the allowed folders", resolve, "outside.mrc")
if hasattr(os, "mkfifo"):
    os.mkfifo(d / "pipe.mrc")
    case("Named pipe (FIFO) named .mrc", inspect_mrc, d / "pipe.mrc")
    os.mkfifo(s / "pipe.tif")
deep = s / "a" / "b" / "c"; deep.mkdir(parents=True); good_movie_tif(deep / "movie_deep.tif")
case("Session folder with a pipe and deeper folders (max_depth=2)", scan_directory, s, max_depth=2)
rng = np.random.default_rng(0)
tifffile.imwrite(d / "raw.tif", rng.poisson(3, (20, 256, 256)).astype(np.uint8))
tifffile.imwrite(d / "other.gain", make_gain((256, 256), seed=9))
case("Gain that does not belong to the movie (orientation)", check_orientation, d / "other.gain", d / "raw.tif")
case("Invalid scan limit", scan_directory, s, limit=-5)

out = Path(__file__).resolve().parents[1] / "docs" / "error-messages.md"
lines = ["# Error messages for damaged files", "",
         "Generated by `scripts/error_catalog.py`: each row is a real damaged file and the exact text the tool returns.",
         "'error' means the tool stopped; 'reported' means it still returned results with these problems listed.", "",
         "| Case | Result | Message |", "| --- | --- | --- |"]
lines += [f"| {t} | {k} | {m} |" for t, k, m in rows]
out.write_text("\n".join(lines) + "\n")
for t, k, m in rows:
    print(f"\n[{k}] {t}\n  {m}")
