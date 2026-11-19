"""Format detection (by content, not just extension) and low-level readers."""

from __future__ import annotations

import io
import warnings
from pathlib import Path

import mrcfile
import numpy as np
import tifffile

EER_COMPRESSIONS = {65000, 65001, 65002}
MOVIE_EXTS = {".tif", ".tiff", ".eer", ".mrc", ".mrcs"}


def detect_format(path: Path) -> str:
    """Return one of: tiff, bigtiff, eer, mrc, dm4, dm3, star, mdoc, unknown."""
    with open(path, "rb") as f:
        head = f.read(1024)
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] in (b"II+\x00", b"MM\x00+"):
        try:
            with tifffile.TiffFile(path) as t:
                if int(t.pages[0].compression) in EER_COMPRESSIONS:
                    return "eer"
        except Exception:
            pass
        return "bigtiff"
    if len(head) >= 212 and head[208:211] == b"MAP":
        return "mrc"
    if len(head) >= 4:
        version = int.from_bytes(head[:4], "big")
        if version == 4:
            return "dm4"
        if version == 3:
            return "dm3"
    text = head.decode("latin-1", errors="ignore")
    if "data_" in text and ("_rln" in text or "loop_" in text):
        return "star"
    if "PixelSpacing" in text or "[ZValue" in text or "[FrameSet" in text:
        return "mdoc"
    # MRC files written by some tools lack the MAP stamp; fall back on extension
    if path.suffix.lower() in (".mrc", ".mrcs", ".map", ".st", ".ali", ".rec"):
        return "mrc"
    return "unknown"


def open_mrc(path: Path):
    """Open an MRC memory-mapped, tolerating slightly non-standard headers."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return mrcfile.mmap(path, mode="r", permissive=True)


def read_2d(path: Path) -> tuple[np.ndarray, str]:
    """Read a single 2D image (gain reference / micrograph) as float32.

    Raises if the file holds a stack, so a multi-page TIFF is never treated as one gain.
    """
    fmt = detect_format(path)
    if fmt in ("tiff", "bigtiff", "eer"):
        with tifffile.TiffFile(path) as t:
            if len(t.pages) != 1:
                raise ValueError(f"{path.name} has {len(t.pages)} pages; expected a single 2D gain image")
            data = t.pages[0].asarray()
    elif fmt == "mrc":
        with open_mrc(path) as m:
            data = np.asarray(m.data)
            if data.ndim == 3:
                if data.shape[0] != 1:
                    raise ValueError(f"{path.name} is a stack of {data.shape[0]} images; expected one 2D image")
                data = data[0]
            data = np.array(data)
    elif fmt in ("dm4", "dm3"):
        data = _read_dm(path)
    else:
        raise ValueError(f"Cannot read {path.name}: format '{fmt}' is not a supported image format")
    data = np.squeeze(data)
    if data.ndim != 2:
        raise ValueError(f"{path.name} is {data.ndim}-D; expected a 2D image")
    return data.astype(np.float32, copy=False), fmt


def _read_dm(path: Path) -> np.ndarray:
    try:
        from ncempy.io import dm
    except ImportError as e:
        raise ValueError("Reading .dm4/.dm3 needs the optional extra: pip install 'cryoem-files-mcp[dm4]'") from e
    with dm.fileDM(str(path)) as f:
        return np.asarray(f.getDataset(0)["data"])


class Movie:
    """Uniform access to movie frames in TIFF, EER or MRC stacks."""

    def __init__(self, path: Path):
        self.path = path
        self.format = detect_format(path)
        if self.format in ("tiff", "bigtiff", "eer"):
            self._tif = tifffile.TiffFile(path)
            self.n_frames = len(self._tif.pages)
            p0 = self._tif.pages[0]
            self.shape = tuple(p0.shape[-2:])
            self.dtype = str(p0.dtype)
            self.compression = getattr(p0.compression, "name", str(p0.compression))
            self._mrc = None
        elif self.format == "mrc":
            self._mrc = open_mrc(path)
            d = self._mrc.data
            self.n_frames = 1 if d.ndim == 2 else d.shape[0]
            self.shape = tuple(d.shape[-2:])
            self.dtype = str(d.dtype)
            self.compression = "none"
            self._tif = None
        else:
            raise ValueError(f"{path.name} is not a movie format this tool reads (got '{self.format}')")

    def frame(self, i: int) -> np.ndarray:
        if self._tif is not None:
            return self._tif.pages[i].asarray()
        d = self._mrc.data
        return np.asarray(d if d.ndim == 2 else d[i])

    def sum_frames(self, n: int) -> tuple[np.ndarray, int]:
        n = max(1, min(n, self.n_frames))
        acc = np.zeros(self.shape, dtype=np.float64)
        for i in range(n):
            acc += self.frame(i)
        return acc, n

    def close(self):
        if self._tif is not None:
            self._tif.close()
        if self._mrc is not None:
            self._mrc.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def sample_stats(a: np.ndarray, max_points: int = 2_000_000) -> dict:
    """min/max/mean/std from a strided sample so huge arrays stay cheap."""
    flat = np.asarray(a).reshape(-1)
    step = max(1, flat.size // max_points)
    s = np.asarray(flat[::step], dtype=np.float64)
    finite = s[np.isfinite(s)]
    if finite.size == 0:
        return {"min": None, "max": None, "mean": None, "std": None}
    return {
        "min": _r(finite.min()),
        "max": _r(finite.max()),
        "mean": _r(finite.mean()),
        "std": _r(finite.std()),
        "sampled": step > 1,
    }


def _r(x: float) -> float:
    return float(f"{float(x):.5g}")


def capture_validate(path: Path) -> tuple[bool, list[str]]:
    buf = io.StringIO()
    ok = mrcfile.validate(str(path), print_file=buf)
    issues = [line.strip() for line in buf.getvalue().splitlines() if line.strip()]
    return bool(ok), issues[:8]
