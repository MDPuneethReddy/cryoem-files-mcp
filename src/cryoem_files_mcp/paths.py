"""Path safety: every tool may only touch files under the configured root folders."""

from __future__ import annotations

import os
from pathlib import Path

_roots: list[Path] = []


class PathNotAllowed(ValueError):
    pass


def set_roots(roots: list[str]) -> list[Path]:
    """Configure allowed roots. With none given, the current working directory is used."""
    global _roots
    chosen = roots or [os.getcwd()]
    _roots = [Path(r).expanduser().resolve() for r in chosen]
    return _roots


def get_roots() -> list[Path]:
    if not _roots:
        set_roots([])
    return _roots


def is_allowed(p: Path) -> bool:
    """True if an already-resolved path lies inside one of the allowed roots."""
    return any(p == r or r in p.parents for r in get_roots())


def display(p: Path) -> str:
    """Show a path relative to its root (never the absolute path)."""
    for r in get_roots():
        if p == r:
            return "."
        if r in p.parents:
            return str(p.relative_to(r))
    return p.name


def resolve(path: str, must_exist: bool = True) -> Path:
    """Resolve a user path (relative paths are relative to the first root) and check it is allowed.

    Error messages repeat the path as the caller gave it: they never reveal where a symlink points.
    """
    roots = get_roots()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = roots[0] / p
    p = p.resolve()
    if not is_allowed(p):
        allowed = ", ".join(str(r) for r in roots)
        raise PathNotAllowed(
            f"{path}: is outside the allowed folders ({allowed}). Likely cause: an absolute path, '..', or a "
            "symlink that points outside them. Fix: use a path inside the allowed folders, or start the server "
            "with --root for the folder you need."
        )
    if must_exist and not p.exists():
        raise FileNotFoundError(
            f"{display(p)}: does not exist. Likely cause: wrong path, or the file was moved or deleted. "
            f"Fix: check the name; relative paths start at {roots[0]}."
        )
    return p


def output_path(src: Path, out: str | None, suffix: str = ".mrc", overwrite: bool = False) -> Path:
    """Pick a safe output path next to the source (or at `out`), never equal to the source.

    Uses the real extension (case-insensitive) rather than a substring replace, so
    'D.TIF' -> 'D.mrc' and 'tiff_gain.tiff' -> 'tiff_gain.mrc'.
    """
    if out:
        dst = resolve(out, must_exist=False)
        if dst.is_dir():
            dst = dst / (_stem(src) + suffix)
    else:
        dst = src.with_name(_stem(src) + suffix)
    if dst == src:
        dst = src.with_name(_stem(src) + "_converted" + suffix)
    if dst.exists() and not overwrite:
        raise FileExistsError(f"{display(dst)}: already exists. Fix: pass overwrite=true to replace it, or choose another out=.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


def _stem(p: Path) -> str:
    name = p.name
    # strip one known extension, plus '.gain' in double extensions like 'x.gain.eer' -> 'x_gain'
    lower = name.lower()
    for ext in (".gain.eer", ".gain.tiff", ".gain.tif", ".gain.mrc"):
        if lower.endswith(ext):
            return name[: -len(ext)] + "_gain"
    return p.stem
