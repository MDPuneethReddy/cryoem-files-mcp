"""STAR and SerialEM mdoc summaries, with clear errors for empty or malformed files."""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np

from .formats import FileProblem, check_basic, plural


def _r(x) -> float:
    return float(f"{float(x):.5g}")


def _num_summary(col) -> tuple[dict, int]:
    """Numeric summary + count of values that are not numbers (text or blanks in a number column)."""
    import pandas as pd

    v = pd.to_numeric(col, errors="coerce")
    bad = int(v.isna().sum())
    v = v.to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {}, bad
    q = np.percentile(v, [5, 50, 95])
    return {"min": _r(v.min()), "p5": _r(q[0]), "median": _r(q[1]), "p95": _r(q[2]), "max": _r(v.max())}, bad


KEY_COLUMNS = {
    "rlnDefocusU": "defocus_U_A",
    "rlnDefocusV": "defocus_V_A",
    "rlnCtfMaxResolution": "ctf_max_resolution_A",
    "rlnCtfFigureOfMerit": "ctf_figure_of_merit",
    "rlnAccumMotionTotal": "total_motion_A",
    "rlnMaxValueProbDistribution": "max_prob",
}

OPTICS_COLUMNS = {
    "rlnOpticsGroupName": "name",
    "rlnImagePixelSize": "image_pixel_size_A",
    "rlnMicrographPixelSize": "micrograph_pixel_size_A",
    "rlnMicrographOriginalPixelSize": "original_pixel_size_A",
    "rlnVoltage": "voltage_kV",
    "rlnSphericalAberration": "cs_mm",
    "rlnAmplitudeContrast": "amplitude_contrast",
    "rlnImageSize": "box_size_px",
}


def _text(path: Path, what: str) -> str:
    check_basic(path)
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise FileProblem(path, f"contains binary data, not {what} text.",
                          cause="the file is corrupted or is not a text file", fix="re-copy it or regenerate it")
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise FileProblem(path, f"contains only whitespace; no {what} content.", cause="the writing program failed")
    return text


def _loop_row_problems(text: str) -> list[str]:
    """Find data rows whose field count differs from the loop's column count (pandas silently pads short rows)."""
    problems = []
    block, cols, in_loop, data_started = None, 0, False, False
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("data_"):
            block, cols, in_loop, data_started = s, 0, False, False
            continue
        if s == "loop_":
            in_loop, cols, data_started = True, 0, False
            continue
        if in_loop and s.startswith("_"):
            if data_started:  # a key after data: new implicit section
                in_loop = False
                continue
            cols += 1
            continue
        if in_loop and cols:
            data_started = True
            n = len(s.split())
            if n != cols:
                problems.append(f"{block} line {ln}: {n} values but {cols} columns")
                if len(problems) >= 5:
                    break
    return problems


def summarize_star(path: Path) -> dict:
    import starfile

    text = _text(path, "STAR")
    if "data_" not in text:
        raise FileProblem(path, "has no 'data_' block, so it is not a STAR file (or it is empty/cut off at the start).",
                          fix="check that this is the right file; regenerate it from RELION/CryoSPARC")
    row_problems = _loop_row_problems(text)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            blocks = starfile.read(path, always_dict=True)
    except Exception as e:
        msg = str(e).strip().splitlines()[0][:200]
        if row_problems:
            what = f"is malformed: rows with the wrong number of values: {'; '.join(row_problems)}."
        else:
            what = f"is malformed and could not be parsed ({type(e).__name__}: {msg})."
        raise FileProblem(path, what,
                          cause="a row has a different number of values than the columns, or the file was cut off mid-write",
                          fix="fix or remove the listed rows, or regenerate the file") from None
    if not blocks:
        raise FileProblem(path, "has 'data_' text but no readable blocks (no loop or key/value data).",
                          cause="the file is truncated or malformed", fix="regenerate it")
    out: dict = {"file": path.name, "blocks": {}}
    problems = list(row_problems and [f"rows with the wrong number of values (they were padded with blanks): {'; '.join(row_problems)}"])
    for name, df in blocks.items():
        label = name or "(unnamed)"
        if not hasattr(df, "columns"):
            out["blocks"][label] = {"type": "key-value", "keys": list(df)[:15]}
            continue
        info: dict = {"rows": int(len(df)), "columns": len(df.columns)}
        if len(df) == 0:
            problems.append(f"block '{label}' has columns but no rows")
        if name == "optics":
            groups = []
            for _, row in df.head(8).iterrows():
                g = {}
                for c, key in OPTICS_COLUMNS.items():
                    if c in df.columns:
                        val = row[c]
                        g[key] = val.item() if hasattr(val, "item") else val
                groups.append(g)
            info["optics_groups"] = groups
        else:
            stats = {}
            for c, key in KEY_COLUMNS.items():
                if c in df.columns:
                    summary, bad = _num_summary(df[c])
                    if summary:
                        stats[key] = summary
                    if bad:
                        problems.append(f"block '{label}', column {c}: {bad} of {len(df)} values {'is' if bad == 1 else 'are'} missing or not a number")
            if stats:
                info["stats"] = stats
            if "rlnClassNumber" in df.columns:
                vc = df["rlnClassNumber"].value_counts().sort_index()
                info["particles_per_class"] = {str(k): int(v) for k, v in vc.head(50).items()}
            if "rlnMicrographName" in df.columns and "rlnImageName" in df.columns:
                info["micrographs"] = int(df["rlnMicrographName"].nunique())
            if "rlnOpticsGroup" in df.columns:
                info["optics_group_counts"] = {str(k): int(v) for k, v in df["rlnOpticsGroup"].value_counts().items()}
            info["column_names"] = [c for c in df.columns if c not in KEY_COLUMNS][:20]
            null_rows = int(df.isna().any(axis=1).sum())
            if null_rows and not row_problems:
                problems.append(f"block '{label}': {plural(null_rows, 'row')} with empty values")
        out["blocks"][label] = info
    if "optics" not in blocks and any(k for k in blocks):
        out["notes"] = ["no optics block: a RELION 3.0-style file; RELION 3.1+ expects one"]
    if problems:
        out["problems"] = problems
    return out


