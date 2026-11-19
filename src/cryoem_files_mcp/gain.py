"""Gain reference tools: inspect, find the right orientation, convert to MRC."""

from __future__ import annotations

from pathlib import Path

import mrcfile
import numpy as np

from .formats import FORMAT_NAMES, FileProblem, Movie, data_warnings, read_2d, sample_stats
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
        "format": FORMAT_NAMES[fmt],
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
    problems = data_warnings(g, "gain")
    if good.size and (good < 0).any():
        problems.append(f"{100 * (good < 0).mean():.3g}% of gain pixels are negative; a gain reference should be positive")
    if result["zero_or_defect_pixels_pct"] > 5:
        problems.append(f"{result['zero_or_defect_pixels_pct']}% of pixels are zero: far more defects than normal (<1%)")
    result["problems"] = problems
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


def _detrend_lines(a: np.ndarray) -> np.ndarray:
    """Remove per-row and per-column offsets. Readout stripes are shared by many detectors; left in, they make
    unrelated gains correlate (few independent values), which would break the z = |r| * sqrt(n) null model."""
    a = a - a.mean(axis=0, keepdims=True)
    return a - a.mean(axis=1, keepdims=True)


def _center_crop(a: np.ndarray, size: int) -> np.ndarray:
    h, w = a.shape
    ch, cw = min(size, h), min(size, w)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return a[y0 : y0 + ch, x0 : x0 + cw]


# Orientation scoring is a significance test. For a gain that has nothing to do with the movie, the Pearson
# correlation r over n independent pixels is ~ N(0, 1/sqrt(n)), so z = |r| * sqrt(n) is ~ |N(0, 1)|.
# The best of 8 variants exceeds 6 by chance with probability ~ 8 * 2e-9; real data (residual structure,
# shared defect rows) is less ideal than that, hence the margin. Calibration: scripts/calibrate_orientation.py.
Z_MATCH = 6.0  # below this the best variant is indistinguishable from chance: no match
Z_HIGH = 10.0  # best variant at least this significant ...
GAP_HIGH = 6.0  # ... and this far (in z units) ahead of the runner-up -> high confidence
GAP_MEDIUM = 3.0  # below this the top two variants cannot be told apart -> low (ambiguous)


def _robust_clip(a: np.ndarray, k: float = 5.0) -> np.ndarray:
    """Clip outliers (hot/dead pixels) to k robust standard deviations so a few pixels cannot dominate r."""
    lo, med, hi = np.percentile(a, [15.87, 50, 84.13])
    sd = (hi - lo) / 2
    if not np.isfinite(sd) or sd <= 0:
        return a
    return np.clip(a, med - k * sd, med + k * sd)


