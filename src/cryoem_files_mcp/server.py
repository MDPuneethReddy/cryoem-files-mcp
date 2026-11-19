"""MCP server entry point. Run: cryoem-files-mcp --root /data/cryoem"""

from __future__ import annotations

import argparse
import os
from importlib import resources
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from . import gain as gain_mod
from . import inspect as insp
from . import meta
from .paths import get_roots, resolve, set_roots

def _call(fn):
    """Run a tool body; expected problems (bad path, wrong format, file exists) reach the model with their reason."""
    try:
        return fn()
    except (ValueError, OSError) as e:  # PathNotAllowed is a ValueError; FileExistsError/FileNotFoundError are OSError
        raise ToolError(str(e)) from e


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITES_NEW_FILE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

mcp = MCPServer(
    "cryoem-files",
    version=__version__,
    instructions=(
        "Tools for inspecting and fixing cryo-EM files on this machine. Every tool returns a short summary, "
        "never raw pixel data. Read cryoem://formats before advising on gain conventions. "
        "For gain problems: inspect_gain, then check_gain_orientation against one raw movie, then convert_gain."
    ),
)


@mcp.tool(annotations=READ_ONLY)
def identify_file(path: str, verify_data: bool = False) -> dict:
    """Detect a file's real format from its contents, guess its role and check its integrity (truncation,
    corrupted headers, wrong extension). verify_data=true also decodes every frame/pixel. Damage is reported
    under 'problems' with the exact location (byte, frame) instead of failing."""
    return _call(lambda: insp.identify_file(resolve(path), verify_data))


@mcp.tool(annotations=READ_ONLY)
def inspect_mrc(path: str, validate: bool = True) -> dict:
    """Summarise an MRC/MRCS header and data (shape, mode, pixel size, stats) and check it against MRC2014."""
    return _call(lambda: insp.inspect_mrc(resolve(path), validate))


@mcp.tool(annotations=READ_ONLY)
def inspect_movie(path: str, sample_frames: int = 5, verify_all_frames: bool = False) -> dict:
    """Summarise a TIFF, EER or MRC movie: frames, frame size, compression, counts per pixel, gain-normalised guess.
    verify_all_frames=true decodes every frame and lists any corrupted ones."""
    return _call(lambda: insp.inspect_movie(resolve(path), sample_frames, verify_all_frames))


@mcp.tool(annotations=READ_ONLY)
def inspect_gain(path: str) -> dict:
    """Summarise a gain reference (.gain, .tif, .mrc, .dm4): size, value range, defect pixels, convention hints."""
    return _call(lambda: gain_mod.inspect_gain(resolve(path)))


@mcp.tool(annotations=READ_ONLY)
def check_gain_orientation(gain: str, movie: str, n_frames: int = 20) -> dict:
    """Find which of the 8 rotations/flips of the gain matches a raw (non-gain-normalised) movie, and whether
    the gain is multiplicative or divisive. Returns the best variant, its correlation and confidence."""
    return _call(lambda: gain_mod.check_orientation(resolve(gain), resolve(movie), n_frames))


@mcp.tool(annotations=WRITES_NEW_FILE)
def convert_gain(
    gain: str,
    out: str | None = None,
    rotate: int = 0,
    flip: Literal["none", "y", "x"] = "none",
    invert: bool = False,
    bin: Literal[1, 2] = 1,
    pixel_size: float | None = None,
    overwrite: bool = False,
) -> dict:
    """Write a gain reference as a float32 MRC. Applies rotate (x90 deg counter-clockwise) then flip,
    optional invert (1/gain, for divisive EER gains) and 2x binning (8K -> 4K). Never overwrites unless asked."""
    return _call(lambda: gain_mod.convert_gain(resolve(gain), out, rotate, flip, invert, bin, pixel_size, overwrite))


@mcp.tool(annotations=READ_ONLY)
def summarize_star(path: str) -> dict:
    """Summarise a RELION STAR file: blocks, row counts, optics groups, defocus/CTF/class statistics."""
    return _call(lambda: meta.summarize_star(resolve(path)))


@mcp.tool(annotations=READ_ONLY)
def parse_mdoc(path: str) -> dict:
    """Summarise a SerialEM .mdoc: tilt range and step, dose, pixel size, frames per tilt."""
    return _call(lambda: meta.parse_mdoc(resolve(path)))


@mcp.tool(annotations=READ_ONLY)
def scan_directory(path: str, pattern: str = "*", limit: int = 20000) -> dict:
    """Count files by type in a session folder, find gain references and flag obvious problems."""
    return _call(lambda: insp.scan_directory(resolve(path), pattern, limit))


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


def main() -> None:
    ap = argparse.ArgumentParser(prog="cryoem-files-mcp", description=__doc__)
    ap.add_argument("--root", action="append", default=[], help="Folder the tools may read (repeatable). Default: current directory.")
    args = ap.parse_args()
    env_roots = [r for r in os.environ.get("CRYOEM_MCP_ROOTS", "").split(os.pathsep) if r]
    set_roots(args.root or env_roots)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