def parse_mdoc(path: Path) -> dict:
    import mdocfile

    text = _text(path, "mdoc")
    if "[ZValue" not in text and "[FrameSet" not in text:
        raise FileProblem(path, "has no [ZValue = n] sections, so it is not a SerialEM mdoc (or it is truncated at the start).",
                          fix="check that this is the right file")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = mdocfile.read(path)
    except Exception as e:
        bad = [f"line {i}: '{ln.strip()[:40]}'" for i, ln in enumerate(text.splitlines(), 1)
               if ln.strip() and not ln.strip().startswith("[") and "=" not in ln][:3]
        where = f" Lines that are not 'key = value': {'; '.join(bad)}." if bad else ""
        raise FileProblem(path, f"is malformed and could not be parsed ({type(e).__name__}: {e}).{where}",
                          cause="damaged or hand-edited file", fix="fix those lines or re-export the mdoc from SerialEM") from None
    if len(df) == 0:
        raise FileProblem(path, "has no tilt/frame sections with data.", cause="truncated file")
    out: dict = {"file": path.name, "sections": int(len(df))}
    problems = []
    odd = [f"line {i}: '{ln.strip()[:40]}'" for i, ln in enumerate(text.splitlines(), 1)
           if ln.strip() and not ln.strip().startswith("[") and "=" not in ln]
    if odd:
        problems.append(f"{plural(len(odd), 'line')} not in 'key = value' form {'was' if len(odd) == 1 else 'were'} ignored ({'; '.join(odd[:3])}): "
                        "the file may be damaged or hand-edited")
    if "TiltAngle" in df.columns:
        t = np.asarray(df["TiltAngle"], dtype=float)
        missing = int((~np.isfinite(t)).sum())
        if missing:
            problems.append(f"{missing} of {plural(len(t), 'section')} {'has' if missing == 1 else 'have'} no TiltAngle")
        t = t[np.isfinite(t)]
        if t.size:
            st = np.sort(t)
            steps = np.diff(st)
            out["tilt_range_deg"] = [_r(st[0]), _r(st[-1])]
            out["tilt_step_deg"] = _r(np.median(steps)) if steps.size else None
            out["acquisition_order_first5"] = [_r(x) for x in t[:5]]
            dup = int(len(t) - len(np.unique(np.round(t, 2))))
            if dup:
                problems.append(f"{plural(dup, 'duplicate tilt angle')} (repeated or failed exposures?)")
    else:
        problems.append("no TiltAngle entries: not a tilt series, or the mdoc is incomplete")
    if "ExposureDose" in df.columns:
        d = np.asarray(df["ExposureDose"], dtype=float)
        out["total_dose_e_per_A2"] = _r(np.nansum(d))
        out["dose_per_tilt"] = _r(np.nanmedian(d))
        if np.nanmax(d) == 0:
            problems.append("ExposureDose is 0 for every section: dose was not calibrated, so dose weighting will be wrong")
    for col, key in (("PixelSpacing", "pixel_size_A"), ("NumSubFrames", "frames_per_tilt"), ("ExposureTime", "exposure_s"),
                     ("Magnification", "magnification"), ("Defocus", "defocus_um")):
        if col in df.columns:
            v = np.asarray(df[col], dtype=float)
            v = v[np.isfinite(v)]
            if v.size:
                out[key] = _r(np.median(v)) if col != "Defocus" else [_r(v.min()), _r(v.max())]
    if "SubFramePath" in df.columns:
        names = [str(x).replace("\\", "/").split("/")[-1] for x in df["SubFramePath"].dropna()]
        out["subframe_files_first3"] = names[:3]
    if problems:
        out["problems"] = problems
    return out
