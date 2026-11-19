"""Calibrate the check_gain_orientation z thresholds on simulated data.

Run: python3 scripts/calibrate_orientation.py [--full]   (--full adds the 8K Falcon case, ~1 min, ~3 GB RAM)

(a) unrelated gain of the same size: must never give high/medium (no convert recommendation)
(b) correct gain, 2% and 1% pixel variation, 0.5 e/px/frame, 20 frames, 512x512: must be high
(c) Falcon-style 8K gain (4% variation) against a 4K counting movie (2x2 summed): must be high
"""

import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cryoem_files_mcp.gain import VARIANTS, check_orientation, transform  # noqa: E402
from cryoem_files_mcp.paths import set_roots  # noqa: E402

d = Path(tempfile.mkdtemp())
set_roots([str(d)])


def gain(shape, var, seed):
    rng = np.random.default_rng(seed)
    g = (1 + var * rng.standard_normal(shape)).astype(np.float32)
    g[rng.integers(0, shape[0], 20), rng.integers(0, shape[1], 20)] = 0
    return g


def movie(path, g, k, flip, dose, frames, seed, bin_to=None, dtype=np.uint8):
    rng = np.random.default_rng(seed)
    m = transform(g, k, flip).astype(np.float64)
    m = np.where(m > 0, m, 1.0)
    lam = dose / m  # multiplicative gain: raw counts ~ 1/gain
    if bin_to:  # counting movie from a super-resolution sensor: 2x2 sum
        h, w = lam.shape
        lam = lam.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3)) / 4
    stack = np.stack([rng.poisson(lam).astype(dtype) for _ in range(frames)])
    tifffile.imwrite(path, stack)
    return path


def run(name, gshape, var, dose, frames, trials, related, bin_to=None):
    verdicts, zs, gaps, correct = Counter(), [], [], 0
    for t in range(trials):
        g_true = gain(gshape, var, 1000 + t)
        k, flip = VARIANTS[t % 8]
        movie(d / "m.tif", g_true, k, flip, dose, frames, 2000 + t, bin_to)
        g_used = g_true if related else gain(gshape, var, 5000 + t)
        tifffile.imwrite(d / "g.tif", g_used)
        r = check_orientation(d / "g.tif", d / "m.tif", n_frames=frames)
        verdicts[r["confidence"]] += 1
        zs.append(r["z_best"])
        gaps.append(r["z_gap"])
        if related and r.get("rot90_ccw") is not None:
            correct += np.array_equal(transform(g_true, r["rot90_ccw"], r["flip_y"]), transform(g_true, k, flip))
    print(f"{name}: {dict(verdicts)}  z_best min/median/max = {min(zs):.1f}/{np.median(zs):.1f}/{max(zs):.1f}  "
          f"gap min = {min(gaps):.1f}" + (f"  correct {correct}/{trials}" if related else ""))
    return verdicts


v = run("(a) unrelated 256x256, 8% var, 3 e/px/frame", (256, 256), 0.08, 3.0, 20, 60, related=False)
v2 = run("(a) unrelated 512x512, 2% var, 0.5 e/px/frame", (512, 512), 0.02, 0.5, 20, 60, related=False)
v3 = run("(b) correct 512x512, 2% var, 0.5 e/px/frame", (512, 512), 0.02, 0.5, 20, 16, related=True)
v4 = run("(b) correct 512x512, 1% var, 0.5 e/px/frame", (512, 512), 0.01, 0.5, 20, 16, related=True)
v5 = run("(c-small) correct 1024 gain vs 512 movie, 4% var, 0.5 e/px/frame", (1024, 1024), 0.04, 0.5, 20, 8,
         related=True, bin_to=2)
if "--full" in sys.argv:
    run("(c) Falcon 8K gain vs 4K movie, 4% var, 0.5 e/px/frame", (8192, 8192), 0.04, 0.5, 10, 1, related=True, bin_to=2)
assert v["high"] + v["medium"] + v2["high"] + v2["medium"] == 0, "false recommendation on an unrelated gain"
assert v3["high"] == v3.total() and v4["high"] == v4.total() and v5["high"] == v5.total(), "correct match not high"
print("calibration OK")
