"""File identification and integrity, MRC and movie inspection, directory scans."""

from __future__ import annotations

import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .formats import (
    FORMAT_NAMES,
    MOVIE_EXTS,
    FileProblem,
    Movie,
    capture_validate,
    check_format,
    data_warnings,
    detect_format,
    mrc_check,
    open_mrc,
    open_tiff,
    plural,
    regular_stat,
    sample_stats,
    special_kind,
)
from .paths import display, is_allowed


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n} B"


def identify_file(path: Path, verify_data: bool = False) -> dict:
    """Detect the real format, guess the role and check integrity. Never raises for a damaged file:
    damage is reported under 'problems' so the user sees exactly what is wrong."""
    out: dict = {"file": path.name}
    regular_stat(path)  # a folder, pipe or device is a usage error, not a damaged file: raise (never open it)
    try:
        fmt = check_format(path)
    except FileProblem as e:
        try:
            fmt = detect_format(path)
        except OSError:
            fmt = "unknown"
        out.update({"format": FORMAT_NAMES.get(fmt, fmt), "integrity": "damaged", "problems": [str(e)]})
        if path.exists() and path.is_file():
            out["size"] = _size(path.stat().st_size)
        return out
    out["format"] = FORMAT_NAMES[fmt]
    out["size"] = _size(path.stat().st_size)
    name = path.name.lower()
    problems: list[str] = []
    notes: list[str] = []
    role = "unknown"
    try:
        if fmt in ("tiff", "bigtiff", "eer"):
            with Movie(path) as mv:
                out.update({"frames": mv.n_frames, "shape_yx": list(mv.shape), "dtype": mv.dtype, "compression": mv.compression})
                if fmt == "eer":
                    role = "movie (EER)"
                elif mv.n_frames > 1:
                    role = "movie"
                else:
                    role = "gain reference" if "gain" in name else "single image"
                if verify_data:
                    bad = _bad_frames(mv)
                    if bad:
                        problems.append(bad)
        elif fmt == "mrc":
            m = open_mrc(path)
            try:
                shape = m.data.shape
                notes += m.integrity["warnings"]
                out.update({"shape": list(shape), "mode": int(m.header.mode)})
                role = _mrc_role(name, shape)
                if verify_data:
                    problems += [f"data: {w}" for w in data_warnings(m.data)]
            finally:
                m.close()
        elif fmt in ("dm4", "dm3"):
            role = "gain reference (Gatan)" if "gain" in name else "Gatan image"
            if verify_data:
                from .formats import read_2d
                read_2d(path)
        elif fmt == "star":
            role = "STAR metadata"
        elif fmt == "mdoc":
            role = "SerialEM mdoc metadata"
        else:
            notes.append("format not recognised from contents; this may not be a cryo-EM file")
    except FileProblem as e:
        problems.append(str(e))
    out["role_guess"] = role
    if problems:
        out["integrity"] = "damaged"
    elif fmt == "unknown":
        out["integrity"] = "unrecognised format: not checked"
    elif fmt in ("star", "mdoc"):
        out["integrity"] = "text file: use summarize_star or parse_mdoc to check its contents"
    else:
        out["integrity"] = "ok" if verify_data else "header ok (pass verify_data=true to decode all data)"
    if problems:
        out["problems"] = problems
    if notes:
        out["notes"] = notes
    return out


def _mrc_role(name: str, shape: tuple) -> str:
    """Guess what an MRC holds from its name and shape (z, y, x)."""
    if "gain" in name:
        return "gain reference"
    if len(shape) == 2 or shape[0] == 1:
        return "micrograph or 2D image"
    nz, ny, nx = shape[0], shape[1], shape[2]
    if nz == ny == nx:
        return "3D map/volume"
    if name.endswith(".mrcs") or (nz > 50 and ny == nx and ny <= 512):
        return "particle stack"
    return "movie or image stack"


def _bad_frames(mv: Movie) -> str | None:
    """Decode every frame; report all bad ones (not just the first)."""
    bad = []
    first_err = None
    for i in range(mv.n_frames):
        try:
            mv.frame(i)
        except FileProblem as e:
            bad.append(i + 1)
            first_err = first_err or e.problem
    if not bad:
        return None
    ranges = _ranges(bad)
    return (f"{len(bad)} of {plural(mv.n_frames, 'frame')} cannot be decoded (frames {ranges}); first error: {first_err} "
            f"The other {mv.n_frames - len(bad)} frames are readable.")


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(out[:10]) + (" ..." if len(out) > 10 else "")


