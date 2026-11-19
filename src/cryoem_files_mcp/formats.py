"""Format detection, integrity checks and readers.

Every reader validates the file before trusting the library, because the libraries fail silently on
common damage: a truncated TIFF opens with fewer pages and no error, a truncated MRC opened leniently
returns no data, an empty STAR file reads as "no blocks". Problems are raised as FileProblem with
what is wrong, where, the likely cause and what to do.
"""

from __future__ import annotations

import io
import logging
import os
import stat
import warnings
from contextlib import contextmanager
from pathlib import Path

import mrcfile
import numpy as np
import tifffile
from mrcfile.dtypes import HEADER_DTYPE

EER_COMPRESSIONS = {65000, 65001, 65002}
MOVIE_EXTS = {".tif", ".tiff", ".eer", ".mrc", ".mrcs"}
MRC_MODES = {0: 1, 1: 2, 2: 4, 3: 4, 4: 8, 6: 2, 12: 2, 101: 0.5}
MRC_MODE_NAMES = {0: "int8", 1: "int16", 2: "float32", 3: "complex int16", 4: "complex64", 6: "uint16", 12: "float16", 101: "4-bit"}

# what contents each extension may legitimately hold
EXPECTED = {
    ".mrc": {"mrc"}, ".mrcs": {"mrc"}, ".map": {"mrc"}, ".st": {"mrc"},
    ".tif": {"tiff", "bigtiff"}, ".tiff": {"tiff", "bigtiff"},
    ".eer": {"eer", "bigtiff", "tiff"},  # '*.gain.eer' gain references are plain TIFF
    ".gain": {"tiff", "bigtiff", "mrc"},
    ".dm4": {"dm4"}, ".dm3": {"dm3"},
    ".star": {"star"}, ".mdoc": {"mdoc"},
}
FORMAT_NAMES = {"mrc": "MRC", "tiff": "TIFF", "bigtiff": "BigTIFF", "eer": "EER", "dm4": "Gatan DM4", "dm3": "Gatan DM3",
                "star": "STAR", "mdoc": "SerialEM mdoc", "empty": "empty", "unknown": "unrecognised"}


class FileProblem(ValueError):
    """A file is missing, damaged or not what its name says. The message is meant for the end user."""

    def __init__(self, path: Path | str, problem: str, cause: str | None = None, fix: str | None = None):
        self.path, self.problem, self.cause, self.fix = Path(path), problem, cause, fix
        msg = f"{Path(path).name}: {problem}"
        if cause:
            msg += f" Likely cause: {cause}."
        if fix:
            msg += f" Fix: {fix}."
        super().__init__(msg)


RECOPY = "re-copy the file from the microscope/storage and compare its size with the original"


