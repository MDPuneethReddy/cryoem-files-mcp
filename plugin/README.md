# Cryo-EM Files

Lets Claude inspect, validate and fix cryo-EM files on your own computer. It reads MRC and MRCS files, TIFF and EER movies, gain references (.gain, .tif, .mrc, .dm4), RELION STAR files and SerialEM mdoc files, and answers with short summaries instead of raw pixels.

It catches the silent problems that waste processing time: truncated or half-copied files, corrupted frames, files with the wrong extension, and gain references in the wrong format, orientation or convention. Every problem is reported with what is wrong, where in the file, the likely cause and the fix.

## What it runs

When enabled, Claude Code starts the open-source `cryoem-files-mcp` Python package (pinned version, installed from PyPI with `uvx`) as a local MCP server. It runs only on your machine, reads only the data folder you choose at setup, makes no network requests and sends no data anywhere. The only tool that writes is `convert_gain`, which creates a new MRC file next to the input and never overwrites anything unless asked.

## Install

In Claude Code:

```
/plugin install cryoem-files --marketplace MDPuneethReddy/cryoem-files-mcp
```

You will be asked for your data folder. Change it later with `/config`.

## Requirements

- [uv](https://docs.astral.sh/uv/) installed (provides `uvx`)
- Python 3.10 or newer

## Try it

- "Check the session in /data/26jun03a for damaged files"
- "Does this gain reference match my movies, and how should I pass it to MotionCor?"
- "Summarise particles.star"

Source, full tool list and error-message catalogue: https://github.com/MDPuneethReddy/cryoem-files-mcp