def inspect_mrc(path: Path, validate: bool = True) -> dict:
    m = open_mrc(path)
    try:
        h = m.header
        vs = m.voxel_size
        out = {
            "file": path.name,
            "shape_zyx": list(m.data.shape),
            "mode": int(h.mode),
            "dtype": str(m.data.dtype),
            "voxel_size_A": [round(float(vs.x), 4), round(float(vs.y), 4), round(float(vs.z), 4)],
            "origin_A": [round(float(h.origin.x), 3), round(float(h.origin.y), 3), round(float(h.origin.z), 3)],
            "space_group": int(h.ispg),
            "extended_header_bytes": int(h.nsymbt),
            "labels": [lab.decode("latin-1").strip() for lab in h.label[: max(0, min(int(h.nlabl), 10))]
                       if lab.decode("latin-1").strip()][:3],
            "stats": sample_stats(m.data),
        }
        problems = list(m.integrity["warnings"]) + data_warnings(m.data)
        if float(vs.x) <= 0 or not np.isfinite(float(vs.x)):
            problems.append("pixel size in the header is 0 or invalid; set it before processing")
    finally:
        m.close()
    if validate:
        ok, issues = capture_validate(path)
        out["mrc2014_valid"] = ok
        if not ok:
            out["validation_issues"] = issues
    if problems:
        out["problems"] = problems
    return out


def inspect_movie(path: Path, sample_frames: int = 5, verify_all_frames: bool = False) -> dict:
    if not 1 <= sample_frames <= 10_000:
        raise ValueError("sample_frames must be at least 1 and at most 10000")
    with Movie(path) as mv:
        s, used = mv.sum_frames(sample_frames)
        per_frame = s / used
        mean = float(per_frame.mean()) if np.isfinite(per_frame).all() else float("nan")
        out = {
            "file": path.name,
            "format": FORMAT_NAMES[mv.format],
            "frames": mv.n_frames,
            "frame_shape_yx": list(mv.shape),
            "dtype": mv.dtype,
            "compression": mv.compression,
            "mean_per_pixel_per_frame": round(mean, 4) if np.isfinite(mean) else None,
            "sampled_frames": used,
            "size": _size(path.stat().st_size),
        }
        problems = data_warnings(per_frame, "frame")
        if np.isfinite(mean) and mean == 0:
            problems.append(f"the first {used} frames contain no counts (all zero): blank exposure")
        if mv.dtype.startswith("float"):
            out["gain_normalised_guess"] = "probably yes (float frames)"
        else:
            out["gain_normalised_guess"] = "no (integer counts): a gain reference is needed"
        if mv.format == "eer":
            out["note"] = "EER: each page is a short sub-frame of electron events; group frames (e.g. FmIntFile) before motion correction."
        elif np.isfinite(mean) and mean > 0:
            out["approx_total_counts_per_pixel"] = round(mean * mv.n_frames, 2)
        if mv.n_frames == 1:
            problems.append("only 1 frame: this is a single image, not a movie")
        if verify_all_frames:
            bad = _bad_frames(mv)
            out["all_frames_verified"] = bad is None
            if bad:
                problems.append(bad)
    if problems:
        out["problems"] = problems
    return out


MRC_HEADER_CHECKS = 500  # header-vs-size checks per scan (1 KB read each)
TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def _is_movie(p: Path, low: str, size: int) -> bool:
    """Heuristic: does this file look like a raw movie? Small .tif files are checked for the TIFF signature."""
    ext = p.suffix.lower()
    if ext == ".eer":
        return not low.endswith(".gain.eer")
    if ext in (".tif", ".tiff"):
        if size < 1_000_000:  # tiny files: only count real TIFFs (a 3-byte text file named .tif is not a movie)
            try:
                with open(p, "rb") as f:
                    return f.read(4) in TIFF_MAGIC
            except OSError:
                return False
        return True
    if ext in (".mrc", ".mrcs"):
        return any(w in low for w in ("frame", "movie", "fractions"))
    return False