def check_orientation(gain_path: Path, movie_path: Path, n_frames: int = 20, crop: int = 2048) -> dict:
    """Score all 8 rotate/flip variants of the gain against the summed raw movie frames.

    Raw (not gain-normalised) frames carry the detector's fixed pixel pattern. The right variant
    correlates strongly with it (negative for multiplicative gains, positive for divisive ones);
    wrong variants, and gains from another detector, correlate only at chance level.
    """
    if not 1 <= n_frames <= 10_000:
        raise ValueError("n_frames must be at least 1 and at most 10000")
    g, _ = read_2d(gain_path)
    bad = data_warnings(g, "gain")
    if any("every" in b or "same value" in b for b in bad):
        raise FileProblem(gain_path, f"cannot be used for an orientation check: {bad[0]}.")
    with Movie(movie_path) as mv:
        if mv.dtype.startswith("float"):
            note = "Movie frames are float: they may already be gain-normalised, so the check can be weak."
        else:
            note = None
        s, used = mv.sum_frames(n_frames)
        movie_shape = mv.shape
    if not np.isfinite(s).all():
        raise FileProblem(movie_path, "frames contain NaN or infinite values; the orientation check needs raw counts.")
    if s.max() == s.min():
        raise FileProblem(movie_path, f"the first {used} frames are blank (every pixel = {s.max():g}).",
                          cause="empty or failed exposure", fix="pick another movie from the session")

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
        n = int(mask.sum())
        if n < 16:
            results.append({"variant": variant_name(k, flip), "r": None, "skipped": "too few valid gain pixels"})
            continue
        ha = _robust_clip(_detrend_lines(_highpass(a))[mask])
        hb = _robust_clip(_detrend_lines(_highpass(np.where(mask, b, np.nanmean(b[mask]))))[mask])
        ha, hb = ha - ha.mean(), hb - hb.mean()
        denom = np.sqrt((ha**2).sum() * (hb**2).sum())
        r = float((ha * hb).sum() / denom) if denom > 0 else 0.0
        results.append({"variant": variant_name(k, flip), "rot90_ccw": k, "flip_y": flip, "r": round(r, 4),
                        "z": round(abs(r) * np.sqrt(n), 1), "n": n})

    scored = [x for x in results if x.get("r") is not None]
    if not scored:
        raise FileProblem(
            gain_path,
            f"is {g.shape[1]}x{g.shape[0]} pixels but the movie frames are {movie_shape[1]}x{movie_shape[0]}; "
            "they do not match in any orientation (or with 2x binning).",
            cause="the gain belongs to a different detector, mode (counting vs super-resolution) or session",
            fix="use the gain reference recorded with this session",
        )
    scored.sort(key=lambda x: x["z"], reverse=True)
    best, runner = scored[0], (scored[1] if len(scored) > 1 else None)
    z_best = best["z"]
    z_runner = runner["z"] if runner else 0.0
    gap = round(z_best - z_runner, 1)
    if z_best < Z_MATCH:
        confidence = "no_match"
    elif gap < GAP_MEDIUM:
        confidence = "low"
    elif z_best >= Z_HIGH and gap >= GAP_HIGH:
        confidence = "high"
    else:
        confidence = "medium"
    out: dict = {"confidence": confidence}
    if confidence != "no_match":
        out.update({"best": best["variant"], "rot90_ccw": best["rot90_ccw"], "flip_y": best["flip_y"],
                    "correlation": best["r"]})
    out.update({
        "z_best": z_best,
        "z_runner_up": z_runner,
        "z_gap": gap,
        "runner_up": runner and {"variant": runner["variant"], "r": runner["r"], "z": runner["z"]},
        "pixels_compared": best["n"],
        "frames_summed": used,
        "all_variants": {x["variant"]: ({"r": x["r"], "z": x["z"]} if x.get("r") is not None else x.get("skipped"))
                         for x in results},
        "thresholds": f"no_match if z_best < {Z_MATCH:g}; high if z_best >= {Z_HIGH:g} and z_gap >= {GAP_HIGH:g}; "
                      f"low if z_gap < {GAP_MEDIUM:g}; otherwise medium (z = |r| * sqrt(pixels compared))",
    })
    if confidence == "no_match":
        hint = (" The movie frames are float, so they are probably already gain-normalised; use the raw movies."
                if note else "")
        out["problem"] = str(FileProblem(
            gain_path,
            f"does not match this movie in any orientation: the best variant ({best['variant']}) scores z={z_best:g}, "
            f"below {Z_MATCH:g}, which is what an unrelated gain gives by chance.{hint}",
            cause="the gain belongs to a different detector or session, or the movie is already gain-normalised",
            fix="find the gain reference recorded with these movies (same session folder), and check against a raw, "
                "integer-count movie; with very low dose, raise n_frames",
        ))
    else:
        convention = "multiply (raw x gain)" if best["r"] < 0 else "divide (raw / gain)"
        out["gain_convention"] = convention
        step = (f"convert_gain with rotate={best['rot90_ccw']}, flip='{'y' if best['flip_y'] else 'none'}'"
                + (", invert=true" if best["r"] > 0 else "")
                + " writes a multiplicative MRC gain already matched to the movie; then run MotionCor with "
                  "-RotGain 0 -FlipGain 0 (and no -InvGain).")
        if confidence == "high":
            out["next_step"] = step
        elif confidence == "medium":
            out["next_step"] = "Probably: " + step
            out["warning"] = (f"The match is real (z={z_best:g}) but {best['variant']} is only {gap:g} z ahead of "
                              f"{runner['variant'] if runner else 'the next variant'}. Re-run with more frames "
                              "(n_frames=50+) to confirm before converting.")
        else:
            out["warning"] = (f"The gain matches the movie (z={z_best:g}) but {best['variant']} and "
                              f"{runner['variant'] if runner else '?'} score almost the same (gap {gap:g}), so the "
                              "orientation cannot be decided. Use more frames (n_frames=50+) or another raw movie; "
                              "do not guess an orientation.")
    if scaling:
        out["scaling"] = scaling
    if note:
        out["note"] = note
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
    """Write the gain as a float32 MRC, applying rotate (ccw, x90), then flip, then 2x binning, then inversion."""
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
    # bin before inverting: a divisive gain describes counts, so 2x2 pixels add up in the gain's own convention;
    # inverting first would turn one defect pixel into a large error in its whole binned pixel (QA M2)
    if bin == 2:
        g = bin2(g)
        applied.append("binned 2x (mean)")
    if invert:
        safe = np.isfinite(g) & (g != 0)
        inv = np.zeros_like(g, dtype=np.float32)
        inv[safe] = 1.0 / g[safe]
        g = inv
        applied.append("inverted (1/gain; zero/defect pixels stay 0)")
    g = np.ascontiguousarray(g, dtype=np.float32)

    for w in data_warnings(g, "gain"):
        if "every" in w or "same value" in w:
            raise FileProblem(gain_path, f"cannot be converted: {w}.")
    try:
        dst = output_path(gain_path, out, ".mrc", overwrite=overwrite)
    except PermissionError as e:
        raise FileProblem(gain_path, f"output folder is not writable ({e.filename or e}).",
                          fix="pass out= pointing to a folder you can write to") from None
    tmp = dst.with_name(dst.name + ".partial")
    try:
        with mrcfile.new(tmp, overwrite=True) as m:
            m.set_data(g)
            if pixel_size:
                m.voxel_size = (pixel_size, pixel_size, pixel_size)
        tmp.replace(dst)  # never leave a half-written gain behind under the real name
    except OSError as e:
        tmp.unlink(missing_ok=True)
        reason = "permission denied" if isinstance(e, PermissionError) else (e.strerror or str(e))
        raise FileProblem(dst, f"could not be written: {reason}.",
                          cause="no write permission or the disk is full", fix="pass out= pointing to a writable folder with free space") from None
    return {
        "output": str(dst),
        "input_format": FORMAT_NAMES[fmt],
        "shape_yx": list(g.shape),
        "applied": applied or ["no transform (format conversion only)"],
        "stats": sample_stats(g),
        "use_with_motioncor": "-Gain <output> -RotGain 0 -FlipGain 0" + ("" if invert else " (multiplicative)"),
    }
