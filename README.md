# cryoem-files-mcp

<!-- mcp-name: io.github.MDPuneethReddy/cryoem-files-mcp -->

An MCP server that lets Claude (or any MCP client) inspect, validate and convert cryo-EM files on your own machine. It returns short summaries instead of gigabytes of pixels, and it fixes the most common silent mistake in preprocessing: a gain reference in the wrong format, orientation or convention.

Runs locally over stdio. Nothing is uploaded; there is no server to host.

## Tools

| Tool | What it does | Writes? |
| --- | --- | --- |
| `identify_file` | Real format from file contents (MRC, TIFF, EER, DM4, STAR, mdoc) + role guess | No |
| `inspect_mrc` | Header, pixel size, stats, MRC2014 validation | No |
| `inspect_movie` | Frames, size, compression, counts/pixel/frame, gain-normalised guess (TIFF, EER, MRC) | No |
| `inspect_gain` | Size, value range, defect pixels, convention hints | No |
| `check_gain_orientation` | Scores all 8 rotations/flips of the gain against raw frames; reports best match and multiply-vs-divide convention | No |
| `convert_gain` | Float32 MRC gain with rotate/flip/invert/2x-bin baked in | New file only |
| `summarize_star` | RELION STAR blocks, optics groups, defocus/CTF/class stats | No |
| `parse_mdoc` | SerialEM tilt range, step, dose, pixel size | No |
| `scan_directory` | File counts by type, gain references found, obvious problems | No |

Also: resource `cryoem://formats` (gain conventions cheat sheet) and prompts `diagnose_dataset`, `prepare_for`.

## Install

Easiest, as a Claude Code plugin (asks for your data folder, adds a file-checking skill):

```
/plugin install cryoem-files --marketplace MDPuneethReddy/cryoem-files-mcp
```

Or the package directly:

```bash
pip install cryoem-files-mcp            # add [dm4] for Gatan .dm4 gains
```

Claude Code (without the plugin):

```bash
claude mcp add cryoem-files -- uvx cryoem-files-mcp --root /data/cryoem
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "cryoem-files": {
      "command": "uvx",
      "args": ["cryoem-files-mcp", "--root", "/data/cryoem"]
    }
  }
}
```

`--root` (repeatable, or `CRYOEM_MCP_ROOTS`) limits which folders the tools can read. Default: the current directory.

## Damaged files

Every reader checks the file before trusting the library, because the libraries fail silently on common damage (a truncated TIFF opens with fewer frames and no error). Problems come back with what is wrong, where (byte or frame), the likely cause and the fix. `identify_file` never fails on a damaged file; it reports `integrity: damaged` and the problems. See [docs/error-messages.md](docs/error-messages.md) for the exact message for each kind of damage.

## How the orientation check works

Raw (not gain-normalised) frames carry the detector's fixed pixel pattern. The tool sums N frames, high-pass filters both the sum and each of the 8 rotated/flipped gains, and correlates them. The right variant correlates strongly; the rest sit near zero. A negative correlation means a multiplicative gain (K2/K3, Falcon TIFF `.gain`); a positive one means divisive (older Falcon MRC gains for EER).

`convert_gain` can then bake the winning orientation (and the inversion, if needed) into a new MRC, so MotionCor runs with `-RotGain 0 -FlipGain 0` and no `-InvGain`.

## Safety

- Read tools never modify files.
- `convert_gain` writes a new file and refuses to overwrite anything (including its input) unless `overwrite=true`.
- Paths outside the allowed roots are refused.

## Status and known limits (v0.1)

- 65 tests on synthetic data: all 8 orientations, both conventions, 4K/8K binning, and ~45 damaged or unusual files. Needs validation on real K3 and Falcon 4 datasets from EMPIAR.
- `.dm4` reading uses `ncempy` and is untested on real Gatan gains; compare against IMOD `dm2mrc` before trusting it.
- Assumes MotionCor reads TIFF/EER movies and MRC gains in the same row order as `tifffile`/`mrcfile`. Confirm once on a real dataset.

## Publishing

See [docs/PUBLISHING.md](docs/PUBLISHING.md) (PyPI, official MCP Registry, Claude plugin directory).

## Development

```bash
pip install -e '.[dev,dm4]'
pytest
```

License: BSD-3-Clause.
