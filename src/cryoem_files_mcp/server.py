"""MCP server entry point. Run: cryoem-files-mcp --root /data/cryoem"""

from __future__ import annotations

import argparse
import os
import sys
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

import anyio
import anyio.to_thread
from pydantic import Field, ValidationError

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import ToolAnnotations

from . import __version__
from . import gain as gain_mod
from . import inspect as insp
from . import meta
from .paths import get_roots, resolve, set_roots

# Longest a single tool call may run. A read on a stale network mount (NFS/GPFS) can block inside the
# kernel; the call then returns an error after this many seconds instead of hanging the session.
TIMEOUT_S = float(os.environ.get("CRYOEM_MCP_TIMEOUT", "300"))


def _call(fn):
    """Run a tool body; expected problems (bad path, wrong format, file exists) reach the model with their reason."""
    try:
        return fn()
    except (ValueError, OSError) as e:  # PathNotAllowed is a ValueError; FileExistsError/FileNotFoundError are OSError
        raise ToolError(str(e)) from e


async def _run(fn):
    """Run a blocking tool body on a worker thread, giving up after TIMEOUT_S.

    A thread stuck in the kernel (stale mount) cannot be killed; it is abandoned and the client gets an error.
    """
    try:
        with anyio.fail_after(TIMEOUT_S):
            return await anyio.to_thread.run_sync(lambda: _call(fn), abandon_on_cancel=True)
    except TimeoutError:
        raise ToolError(
            f"the call did not finish within {TIMEOUT_S:g} s and was abandoned. Likely cause: a file on a slow or "
            "stale network mount (NFS/GPFS) is not responding, or the request covers a very large amount of data. "
            "Fix: check the mount (ls the folder in a terminal), or narrow the request (fewer frames, a subfolder); "
            "raise the limit with CRYOEM_MCP_TIMEOUT if the data is just large"
        ) from None


def _readable_validation(exc: ValidationError) -> str:
    """One short line per bad argument, without pydantic's type codes and documentation links."""
    parts = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ())) or "arguments"
        msg = e.get("msg", "invalid value")
        if e.get("type") == "missing":
            parts.append(f"{loc} is required")
            continue
        val = e.get("input")
        shown = repr(val) if isinstance(val, (int, float, str, bool)) or val is None else type(val).__name__
        parts.append(f"{loc}: {msg[0].lower() + msg[1:] if msg else msg} (got {shown[:40]})")
    return "invalid arguments: " + "; ".join(parts)


class _Server(MCPServer):
    async def call_tool(self, name, arguments, context=None):
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as e:
            if isinstance(e.__cause__, ValidationError) and not isinstance(e, UnexpectedToolError):
                raise ToolError(f"Error executing tool {name}: {_readable_validation(e.__cause__)}") from e.__cause__
            raise


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITES_NEW_FILE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

mcp = _Server(
    "cryoem-files",
    version=__version__,
    instructions=(
        "Tools for inspecting and fixing cryo-EM files on this machine. Every tool returns a short summary, "
        "never raw pixel data. Read cryoem://formats before advising on gain conventions. "
        "For gain problems: inspect_gain, then check_gain_orientation against one raw movie, then convert_gain."
    ),
)


Frames = Annotated[int, Field(ge=1, le=10_000)]


@mcp.tool(title="Identify and check a file", annotations=READ_ONLY)
async def identify_file(path: str, verify_data: bool = False) -> dict:
    """Detect a file's real format from its contents, guess its role and check its integrity (truncation,
    corrupted headers, wrong extension). verify_data=true also decodes every frame/pixel. Damage is reported
    under 'problems' with the exact location (byte, frame) instead of failing."""
    return await _run(lambda: insp.identify_file(resolve(path), verify_data))


@mcp.tool(title="Inspect an MRC file", annotations=READ_ONLY)
async def inspect_mrc(path: str, validate: bool = True) -> dict:
    """Summarise an MRC/MRCS header and data (shape, mode, pixel size, stats) and check it against MRC2014."""
    return await _run(lambda: insp.inspect_mrc(resolve(path), validate))


@mcp.tool(title="Inspect a movie", annotations=READ_ONLY)
async def inspect_movie(path: str, sample_frames: Frames = 5, verify_all_frames: bool = False) -> dict:
    """Summarise a TIFF, EER or MRC movie: frames, frame size, compression, counts per pixel, gain-normalised guess.
    verify_all_frames=true decodes every frame and lists any corrupted ones."""
    return await _run(lambda: insp.inspect_movie(resolve(path), sample_frames, verify_all_frames))


@mcp.tool(title="Inspect a gain reference", annotations=READ_ONLY)
async def inspect_gain(path: str) -> dict:
    """Summarise a gain reference (.gain, .tif, .mrc, .dm4): size, value range, defect pixels, convention hints."""
    return await _run(lambda: gain_mod.inspect_gain(resolve(path)))


@mcp.tool(title="Check gain orientation against a movie", annotations=READ_ONLY)
async def check_gain_orientation(gain: str, movie: str, n_frames: Frames = 20) -> dict:
    """Find which of the 8 rotations/flips of the gain matches a raw (non-gain-normalised) movie, and whether
    the gain is multiplicative or divisive. Scores each variant by significance (z = |r| x sqrt(pixels)).
    confidence: high (convert as suggested), medium (probably right; confirm with more frames), low (the top
    variants tie), or no_match (the gain does not belong to this movie; no orientation is suggested)."""
    return await _run(lambda: gain_mod.check_orientation(resolve(gain), resolve(movie), n_frames))


