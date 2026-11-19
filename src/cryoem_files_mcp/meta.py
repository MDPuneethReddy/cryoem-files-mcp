"""STAR and SerialEM mdoc summaries."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _num_summary(col) -> dict:
    v = np.asarray(col, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {}
    q = np.percentile(v, [5, 50, 95])
    return {"min": _r(v.min()), "p5": _r(q[0]), "median": _r(q[1]), "p95": _r(q[2]), "max": _r(v.max())}


def _r(x) -> float:
    return float(f"{float(x):.5g}")


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


def summarize_star(path: Path) -> dict:
    import starfile

    blocks = starfile.read(path, always_dict=True)
    out = {"file": path.name, "blocks": {}}
    for name, df in blocks.items():
        if not hasattr(df, "columns"):  # single key-value block
            out["blocks"][name or "(unnamed)"] = {"type": "key-value", "keys": list(df)[:15]}
            continue
        info: dict = {"rows": int(len(df)), "columns": len(df.columns)}
        if name == "optics":
            info["optics_groups"] = [
                {OPTICS_COLUMNS[c]: (row[c].item() if hasattr(row[c], "item") else row[c]) for c in OPTICS_COLUMNS if c in df.columns}
                for _, row in df.head(8).iterrows()
            ]
        else:
            stats = {label: _num_summary(df[c]) for c, label in KEY_COLUMNS.items() if c in df.columns}
            if stats:
                info["stats"] = stats
            if "rlnClassNumber" in df.columns:
                vc = df["rlnClassNumber"].value_counts().sort_index()
                info["particles_per_class"] = {int(k): int(v) for k, v in vc.head(50).items()}
            if "rlnMicrographName" in df.columns and len(df) and "rlnImageName" in df.columns:
                info["micrographs"] = int(df["rlnMicrographName"].nunique())
            if "rlnOpticsGroup" in df.columns:
                info["optics_group_counts"] = {int(k): int(v) for k, v in df["rlnOpticsGroup"].value_counts().items()}
            other = [c for c in df.columns if c not in KEY_COLUMNS][:20]
            info["column_names"] = other
        out["blocks"][name or "(unnamed)"] = info
    return out


def parse_mdoc(path: Path) -> dict:
    import mdocfile

    df = mdocfile.read(path)
    out: dict = {"file": path.name, "sections": int(len(df))}
    if "TiltAngle" in df.columns:
        t = np.asarray(df["TiltAngle"], dtype=float)
        t = t[np.isfinite(t)]
        if t.size:
            st = np.sort(t)
            steps = np.diff(st)
            out["tilt_range_deg"] = [_r(st[0]), _r(st[-1])]
            out["tilt_step_deg"] = _r(np.median(steps)) if steps.size else None
            out["acquisition_order_first5"] = [_r(x) for x in t[:5]]
    if "ExposureDose" in df.columns:
        d = np.asarray(df["ExposureDose"], dtype=float)
        out["total_dose_e_per_A2"] = _r(np.nansum(d))
        out["dose_per_tilt"] = _r(np.nanmedian(d))
    for col, key in (("PixelSpacing", "pixel_size_A"), ("NumSubFrames", "frames_per_tilt"), ("ExposureTime", "exposure_s"), ("Magnification", "magnification"), ("Defocus", "defocus_um")):
        if col in df.columns:
            v = np.asarray(df[col], dtype=float)
            v = v[np.isfinite(v)]
            if v.size:
                out[key] = _r(np.median(v)) if col != "Defocus" else [_r(v.min()), _r(v.max())]
    if "SubFramePath" in df.columns:
        names = [str(x).replace("\\", "/").split("/")[-1] for x in df["SubFramePath"].dropna()]
        out["subframe_files_first3"] = names[:3]
    return out
