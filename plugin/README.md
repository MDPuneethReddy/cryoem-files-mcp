# Cryo-EM Files

Lets Claude inspect, validate and fix cryo-EM files on your own computer. It reads MRC and MRCS files, TIFF and EER movies, gain references (.gain, .tif, .mrc, .dm4), RELION STAR files and SerialEM mdoc files, and answers with short summaries instead of raw pixels.

It catches the silent problems that waste processing time: truncated or half-copied files, corrupted frames, files with the wrong extension, and gain references in the wrong format, orientation or convention. Every problem is reported with what is wrong, where in the file, the likely cause and the fix.

## What it runs

When enabled, Claude Code starts the open-source `cryoem-files-mcp` Python package as a local MCP server, with the command `uvx cryoem-files-mcp==0.1.0 --root <your data folder>` (exact pinned version). It runs only on your machine and reads only the data folder you choose at setup.

- **Network:** the server itself makes no network requests and sends no data anywhere. On first run, `uvx` downloads the pinned package and its dependencies from PyPI (`pypi.org`, `files.pythonhosted.org`). If no suitable Python is installed, `uv` may also download a Python build (from `releases.astral.sh`, falling back to `github.com` / `release-assets.githubusercontent.com`). After that it runs offline. These hosts are declared under `network.hosts` in `plugin.json`.
- **Reads:** only files inside the data folder. Links that lead outside it, named pipes and devices are refused.
- **Writes:** only `convert_gain`, which creates a new MRC file and never overwrites anything (including its input) unless asked with `overwrite=true`.

## Install

In Claude Code, add the marketplace, then install the plugin:

```
/plugin marketplace add MDPuneethReddy/cryoem-files-mcp
/plugin install cryoem-files@cryoem-files-mcp
```

On Claude Code v2.1.275 or later you can do both in one step: `/plugin install cryoem-files --marketplace MDPuneethReddy/cryoem-files-mcp`.

You will be asked for your data folder (required: the server does not start without it). Choose the folder that holds your sessions, not your home folder. Change it later with `/config`.

This plugin is built for Claude Code. Claude Cowork does not prompt for plugin settings and skips a server whose setting has no default, and Claude chat does not run local servers.

## Requirements

- [uv](https://docs.astral.sh/uv/) installed (provides `uvx`)
- Python 3.10 or newer (if none is installed, `uv` downloads one on first run)
- Claude Code (see Install)

## Try it

- "Check the session in /data/26jun03a for damaged files"
- "Does this gain reference match my movies, and how should I pass it to MotionCor?"
- "Summarise particles.star"

Source, full tool list and error-message catalogue: https://github.com/MDPuneethReddy/cryoem-files-mcp
