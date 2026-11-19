# Changelog

All notable changes to this project are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - unreleased

First release.

### Added

- Local MCP server over stdio (`cryoem-files-mcp`, MCP Python SDK 2.3 or newer) with nine tools:
  `identify_file`, `inspect_mrc`, `inspect_movie`, `inspect_gain`, `check_gain_orientation`, `convert_gain`,
  `summarize_star`, `parse_mdoc`, `scan_directory`. All tools have human-readable titles and read-only or
  destructive annotations.
- Resources `cryoem://formats` (gain conventions cheat sheet) and `cryoem://config`; prompts `diagnose_dataset`
  and `prepare_for`.
- Integrity checks before any library read (truncated MRC/TIFF, corrupted frames, wrong extensions), with
  messages that give what is wrong, where, the likely cause and the fix (see `docs/error-messages.md`).
- Claude Code plugin bundle (`plugin/`) with a `cryoem-file-check` skill and a data-folder setting, and a
  marketplace at `.claude-plugin/marketplace.json`.
- `server.json` for the official MCP Registry (`io.github.MDPuneethReddy/cryoem-files-mcp`).

### Notable behaviour

- `--root` is required (repeatable; or `CRYOEM_MCP_ROOTS`). The server exits with code 2 if no root is given or a
  root is not an existing folder. It never falls back to the current directory.
- `--timeout` / `CRYOEM_MCP_TIMEOUT` (default 300 s): a tool call that runs longer returns an error instead of
  hanging the session (for example on a stale network mount).
- `check_gain_orientation` reports a significance score z = |r| x sqrt(n) for every rotate/flip variant and a
  verdict of `high`, `medium`, `low` or `no_match`. `no_match` suggests no orientation and no conversion.
- `check_gain_orientation` returns `next_step` as a dict per program (`motioncor`, `relion`, `check_first`),
  because RELION treats non-`.gain` EER gains as divisive.
- `convert_gain` takes `rotate` 0-3 (multiples of 90 degrees counter-clockwise in array view, `numpy.rot90`),
  then flip, then 2x bin, then invert, in that order. `movie_type='tiff'` also flips rows, because MotionCor and
  RELION read TIFF movie rows bottom-up. It writes a new float32 MRC and overwrites only with `overwrite=true`.
- Named pipes, devices and sockets are never opened; links that lead outside the allowed folders are refused and
  never followed by `scan_directory`. `scan_directory` reports what it skipped (depth limit, header-check cap).

### Known limitations

- Tested only on synthetic data. Not yet validated on real K3, Falcon 4 or EER datasets. Before processing a whole
  dataset with a converted gain, run motion correction on one movie and check the corrected sum.
- EER decoding is untested on real files.
- `.dm4` gain reading (optional `[dm4]` extra, `ncempy`) is untested on real Gatan gains.
- Tested on Linux only (Python 3.10, 3.12, 3.13). macOS and Windows are untested.
- The plugin targets Claude Code. Claude Cowork does not prompt for the data folder, and Claude chat does not run
  local servers.
