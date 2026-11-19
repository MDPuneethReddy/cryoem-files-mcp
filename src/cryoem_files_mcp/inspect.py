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
    sample_stats,
)


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
                if "gain" in name:
                    role = "gain reference"
                elif len(shape) == 2:
                    role = "micrograph or 2D image"
                elif name.endswith(".mrcs") or (shape[0] > 1 and shape[1] < 1024):
                    role = "particle stack"
                elif shape[0] == shape[1] == shape[2]:
                    role = "3D map/volume"
                else:
                    role = "movie or stack"
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
    out["integrity"] = "damaged" if problems else ("ok" if verify_data else "header ok (pass verify_data=true to decode all data)")
    if problems:
        out["problems"] = problems
    if notes:
        out["notes"] = notes
    return out


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
    if sample_frames < 1:
        raise ValueError("sample_frames must be at least 1")
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


def scan_directory(root: Path, pattern: str = "*", limit: int = 20000, max_depth: int = 4) -> dict:
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
    base_depth = len(root.parts)
    truncated = False

    def onerror(err: OSError):
        unreadable.append(f"{err.filename}: {err.strerror}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        if len(Path(dirpath).parts) - base_depth >= max_depth:
            dirnames[:] = []
        for fn in filenames:
            p = Path(dirpath) / fn
            if not p.match(pattern):
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
                unreadable.append(f"{p.relative_to(root)}: {e.strerror}")
                continue
            rel = str(p.relative_to(root))
            counts[ext] += 1
            sizes[ext] += st.st_size
            by_ext_files[ext].append((rel, st.st_size))
            if st.st_size == 0:
                empty_files.append(rel)
            if st.st_mtime > newest[0]:
                newest = (st.st_mtime, rel)
            if "gain" in low or low.endswith((".gain", ".dm4")):
                gains.append(rel)
            elif p.suffix.lower() in MOVIE_EXTS and ("frame" in low or "movie" in low or p.suffix.lower() in (".tif", ".tiff", ".eer")):
                movies += 1
        if truncated:
            break

    problems = []
    if seen == 0:
        problems.append("folder is empty (or nothing matches the pattern)")
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
    mrc_bad = []
    for ext in (".mrc", ".mrcs"):
        for rel, _ in by_ext_files.get(ext, [])[:500]:
            try:
                mrc_check(root / rel)
            except FileProblem as e:
                mrc_bad.append(e.problem)
            except OSError:
                pass
    if mrc_bad:
        problems.append(f"{plural(len(mrc_bad), 'damaged MRC file')}; first: {mrc_bad[0]}")
    if movies and not gains:
        problems.append("movies found but no gain reference (no file with 'gain' in its name, .gain or .dm4)")
    if len(gains) > 1:
        problems.append(f"{plural(len(gains), 'gain reference')} found; make sure each session uses the right one")
    if unreadable:
        problems.append(f"{plural(len(unreadable), 'file/folder')} could not be read (permissions?), e.g. {unreadable[0]}")
    if truncated:
        problems.append(f"stopped after {limit} files; narrow the folder or pattern")

    return {
        "folder": str(root),
        "files_scanned": min(seen, limit),
        "by_extension": {k: {"count": counts[k], "size": _size(sizes[k])} for k, _ in counts.most_common(12)},
        "likely_movies": movies,
        "gain_references_found": gains[:10],
        "newest_file": newest[1] or None,
        "newest_modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(newest[0])) if newest[0] else None,
        "problems": problems,
    }
