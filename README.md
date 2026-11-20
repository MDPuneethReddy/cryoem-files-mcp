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
| `check_gain_orientation` | Scores all 8 rotations/flips of the gain against raw frames; reports the best match with a significance-based confidence (`high`, `medium`, `low`, `no_match`) and the multiply-vs-divide convention | No |
| `convert_gain` | Float32 MRC gain with rotate/flip/2x-bin/invert baked in (applied in that order), plus the row flip TIFF movies need (`movie_type='tiff'`) | New file (overwrites only with `overwrite=true`) |
| `summarize_star` | RELION STAR blocks, optics groups, defocus/CTF/class stats | No |
| `parse_mdoc` | SerialEM tilt range, step, dose, pixel size | No |
| `scan_directory` | File counts by type, gain references found, obvious problems. `max_depth` (default 8 folder levels), `limit` (default 20,000 files); names the flagged files (`flagged_files`) and reports what it skipped | No |

Also: resource `cryoem://formats` (gain conventions cheat sheet) and prompts `diagnose_dataset`, `prepare_for`.

## Install

Requires [uv](https://docs.astral.sh/uv/) (provides `uvx`) for the plugin and the configs below. The first run downloads the package from PyPI; after that it runs offline.

Easiest, as a Claude Code plugin (asks for your data folder, adds a file-checking skill). Add the marketplace, then install:

```
/plugin marketplace add MDPuneethReddy/cryoem-files-mcp
/plugin install cryoem-files@cryoem-files-mcp
```

On Claude Code v2.1.275 or later, one step also works: `/plugin install cryoem-files --marketplace MDPuneethReddy/cryoem-files-mcp`. The plugin targets Claude Code: Claude Cowork does not prompt for the data folder, and Claude chat does not run local servers.

Or the package directly:

```bash
pip install cryoem-files-mcp            # add [dm4] for Gatan .dm4 gains
cryoem-files-mcp --root /data/cryoem    # --root is required
```

Claude Code (without the plugin):

```bash
claude mcp add cryoem-files -- uvx cryoem-files-mcp==0.1.0 --root /data/cryoem
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "cryoem-files": {
      "command": "uvx",
      "args": ["cryoem-files-mcp==0.1.0", "--root", "/data/cryoem"]
    }
  }
}
```

Replace `/data/cryoem` with the folder that holds your data (not your home folder). Add more folders with more `--root` arguments.

`--root` (repeatable; or `CRYOEM_MCP_ROOTS`, folders separated by `:`, or `;` on Windows, used only when no `--root` is given) sets which folders the tools can read. It is required: the server does not fall back to the current directory, because MCP clients often start servers in `/` or your home folder. The server refuses to start (exit code 2, reason on stderr) if no root is given, or a root does not exist or is not a folder.

`--timeout` (or `CRYOEM_MCP_TIMEOUT`, default 300 seconds) is the longest a single tool call may run before it returns an error.

## Damaged files

Every reader checks the file before trusting the library, because the libraries fail silently on common damage (a truncated TIFF opens with fewer frames and no error). Problems come back with what is wrong, where (byte or frame), the likely cause and the fix. `identify_file` never fails on a damaged file; it reports `integrity: damaged` and the problems. See [docs/error-messages.md](docs/error-messages.md) for the exact message for each kind of damage.

## How the orientation check works

Raw (not gain-normalised) frames carry the detector's fixed pixel pattern. The tool sums N frames, high-pass filters both the sum and each of the 8 rotated/flipped gains (also removing row/column offsets and clipping hot pixels), and correlates them. The right variant correlates strongly; the rest sit near zero. A negative correlation means a multiplicative gain (K2/K3, Falcon TIFF `.gain`); a positive one means divisive (older Falcon MRC gains for EER).

Confidence is a significance test, not a fixed correlation cut-off. For a gain that does not belong to the movie, the correlation r over n compared pixels is about N(0, 1/sqrt(n)), so the tool reports z = |r| x sqrt(n) for every variant:

| Verdict | Rule | What to do |
| --- | --- | --- |
| `no_match` | best z < 6 | The gain does not belong to these movies (or the movie is already gain-normalised). No orientation is suggested. |
| `low` | z gap to the runner-up < 3 | The gain matches, but two variants tie. Use more frames or another raw movie. |
| `medium` | otherwise | Probably right; confirm with more frames before converting. |
| `high` | best z >= 10 and gap >= 6 | Convert as suggested. |

Calibration on simulated data (`python3 scripts/calibrate_orientation.py --full`): 120 unrelated same-size gains gave best z of at most 3.1 (all `no_match`; 50 more with strong shared row/column stripes, at most 3.0); correct gains with 2% and 1% pixel variation at 0.5 e/px/frame, 20 frames, 512x512 gave z of about 32 and 16 (all `high`); a Falcon-style 8K gain with 4% variation against a 4K movie gave z = 88 (`high`).

Variant names are in array view: `rot90` means `numpy.rot90(gain, 1)`, counter-clockwise with row 0 at the top. MotionCor's `-RotGain k` turns the other way in that view (its help assumes y pointing up), so the tool's `rotate=k` corresponds to `-RotGain (4-k)%4`.

`convert_gain` can then bake the winning orientation (and the inversion, if needed) into a new MRC, so MotionCor runs with `-RotGain 0 -FlipGain 0` and no `-InvGain`. Two program details matter, both from the MotionCor3 and RELION source code:

- **Row order.** MotionCor and RELION read TIFF movie rows bottom-up, but MRC (and EER) rows as stored. A gain matched to a TIFF movie is therefore written upside down (`movie_type='tiff'`). EER and MRC movies need no flip.
- **RELION and EER.** RELION picks the gain convention for EER movies from the file extension: it multiplies by a `.gain` file and divides by anything else, MRC included. For RELION with EER, keep the original `.gain` or write a divisive MRC. For TIFF and MRC movies RELION multiplies, like MotionCor.

`check_gain_orientation` returns the exact `convert_gain` call for MotionCor and for RELION in `next_step`. When the gain is twice the movie frame size (8K gain, 4K movie), those calls include `bin=2` where the program needs a same-size gain. `next_step.cryosparc` is a hedged suggestion only: CryoSPARC's gain row-order handling has not been researched, so check one movie and use its "Flip gain ref in Y" option if the detector pattern remains.

## Safety

- Read tools never modify files.
- `convert_gain` writes a new file and overwrites an existing output only with `overwrite=true`. It never overwrites its input, even with `overwrite=true`. NaN or infinite gain pixels are set to 0 (defects) and reported under `problems`.
- Paths outside the allowed roots are refused, including through symlinks; error messages show the path as given, never where a symlink points. `scan_directory` skips (and counts) symlinks that lead outside the roots without reading them.
- Named pipes, devices and sockets are never opened (reading them can block forever); `scan_directory` skips and counts them.
- Stale network mounts: a read on a hung NFS/GPFS mount blocks inside the kernel and cannot be interrupted. The call returns a timeout error after `--timeout` seconds and the server stays usable, but the stuck worker thread remains until the mount recovers. Check the mount (`ls` the folder in a terminal) if calls time out. A call that times out because the data is large also keeps computing in the background; to bound memory, at most 2 heavy operations (orientation check, gain conversion, full-frame verification) run at once, and further ones wait or report "server busy".
- Filenames that are not valid UTF-8 (common after Windows/Samba copies) are shown with `\xNN` escapes instead of breaking the result.

## Status and known limits (v0.1)

- **Check one movie first.** The gain row order and MotionCor/RELION conventions come from reading their source code, not from a run on real data. Before processing a whole dataset with a converted gain, run motion correction on one movie and check that the corrected sum shows no detector pattern (seams, stripes).
- Tested only on synthetic data (all 8 orientations, both conventions, 4K/8K binning, ~50 damaged or unusual files). Not yet validated on real K3, Falcon 4 or EER datasets; please report results.
- EER support is untested on real files.
- MRC modes 3 (complex int16) and 101 (4-bit packed, SerialEM) are decoded by this tool itself, because `mrcfile` cannot map them; checked on synthetic files only.
- `.dm4` reading uses `ncempy` and is untested on real Gatan gains; compare against IMOD `dm2mrc` before trusting it.
- CryoSPARC statements (EER gain size, `.dm4`) come from CryoSPARC forum reports (2021, 2023), not the official guide.
- `scan_directory` reports when it did not look at everything (deeper folders, MRC header-check cap); read its notes.
- Tested on Linux. macOS and Windows are untested.

## Publishing

See [docs/PUBLISHING.md](docs/PUBLISHING.md) (PyPI, official MCP Registry, Claude plugin directory).

## Development

```bash
pip install -e '.[dev,dm4]'
pytest
```

License: BSD-3-Clause.
