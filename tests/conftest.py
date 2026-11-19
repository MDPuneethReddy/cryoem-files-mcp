import numpy as np
import pytest
import tifffile

from cryoem_files_mcp.gain import transform
from cryoem_files_mcp.paths import set_roots


@pytest.fixture(autouse=True)
def _roots(tmp_path):
    set_roots([str(tmp_path)])
    yield


def make_gain(shape=(256, 256), seed=0):
    rng = np.random.default_rng(seed)
    g = (1 + 0.08 * rng.standard_normal(shape)).astype(np.float32)
    g[5, 7] = 0  # a defect pixel
    return g


def make_movie(path, gain_file, k, flip, convention="multiply", frames=20, dose=3.0, seed=1, scale_to=None):
    """Raw counting movie whose fixed pattern equals the gain file after transform(k, flip)."""
    rng = np.random.default_rng(seed)
    m = transform(gain_file, k, flip).astype(np.float64)
    if scale_to == "half":
        h, w = m.shape
        m = m.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    m = np.where(m > 0, m, 1.0)
    lam = dose / m if convention == "multiply" else dose * m
    stack = rng.poisson(lam, size=(frames, *m.shape)).astype(np.uint8)
    tifffile.imwrite(path, stack, compression="zlib")
    return path
