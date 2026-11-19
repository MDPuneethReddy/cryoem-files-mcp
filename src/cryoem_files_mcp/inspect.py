"""File identification, MRC and movie inspection, directory scans."""

from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path

import numpy as np

from .formats import MOVIE_EXTS, Movie, capture_validate, detect_format, open_mrc, sample_stats


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def identify_file(path: Path) -> dict:
    fmt = detect_format(path)
    name = path.name.lower()
    role = "unknown"
    detail = {}
    try:
        if fmt in ("tiff", "bigtiff", "eer"):
            with Movie(path) as mv:
                detail = {"frames": mv.n_frames, "shape_yx": list(mv.shape), "dtype": mv.dtype}
                if fmt == "eer":
                    role = "movie (EER)"
                elif mv.n_frames > 1:
                    role = "movie"
                else:
                    role = "gain reference" if ("gain" in name or name.endswith(".gain")) else "single image"
        elif fmt == "mrc":
            with open_mrc(path) as m:
                shape = m.data.shape
                detail = {"shape": list(shape), "mode": int(m.header.mode)}
                if "gain" in name:
                    role = "gain reference"
                elif len(shape) == 2:
                    role = "micrograph or 2D image"
                elif name.endswith(".mrcs") or shape[0] > 1 and shape[1] < 1024:
                    role = "particle stack"
                elif shape[0] == shape[1] == shape[2]:
                    role = "3D map/volume"
                else:
                    role = "movie or stack"
        elif fmt in ("dm4", "dm3"):
            role = "gain reference (Gatan)" if "gain" in name else "Gatan image"
        elif fmt == "star":
            role = "STAR metadata"
        elif fmt == "mdoc":
            role = "SerialEM mdoc metadata"
    except Exception as e:  # identification must never fail hard
        detail["read_error"] = str(e)[:200]
    return {"file": path.name, "format": fmt, "role_guess": role, "size": _size(path.stat().st_size), **detail}


def inspect_mrc(path: Path, validate: bool = True) -> dict:
    with open_mrc(path) as m:
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
            "labels": [
                lab.decode("latin-1").strip()
                for lab in h.label[: int(h.nlabl)]
                if lab.decode("latin-1").strip()
            ][:3],
            "stats": sample_stats(m.data),
        }
        if float(vs.x) == 0:
            out["warning"] = "Pixel size is 0 in the header; set it before processing."
    if validate:
        ok, issues = capture_validate(path)
        out["mrc2014_valid"] = ok
        if not ok:
            out["validation_issues"] = issues
    return out


def inspect_movie(path: Path, sample_frames: int = 5) -> dict:
    with Movie(path) as mv:
        s, used = mv.sum_frames(sample_frames)
        per_frame = s / used
        mean = float(per_frame.mean())
        out = {
            "file": path.name,
            "format": mv.format,
            "frames": mv.n_frames,
            "frame_shape_yx": list(mv.shape),
            "dtype": mv.dtype,
            "compression": mv.compression,
            "mean_per_pixel_per_frame": round(mean, 4),
            "sampled_frames": used,
            "size": _size(path.stat().st_size),
        }
        if mv.dtype.startswith("float"):
            out["gain_normalised_guess"] = "probably yes (float frames)"
        else:
            out["gain_normalised_guess"] = "no (integer counts): a gain reference is needed"
        if mv.format == "eer":
            out["note"] = "EER: each page is a short sub-frame of electron events; group frames (e.g. FmIntFile) before motion correction."
        elif mean > 0:
            out["approx_total_counts_per_pixel"] = round(mean * mv.n_frames, 2)
    return out


def scan_directory(root: Path, pattern: str = "*", limit: int = 20000, max_depth: int = 4) -> dict:
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    newest = (0.0, "")
    gains, movies, seen = [], 0, 0
    base_depth = len(root.parts)
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
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
            ext = "".join(p.suffixes[-2:]).lower() if fn.lower().endswith(".gain.eer") else p.suffix.lower() or "(none)"
            try:
                st = p.stat()
            except OSError:
                continue
            counts[ext] += 1
            sizes[ext] += st.st_size
            if st.st_mtime > newest[0]:
                newest = (st.st_mtime, str(p.relative_to(root)))
            low = fn.lower()
            if "gain" in low or low.endswith((".gain", ".dm4")):
                gains.append(str(p.relative_to(root)))
            elif p.suffix.lower() in MOVIE_EXTS and ("frame" in low or "movie" in low or p.suffix.lower() in (".tif", ".tiff", ".eer")):
                movies += 1
        if truncated:
            break
    out = {
        "folder": str(root),
        "files_scanned": min(seen, limit),
        "by_extension": {k: {"count": counts[k], "size": _size(sizes[k])} for k, _ in counts.most_common(12)},
        "likely_movies": movies,
        "gain_references_found": gains[:10],
        "newest_file": newest[1] or None,
        "newest_modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(newest[0])) if newest[0] else None,
    }
    problems = []
    if movies and not gains:
        problems.append("Movies found but no gain reference (no file with 'gain' in its name, .gain or .dm4).")
    if len(gains) > 1:
        problems.append(f"{len(gains)} gain references found; make sure each session uses the right one.")
    if truncated:
        problems.append(f"Stopped after {limit} files; narrow the folder or pattern.")
    out["problems"] = problems
    return out