def _mb(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    if n >= 1e3:
        return f"{n / 1e3:.1f} KB"
    return f"{int(n)} bytes"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# ---------------------------------------------------------------- basic checks and detection

_SPECIAL_KINDS = (
    (stat.S_ISFIFO, "named pipe (FIFO)"),
    (stat.S_ISCHR, "character device"),
    (stat.S_ISBLK, "block device"),
    (stat.S_ISSOCK, "socket"),
)


def special_kind(mode: int) -> str | None:
    """Name of a non-regular, non-folder file type (pipe, device, socket), or None for a regular file/folder."""
    if stat.S_ISREG(mode) or stat.S_ISDIR(mode):
        return None
    return next((name for test, name in _SPECIAL_KINDS if test(mode)), "special file")


def regular_stat(path: Path) -> os.stat_result:
    """stat() a path and refuse anything that is not a regular file, without ever opening it.

    Opening a named pipe blocks until a writer appears and reading a device never ends, so the
    type is checked with stat (which does not open the file) before any read.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise FileProblem(path, "does not exist.", cause="wrong path, or the file was moved or deleted",
                          fix="check the name; relative paths start at the allowed folder") from None
    except PermissionError:
        raise FileProblem(path, "cannot be read: permission denied.", fix="check the file's permissions (ls -l)") from None
    except OSError as e:
        raise FileProblem(path, f"cannot be read: {e.strerror or e}.", cause="storage or network filesystem error") from None
    if stat.S_ISDIR(st.st_mode):
        raise FileProblem(path, "is a folder, not a file.", fix="pass a file path, or use scan_directory for folders")
    kind = special_kind(st.st_mode)
    if kind:
        raise FileProblem(path, f"is a {kind}, not a regular file, so it was not opened.",
                          cause="reading a pipe or device can block forever; it is not a data file",
                          fix="pass a regular data file")
    return st


def check_basic(path: Path) -> int:
    size = regular_stat(path).st_size
    try:
        with open(path, "rb") as f:
            f.read(1)
    except PermissionError:
        raise FileProblem(path, "cannot be read: permission denied.", fix="check the file's permissions (ls -l)") from None
    except OSError as e:
        raise FileProblem(path, f"cannot be read: {e.strerror or e}.", cause="storage or network filesystem error") from None
    if size == 0:
        raise FileProblem(path, "is empty (0 bytes).", cause="the copy or transfer failed or is still in progress", fix=RECOPY)
    return size


def detect_format(path: Path) -> str:
    """Return one of: tiff, bigtiff, eer, mrc, dm4, dm3, star, mdoc, empty, unknown (from contents)."""
    with open(path, "rb") as f:
        head = f.read(1024)
    if not head:
        return "empty"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] in (b"II+\x00", b"MM\x00+"):
        try:
            with _quiet_tifffile(), tifffile.TiffFile(path) as t:
                if int(t.pages.first.compression) in EER_COMPRESSIONS:
                    return "eer"
        except Exception:
            pass
        return "bigtiff"
    if len(head) >= 212 and head[208:211] == b"MAP":
        return "mrc"
    if len(head) >= 8 and int.from_bytes(head[:4], "big") in (3, 4) and b"\x00" in head[:16]:
        return "dm4" if int.from_bytes(head[:4], "big") == 4 else "dm3"
    text = head.decode("latin-1", errors="ignore")
    printable = sum(c.isprintable() or c in "\r\n\t" for c in text) / max(len(text), 1)
    if printable > 0.95:
        if "data_" in text:
            return "star"
        if "[ZValue" in text or "[FrameSet" in text or "PixelSpacing" in text:
            return "mdoc"
    # an MRC without the 'MAP ' stamp (old IMOD/EMAN files) still has a sane header
    if len(head) >= 1024 and _plausible_mrc_header(head):
        return "mrc"
    return "unknown"


def check_format(path: Path, allowed: set[str] | None = None) -> str:
    """Basic checks + make sure the contents match the extension (and the caller's allowed formats)."""
    check_basic(path)
    fmt = detect_format(path)
    ext = path.suffix.lower()
    expected = EXPECTED.get(ext)
    if expected and fmt not in expected:
        want = " or ".join(sorted({FORMAT_NAMES[e] for e in expected}))
        if fmt == "unknown":
            with open(path, "rb") as f:
                first = f.read(8).hex(" ")
            raise FileProblem(
                path,
                f"is named {ext} but its contents are not a valid {want} file (first bytes: {first}).",
                cause="the file is damaged, truncated at the start, or is a different kind of file",
                fix=RECOPY,
            )
        raise FileProblem(
            path,
            f"is named {ext} but its contents are {FORMAT_NAMES[fmt]}.",
            cause="the file was renamed or saved with the wrong extension",
            fix=f"rename it with the correct extension, or treat it as {FORMAT_NAMES[fmt]}",
        )
    if allowed is not None and fmt not in allowed:
        want = ", ".join(sorted(FORMAT_NAMES[a] for a in allowed))
        raise FileProblem(path, f"is {FORMAT_NAMES[fmt]}, but this tool needs {want}.")
    return fmt


# ---------------------------------------------------------------- MRC

def _plausible_mrc_header(head: bytes) -> bool:
    h = np.frombuffer(head[:1024], dtype=HEADER_DTYPE)[0]
    try:
        nx, ny, nz, mode = int(h["nx"]), int(h["ny"]), int(h["nz"]), int(h["mode"])
    except Exception:
        return False
    return 0 < nx < 1_000_000 and 0 < ny < 1_000_000 and 0 < nz < 10_000_000 and mode in MRC_MODES


def mrc_check(path: Path) -> dict:
    """Validate an MRC header against the file size. Returns header facts; raises FileProblem if unusable."""
    size = check_basic(path)
    if size < 1024:
        raise FileProblem(path, f"is only {size} bytes; an MRC header alone needs 1024.",
                          cause="the file is truncated (incomplete copy)", fix=RECOPY)
    check_format(path, {"mrc"})
    with open(path, "rb") as f:
        head = f.read(1024)
    stamp = head[212:214]
    big_endian = stamp[:1] == b"\x11"
    dt = HEADER_DTYPE.newbyteorder(">") if big_endian else HEADER_DTYPE
    h = np.frombuffer(head, dtype=dt)[0]
    nx, ny, nz, mode, nsym = (int(h[k]) for k in ("nx", "ny", "nz", "mode", "nsymbt"))
    warnings_: list[str] = []
    if head[208:212] not in (b"MAP ", b"MAP\x00"):
        warnings_.append("missing the 'MAP ' identifier at byte 208 (older or non-standard writer)")
    if not (0 < nx < 1_000_000 and 0 < ny < 1_000_000 and 0 < nz < 10_000_000):
        raise FileProblem(path, f"has an invalid MRC header: dimensions nx={nx}, ny={ny}, nz={nz}.",
                          cause="the header bytes are corrupted or this is not an MRC file", fix=RECOPY)
    if mode not in MRC_MODES:
        raise FileProblem(path, f"has an invalid MRC header: unknown data mode {mode} (valid: {sorted(MRC_MODES)}).",
                          cause="the header is corrupted", fix=RECOPY)
    if nsym < 0 or 1024 + nsym > size:
        raise FileProblem(path, f"has an invalid MRC header: extended header of {nsym} bytes does not fit in a {_mb(size)} file.",
                          cause="the header is corrupted or the file is truncated", fix=RECOPY)
    bps = MRC_MODES[mode]
    data_bytes = ((nx + 1) // 2) * ny * nz if mode == 101 else int(nx * ny * nz * bps)
    expected = 1024 + nsym + data_bytes
    if size < expected:
        section = int(nx * ny * bps) or 1
        complete = max(0, (size - 1024 - nsym) // section)
        what = "frames (sections)"
        detail = f" Only {complete} of {nz} {what} are complete." if nz > 1 else ""
        raise FileProblem(
            path,
            f"is truncated: the header describes {nx}x{ny}x{nz} {MRC_MODE_NAMES[mode]} data ({_mb(expected)}), "
            f"but the file is {_mb(size)} ({_mb(expected - size)} missing).{detail}",
            cause="incomplete copy or transfer, or the writer crashed",
            fix=RECOPY,
        )
    if size > expected:
        warnings_.append(f"{_mb(size - expected)} of extra bytes after the data (header may understate the size)")
    return {"nx": nx, "ny": ny, "nz": nz, "mode": mode, "big_endian": big_endian, "warnings": warnings_}


def open_mrc(path: Path):
    """Integrity-check, then memory-map. The caller closes it."""
    info = mrc_check(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = mrcfile.mmap(path, mode="r", permissive=True)
    except Exception as e:
        raise FileProblem(path, f"could not be opened as MRC: {e}.", fix=RECOPY) from None
    if m.data is None:
        m.close()
        raise FileProblem(path, "MRC header was read but its data block could not be mapped.", fix=RECOPY)
    m.integrity = info
    return m


# ---------------------------------------------------------------- TIFF / EER

class _LogCatcher(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextmanager
def _quiet_tifffile():
    """Capture tifffile's logged warnings (it logs damage instead of raising)."""
    logger = logging.getLogger("tifffile")
    catcher = _LogCatcher()
    old = logger.propagate
    logger.addHandler(catcher)
    logger.propagate = False
    try:
        yield catcher
    finally:
        logger.removeHandler(catcher)
        logger.propagate = old


def open_tiff(path: Path) -> tuple[tifffile.TiffFile, int]:
    """Open a TIFF/BigTIFF/EER and verify the page chain and every page's data lie inside the file."""
    size = check_basic(path)
    with _quiet_tifffile() as log:
        try:
            t = tifffile.TiffFile(path)
        except Exception as e:
            raise FileProblem(path, f"has a damaged TIFF structure and cannot be opened ({e}).",
                              cause="the file is truncated or corrupted", fix=RECOPY) from None
        try:
            n = len(t.pages)
        except Exception as e:
            t.close()
            raise FileProblem(path, f"has a damaged TIFF page directory ({e}).", cause="corruption", fix=RECOPY) from None
    broken = [m for m in log.messages if "invalid page offset" in m or "corrupted tag list" in m or "invalid" in m.lower()]
    if broken:
        t.close()
        raise FileProblem(
            path,
            f"is damaged: only the first {n} page(s)/frame(s) could be read before the TIFF page chain breaks ({broken[0].split('>')[-1].strip()}).",
            cause="the file is truncated (incomplete copy) or corrupted after frame " + str(n),
            fix=RECOPY,
        )
    if n == 0:
        t.close()
        raise FileProblem(path, "is a TIFF with no pages (no image data).", fix=RECOPY)
    for i, page in enumerate(t.pages):
        offs = getattr(page, "dataoffsets", ()) or ()
        cnts = getattr(page, "databytecounts", ()) or ()
        end = max((o + c for o, c in zip(offs, cnts)), default=0)
        if end > size:
            t.close()
            raise FileProblem(
                path,
                f"is truncated: frame {i + 1} of {n} needs data up to byte {end}, but the file ends at byte {size} "
                f"({_mb(end - size)} missing). Frames {i + 1}-{n} are incomplete.",
                cause="incomplete copy or transfer",
                fix=RECOPY,
            )
    return t, n


def decode_page(path: Path, t: tifffile.TiffFile, i: int, n: int) -> np.ndarray:
    try:
        with _quiet_tifffile():
            return t.pages[i].asarray()
    except Exception as e:
        kind = type(e).__name__
        hint = ""
        if "imagecodecs" in str(e).lower() or "codec" in str(e).lower() and "not" in str(e).lower():
            raise FileProblem(path, f"frame {i + 1} of {n} uses a compression this installation cannot decode ({e}).",
                              fix="pip install --upgrade imagecodecs") from None
        raise FileProblem(
            path,
            f"frame {i + 1} of {n} cannot be decoded ({kind}: {e}).{hint}",
            cause="the compressed data of that frame is corrupted",
            fix=RECOPY + "; if only a few frames are bad, the rest of the movie may still be usable",
        ) from None


# ---------------------------------------------------------------- 2D images (gains)

def read_2d(path: Path) -> tuple[np.ndarray, str]:
    """Read one 2D image (gain reference / micrograph) as float32. Refuses stacks and damaged files."""
    fmt = check_format(path, {"tiff", "bigtiff", "eer", "mrc", "dm4", "dm3"})
    if fmt == "eer":
        raise FileProblem(path, "is an EER movie (electron events), not a gain reference.",
                          fix="pass the gain file (.gain or .mrc) that came with the EER movies")
    if fmt in ("tiff", "bigtiff"):
        t, n = open_tiff(path)
        try:
            if n != 1:
                raise FileProblem(path, f"has {n} pages; a gain reference is a single 2D image.",
                                  cause="this is probably a movie, not a gain", fix="pass the gain reference file")
            data = decode_page(path, t, 0, 1)
        finally:
            t.close()
    elif fmt == "mrc":
        m = open_mrc(path)
        try:
            data = np.asarray(m.data)
            if data.ndim == 3:
                if data.shape[0] != 1:
                    raise FileProblem(path, f"is a stack of {data.shape[0]} images; a gain reference is a single 2D image.",
                                      fix="pass the gain reference file")
                data = data[0]
            data = np.array(data)
        finally:
            m.close()
    else:
        data = _read_dm(path)
    data = np.squeeze(data)
    if data.ndim == 3 and data.shape[-1] > 4:
        raise FileProblem(path, f"is a stack of {data.shape[0]} images; a gain reference is a single 2D image.",
                          cause="this is probably a movie, not a gain", fix="pass the gain reference file")
    if data.ndim != 2:
        raise FileProblem(path, f"holds {data.ndim}-D data with shape {list(data.shape)}; expected a 2D image.")
    if np.iscomplexobj(data):
        raise FileProblem(path, "holds complex (Fourier) data, not a real-space image.")
    return data.astype(np.float32, copy=False), fmt


def _read_dm(path: Path) -> np.ndarray:
    try:
        from ncempy.io import dm
    except ImportError:
        raise FileProblem(path, "is a Gatan DM file, which needs an optional package.",
                          fix="pip install 'cryoem-files-mcp[dm4]'") from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with dm.fileDM(str(path)) as f:
                data = np.asarray(f.getDataset(0)["data"])
    except Exception as e:
        raise FileProblem(path, f"is a damaged or unsupported Gatan DM file ({type(e).__name__}: {e}).",
                          cause="truncated/corrupted file or an unusual DM layout",
                          fix="re-copy it, or convert it with IMOD: dm2mrc in.dm4 out.mrc") from None
    if data.size == 0:
        raise FileProblem(path, "is a Gatan DM file with no image data.", fix=RECOPY)
    return data


def data_warnings(a: np.ndarray, what: str = "image") -> list[str]:
    """Content problems that do not stop reading but the user should know about."""
    out = []
    flat = np.asarray(a).reshape(-1)
    step = max(1, flat.size // 4_000_000)
    s = flat[::step].astype(np.float64)
    nonfinite = ~np.isfinite(s)
    if nonfinite.all():
        return [f"every {what} pixel is NaN or infinite: the data is unusable"]
    if nonfinite.any():
        out.append(f"{100 * nonfinite.mean():.3g}% of {what} pixels are NaN or infinite")
    f = s[~nonfinite]
    if f.size and f.min() == f.max():
        out.append(f"all {what} pixels have the same value ({f.min():g}): no real data")
    return out


# ---------------------------------------------------------------- movies

class Movie:
    """Uniform, integrity-checked access to frames in TIFF, EER or MRC stacks."""

    def __init__(self, path: Path):
        self.path = path
        self.format = check_format(path, {"tiff", "bigtiff", "eer", "mrc"})
        self._tif = self._mrc = None
        if self.format in ("tiff", "bigtiff", "eer"):
            self._tif, self.n_frames = open_tiff(path)
            p0 = self._tif.pages.first
            self._stack = None
            if self.n_frames == 1 and len(p0.shape) == 3 and p0.shape[0] > 1 and p0.shape[-1] > 4:
                # all frames stored in one page (TIFF ImageDepth / volumetric layout)
                self._stack = decode_page(path, self._tif, 0, 1)
                self.n_frames = self._stack.shape[0]
            self.shape = tuple(p0.shape[-2:])
            self.dtype = str(p0.dtype)
            self.compression = getattr(p0.compression, "name", str(p0.compression))
            shapes = {tuple(p.shape[-2:]) for p in self._tif.pages}
            if len(shapes) > 1:
                self.close()
                raise FileProblem(path, f"has frames of different sizes {sorted(shapes)}; a movie's frames must all match.",
                                  cause="corrupted or mixed file")
        else:
            self._mrc = open_mrc(path)
            d = self._mrc.data
            self.n_frames = 1 if d.ndim == 2 else d.shape[0]
            self.shape = tuple(d.shape[-2:])
            self.dtype = str(d.dtype)
            self.compression = "none"
        self.warnings: list[str] = []

    def frame(self, i: int) -> np.ndarray:
        if self._tif is not None:
            if self._stack is not None:
                return self._stack[i]
            return decode_page(self.path, self._tif, i, self.n_frames)
        d = self._mrc.data
        return np.asarray(d if d.ndim == 2 else d[i])

    def sum_frames(self, n: int) -> tuple[np.ndarray, int]:
        n = max(1, min(int(n), self.n_frames))
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


# ---------------------------------------------------------------- misc helpers

def sample_stats(a: np.ndarray, max_points: int = 2_000_000) -> dict:
    """min/max/mean/std from a strided sample so huge arrays stay cheap (NaN/inf ignored)."""
    flat = np.asarray(a).reshape(-1)
    step = max(1, flat.size // max_points)
    s = np.asarray(flat[::step], dtype=np.float64)
    finite = s[np.isfinite(s)]
    if finite.size == 0:
        return {"min": None, "max": None, "mean": None, "std": None}
    return {"min": _r(finite.min()), "max": _r(finite.max()), "mean": _r(finite.mean()), "std": _r(finite.std()),
            "sampled": step > 1}


def _r(x: float) -> float:
    return float(f"{float(x):.5g}")


def capture_validate(path: Path) -> tuple[bool, list[str]]:
    buf = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ok = mrcfile.validate(str(path), print_file=buf)
    issues = [line.strip() for line in buf.getvalue().splitlines() if line.strip()]
    return bool(ok), issues[:8]
