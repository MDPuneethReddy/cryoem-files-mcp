"""Gain reference tools: inspect, find the right orientation, convert to MRC."""

from __future__ import annotations

from pathlib import Path

import mrcfile
import numpy as np

from .formats import Movie, read_2d, sample_stats
from .paths import output_path

# The 8 distinct ways a gain can be rotated/flipped relative to the movie.
# Each is: rotate counter-clockwise by k*90 degrees (numpy rot90), then optionally flip rows (upside down).
VARIANTS = [(k, flip) for k in range(4) for flip in (False, True)]


def variant_name(k: int, flip: bool) -> str:
    return f"rot{k * 90}" + ("+flip_y" if flip else "")


def transform(g: np.ndarray, k: int, flip: bool) -> np.ndarray:
    out = np.rot90(g, k)
    return np.flipud(out) if flip else out


def bin2(a: np.ndarray) -> np.ndarray:
    h, w = (a.shape[0] // 2) * 2, (a.shape[1] // 2) * 2
    return a[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def inspect_gain(path: Path) -> dict:
    g, fmt = read_2d(path)
    finite = np.isfinite(g)
    zero = (g == 0) & finite
    good = g[finite & ~zero]
    mean = float(good.mean()) if good.size else float("nan")
    result = {
        "file": path.name,
        "format": fmt,
        "shape_yx": list(g.shape),
        "stats": sample_stats(g),
        "zero_or_defect_pixels_pct": round(100 * zero.mean(), 4),
        "non_finite_pixels": int((~finite).sum()),
        "normalised_near_1": bool(good.size and 0.5 < mean < 2.0),
    }
    hints = []
    if g.shape in ((8192, 8192), (4096, 4096)) and fmt in ("tiff", "bigtiff", "eer", "mrc"):
        hints.append(
            "Square 4K/8K gain: likely Falcon. Falcon TIFF .gain files are multiplicative; "
            "older Falcon MRC gains for EER are divisive (RELION divides by an MRC gain for EER). "
            "Run check_gain_orientation against a movie to confirm the convention."
        )
    if g.shape == (8192, 8192):
        hints.append("8K gain: CryoSPARC expects a 4096x4096 gain for EER; use convert_gain with bin=2.")
    if fmt in ("dm4", "dm3"):
        hints.append("Gatan gain (K2/K3): multiplicative. Orientation often differs from TIFF movies; check it.")
    if not result["normalised_near_1"]:
        hints.append("Mean is far from 1: this may not be a normalised gain reference.")
    result["hints"] = hints
    return result


def _highpass(a: np.ndarray, r: int = 8) -> np.ndarray:
    """Subtract a box-blurred copy (radius r) to remove illumination gradients, keeping pixel-level pattern."""
    a = a.astype(np.float64)
    pad = np.pad(a, r + 1, mode="reflect")
    c = pad.cumsum(0).cumsum(1)
    size = 2 * r + 1
    s = c[size:, size:] - c[:-size, size:] - c[size:, :-size] + c[:-size, :-size]
    blur = s[: a.shape[0], : a.shape[1]] / size**2
    return a - blur


def _center_crop(a: np.ndarray, size: int) -> np.ndarray:
    h, w = a.shape
    ch, cw = min(size, h), min(size, w)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return a[y0 : y0 + ch, x0 : x0 + cw]


def check_orientation(gain_path: Path, movie_path: Path, n_frames: int = 20, crop: int = 2048) -> dict:
    """Score all 8 rotate/flip variants of the gain against the summed raw movie frames.

    Raw (not gain-normalised) frames carry the detector's fixed pixel pattern. The right variant
    correlates strongly with it (negative for multiplicative gains, positive for divisive ones);
    wrong variants correlate near zero.
    """
    g, _ = read_2d(gain_path)
    with Movie(movie_path) as mv:
        if mv.dtype.startswith("float"):
            note = "Movie frames are float: they may already be gain-normalised, so the check can be weak."
        else:
            note = None
        s, used = mv.sum_frames(n_frames)
        movie_shape = mv.shape

    scaling = None
    results = []
    for k, flip in VARIANTS:
        t = transform(g, k, flip)
        mv_sum = s
        if t.shape != movie_shape:
            if (t.shape[0] == 2 * movie_shape[0]) and (t.shape[1] == 2 * movie_shape[1]):
                t = bin2(t)
                scaling = "gain binned 2x to match movie"
            elif (movie_shape[0] == 2 * t.shape[0]) and (movie_shape[1] == 2 * t.shape[1]):
                mv_sum = bin2(s)
                scaling = "movie sum binned 2x to match gain (super-resolution movie)"
            else:
                results.append({"variant": variant_name(k, flip), "r": None, "skipped": "shape mismatch"})
                continue
        a = _center_crop(mv_sum, crop)
        b = _center_crop(t, crop)
        mask = np.isfinite(b) & (b > 0)
        ha, hb = _highpass(a)[mask], _highpass(np.where(mask, b, np.nanmean(b[mask])))[mask]
        ha, hb = ha - ha.mean(), hb - hb.mean()
        denom = np.sqrt((ha**2).sum() * (hb**2).sum())
        r = float((ha * hb).sum() / denom) if denom > 0 else 0.0
        results.append({"variant": variant_name(k, flip), "rot90_ccw": k, "flip_y": flip, "r": round(r, 4)})

    scored = [x for x in results if x.get("r") is not None]
    if not scored:
        return {
            "error": f"Gain shape {list(g.shape)} does not match movie frame shape {list(movie_shape)} in any orientation",
        }
    scored.sort(key=lambda x: abs(x["r"]), reverse=True)
    best, runner = scored[0], (scored[1] if len(scored) > 1 else None)
    margin = abs(best["r"]) - (abs(runner["r"]) if runner else 0.0)
    ratio = abs(best["r"]) / max(abs(runner["r"]), 1e-6) if runner else float("inf")
    confidence = "high" if (abs(best["r"]) > 0.1 and ratio > 3) else "medium" if ratio > 1.5 else "low"
    convention = "multiply (raw x gain)" if best["r"] < 0 else "divide (raw / gain)"
    out = {
        "best": best["variant"],
        "rot90_ccw": best["rot90_ccw"],
        "flip_y": best["flip_y"],
        "correlation": best["r"],
        "runner_up": runner and {"variant": runner["variant"], "r": runner["r"]},
        "confidence": confidence,
        "margin": round(margin, 4),
        "gain_convention": convention,
        "frames_summed": used,
        "all_variants": {x["variant"]: x.get("r") for x in results},
        "next_step": (
            f"convert_gain with rotate={best['rot90_ccw']}, flip='{'y' if best['flip_y'] else 'none'}'"
            + (", invert=true" if best["r"] > 0 else "")
            + " writes a multiplicative MRC gain already matched to the movie; then run MotionCor with "
            "-RotGain 0 -FlipGain 0 (and no -InvGain)."
        ),
    }
    if scaling:
        out["scaling"] = scaling
    if note:
        out["note"] = note
    if confidence == "low":
        out["warning"] = "No variant stands out. Use raw (non-gain-normalised) frames and more frames (n_frames=50+)."
    return out


def convert_gain(
    gain_path: Path,
    out: str | None = None,
    rotate: int = 0,
    flip: str = "none",
    invert: bool = False,
    bin: int = 1,
    pixel_size: float | None = None,
    overwrite: bool = False,
) -> dict:
    """Write the gain as a float32 MRC, applying rotate (ccw, x90) then flip, optional inversion and binning."""
    if rotate not in (0, 1, 2, 3):
        raise ValueError("rotate must be 0, 1, 2 or 3 (multiples of 90 degrees counter-clockwise)")
    if flip not in ("none", "y", "x"):
        raise ValueError("flip must be 'none', 'y' (upside down) or 'x' (left-right)")
    if bin not in (1, 2):
        raise ValueError("bin must be 1 or 2")
    g, fmt = read_2d(gain_path)
    applied = []
    g = np.rot90(g, rotate)
    if rotate:
        applied.append(f"rotated {rotate * 90} deg counter-clockwise")
    if flip == "y":
        g = np.flipud(g)
        applied.append("flipped upside down")
    elif flip == "x":
        g = np.fliplr(g)
        applied.append("flipped left-right")
    if invert:
        safe = np.isfinite(g) & (g != 0)
        inv = np.zeros_like(g, dtype=np.float32)
        inv[safe] = 1.0 / g[safe]
        g = inv
        applied.append("inverted (1/gain; zero/defect pixels stay 0)")
    if bin == 2:
        g = bin2(g)
        applied.append("binned 2x (mean)")
    g = np.ascontiguousarray(g, dtype=np.float32)

    dst = output_path(gain_path, out, ".mrc", overwrite=overwrite)
    with mrcfile.new(dst, overwrite=True) as m:
        m.set_data(g)
        if pixel_size:
            m.voxel_size = (pixel_size, pixel_size, pixel_size)
    return {
        "output": str(dst),
        "input_format": fmt,
        "shape_yx": list(g.shape),
        "applied": applied or ["no transform (format conversion only)"],
        "stats": sample_stats(g),
        "use_with_motioncor": "-Gain <output> -RotGain 0 -FlipGain 0" + ("" if invert else " (multiplicative)"),
    }