def scan_directory(root: Path, pattern: str = "*", limit: int = 20000, max_depth: int = 8) -> dict:
    """Count files by type under root. max_depth counts folder levels, the given folder being level 1.

    Never opens anything that is not a regular file, and never follows or reads a symlink whose target is
    outside the allowed folders (it is skipped and counted).
    """
    if not 1 <= limit <= 1_000_000:
        raise ValueError("limit must be at least 1 and at most 1000000")
    if not 1 <= max_depth <= 64:
        raise ValueError("max_depth must be at least 1 and at most 64")
    if not root.exists():
        raise FileProblem(root, "folder does not exist.", cause="wrong path")
    if not root.is_dir():
        raise FileProblem(root, "is a file, not a folder.", fix="use identify_file for single files")
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    by_ext_files: dict[str, list[tuple[str, int]]] = defaultdict(list)
    newest = (0.0, "")
    gains, movies, seen = [], 0, 0
    unreadable: list[str] = []
    empty_files: list[str] = []
    special: list[str] = []
    outside_links: list[str] = []
    deeper_dirs = 0
    base_depth = len(root.parts)
    truncated = False

    def onerror(err: OSError):
        where = display(Path(err.filename)) if err.filename else "?"
        unreadable.append(f"{where}: {err.strerror}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):  # does not descend into symlinked folders
        here = Path(dirpath)
        for dn in dirnames:
            dp = here / dn
            if dp.is_symlink() and not is_allowed(dp.resolve()):
                outside_links.append(str(dp.relative_to(root)))
        if len(here.parts) - base_depth >= max_depth - 1:
            deeper_dirs += len(dirnames)
            dirnames[:] = []
        for fn in filenames:
            p = here / fn
            if not p.match(pattern):
                continue
            rel = str(p.relative_to(root))
            if p.is_symlink() and not is_allowed(p.resolve()):
                outside_links.append(rel)  # never stat or read the target
                continue
            seen += 1
            if seen > limit:
                truncated = True
                break
            low = fn.lower()
            ext = ".gain.eer" if low.endswith(".gain.eer") else (p.suffix.lower() or "(none)")
            try:
                st = p.stat()
            except OSError as e:
                unreadable.append(f"{rel}: {e.strerror}")
                continue
            kind = special_kind(st.st_mode)
            if kind:
                special.append(f"{rel} ({kind})")
                continue
            counts[ext] += 1
            sizes[ext] += st.st_size
            by_ext_files[ext].append((rel, st.st_size))
            if st.st_size == 0:
                empty_files.append(rel)
            if st.st_mtime > newest[0]:
                newest = (st.st_mtime, rel)
            if "gain" in low or low.endswith((".gain", ".dm4")):
                gains.append(rel)
            elif _is_movie(p, low, st.st_size):
                movies += 1
        if truncated:
            break

    problems = []
    notes = []
    scanned = min(seen, limit)
    if scanned == 0:
        if deeper_dirs:
            problems.append(f"no files in the top {max_depth} folder levels, but {plural(deeper_dirs, 'deeper folder')} "
                            f"{'was' if deeper_dirs == 1 else 'were'} not scanned; pass a larger max_depth")
        else:
            problems.append("folder is empty (or nothing matches the pattern)")
    elif deeper_dirs:
        problems.append(f"{plural(deeper_dirs, 'folder')} deeper than max_depth={max_depth} not scanned; "
                        "pass a larger max_depth to include them")
    if empty_files:
        problems.append(f"{plural(len(empty_files), 'empty (0-byte) file')}, e.g. {', '.join(empty_files[:3])}: failed or in-progress copies")
    # same-type files much smaller than their siblings are usually incomplete copies
    suspicious = []
    for ext, items in by_ext_files.items():
        if ext not in MOVIE_EXTS or len(items) < 5:
            continue
        med = statistics.median(s for _, s in items)
        small = [(n, s) for n, s in items if 0 < s < 0.5 * med]
        suspicious += small
    if suspicious:
        ex = ", ".join(f"{n} ({_size(s)})" for n, s in suspicious[:3])
        problems.append(f"{plural(len(suspicious), 'movie/image file')} less than half the typical size, possibly truncated: {ex}. "
                        "Check them with identify_file")
    # cheap header-vs-size check on MRC files (reads 1 KB each)
    mrc_files = [rel for ext in (".mrc", ".mrcs") for rel, _ in by_ext_files.get(ext, [])]
    mrc_bad = []
    for rel in mrc_files[:MRC_HEADER_CHECKS]:
        try:
            mrc_check(root / rel)
        except FileProblem as e:
            mrc_bad.append(e.problem)
        except OSError:
            pass
    if mrc_bad:
        problems.append(f"{plural(len(mrc_bad), 'damaged MRC file')}; first: {mrc_bad[0]}")
    if len(mrc_files) > MRC_HEADER_CHECKS:
        notes.append(f"header-checked {MRC_HEADER_CHECKS} of {len(mrc_files)} MRC files; the rest were only checked "
                     "by size. Scan subfolders separately (or use a pattern) to header-check them all")
    if movies and not gains:
        problems.append("movies found but no gain reference (no file with 'gain' in its name, .gain or .dm4)")
    if len(gains) > 1:
        problems.append(f"{plural(len(gains), 'gain reference')} found; make sure each session uses the right one")
    if unreadable:
        problems.append(f"{plural(len(unreadable), 'file/folder')} could not be read (permissions?), e.g. {unreadable[0]}")
    if special:
        problems.append(f"{plural(len(special), 'special file')} (pipes, devices or sockets) skipped without opening, "
                        f"e.g. {special[0]}")
    if outside_links:
        notes.append(f"{plural(len(outside_links), 'symlink')} pointing outside the allowed folders skipped (not read), "
                     f"e.g. {outside_links[0]}")
    if truncated:
        problems.append(f"stopped after {limit} files; narrow the folder or pattern, or raise limit")

    out = {
        "folder": display(root),
        "files_scanned": scanned,
        "by_extension": {k: {"count": counts[k], "size": _size(sizes[k])} for k, _ in counts.most_common(12)},
        "likely_movies": movies,
        "gain_references_found": gains[:10],
        "newest_file": newest[1] or None,
        "newest_modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(newest[0])) if newest[0] else None,
        "mrc_headers_checked": f"{min(len(mrc_files), MRC_HEADER_CHECKS)} of {len(mrc_files)}",
        "skipped": {"deeper_folders": deeper_dirs, "outside_symlinks": len(outside_links), "special_files": len(special)},
        "problems": problems,
    }
    if notes:
        out["notes"] = notes
    return out