@mcp.tool(title="Convert a gain reference to MRC", annotations=WRITES_NEW_FILE)
async def convert_gain(
    gain: str,
    out: str | None = None,
    rotate: Literal[0, 1, 2, 3] = 0,
    flip: Literal["none", "y", "x"] = "none",
    invert: bool = False,
    bin: Literal[1, 2] = 1,
    pixel_size: Annotated[float, Field(gt=0, lt=100)] | None = None,
    overwrite: bool = False,
) -> dict:
    """Write a gain reference as a float32 MRC. Applies rotate (x90 deg counter-clockwise), then flip, then
    2x binning (8K -> 4K), then invert (1/gain, for divisive EER gains). Never overwrites unless asked."""
    return await _run(lambda: gain_mod.convert_gain(resolve(gain), out, rotate, flip, invert, bin, pixel_size, overwrite))


@mcp.tool(title="Summarise a STAR file", annotations=READ_ONLY)
async def summarize_star(path: str) -> dict:
    """Summarise a RELION STAR file: blocks, row counts, optics groups, defocus/CTF/class statistics."""
    return await _run(lambda: meta.summarize_star(resolve(path)))


@mcp.tool(title="Summarise a SerialEM mdoc", annotations=READ_ONLY)
async def parse_mdoc(path: str) -> dict:
    """Summarise a SerialEM .mdoc: tilt range and step, dose, pixel size, frames per tilt."""
    return await _run(lambda: meta.parse_mdoc(resolve(path)))


@mcp.tool(title="Scan a session folder", annotations=READ_ONLY)
async def scan_directory(
    path: str,
    pattern: str = "*",
    limit: Annotated[int, Field(ge=1, le=1_000_000)] = 20000,
    max_depth: Annotated[int, Field(ge=1, le=64)] = 8,
) -> dict:
    """Count files by type in a session folder, find gain references and flag obvious problems.
    max_depth = folder levels to scan (the folder itself is level 1). Reports what it skipped (deeper folders,
    symlinks leading outside the allowed folders, pipes/devices) and how many MRC headers it checked."""
    return await _run(lambda: insp.scan_directory(resolve(path), pattern, limit, max_depth))


@mcp.resource("cryoem://formats", name="formats", description="Cheat sheet: formats and gain conventions")
def formats_doc() -> str:
    return resources.files("cryoem_files_mcp").joinpath("resources/formats.md").read_text()


@mcp.resource("cryoem://config", name="config", description="Folders this server is allowed to read")
def config_doc() -> str:
    return "Allowed folders:\n" + "\n".join(f"- {r}" for r in get_roots())


@mcp.prompt()
def diagnose_dataset(path: str) -> str:
    """Check a session folder end to end and report problems with fixes."""
    return (
        f"Diagnose the cryo-EM session at {path}. Use scan_directory first, then inspect_movie on one movie "
        "and inspect_gain on the gain reference. If movies are raw counts, run check_gain_orientation. "
        "Report each problem with the exact fix (tool call or MotionCor flags). Be brief."
    )


@mcp.prompt()
def prepare_for(pipeline: Literal["RELION", "CryoSPARC", "MotionCor"], path: str) -> str:
    """List what must be converted or fixed before importing a session into a pipeline."""
    return (
        f"Check whether the files in {path} are ready for {pipeline}. Read cryoem://formats for its expectations, "
        "inspect the movies and gain, and list only the conversions needed, as tool calls."
    )


def check_roots(roots: list[str]) -> list[str]:
    """Return a list of problems with the configured roots (empty if all are usable folders)."""
    problems = []
    for r in roots:
        p = Path(r).expanduser()
        if not p.exists():
            problems.append(f"{r}: does not exist")
        elif not p.is_dir():
            problems.append(f"{r}: is a file, not a folder")
        elif not os.access(p, os.R_OK | os.X_OK):
            problems.append(f"{r}: cannot be read (permission denied)")
    return problems


def main() -> None:
    global TIMEOUT_S
    ap = argparse.ArgumentParser(prog="cryoem-files-mcp", description=__doc__)
    ap.add_argument("--root", action="append", default=[], help="Folder the tools may read (repeatable). Default: current directory.")
    ap.add_argument("--timeout", type=float, default=None,
                    help="Seconds before a single tool call is abandoned (default 300, or CRYOEM_MCP_TIMEOUT).")
    args = ap.parse_args()
    env_roots = [r for r in os.environ.get("CRYOEM_MCP_ROOTS", "").split(os.pathsep) if r]
    roots = args.root or env_roots
    source = "--root" if args.root else "CRYOEM_MCP_ROOTS"
    bad = check_roots(roots)
    if bad:
        print(f"cryoem-files-mcp: cannot start: {source} must name existing folders:", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        print("Fix: pass the folder that holds your data, e.g. --root /data/cryoem", file=sys.stderr)
        sys.exit(2)
    if args.timeout is not None:
        if args.timeout <= 0:
            print("cryoem-files-mcp: --timeout must be positive", file=sys.stderr)
            sys.exit(2)
        TIMEOUT_S = args.timeout
    set_roots(roots)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
