---
name: cryoem-file-check
description: Check cryo-EM sessions and files (MRC, TIFF/EER movies, gain references, STAR, mdoc) for damage and gain problems using the cryoem-files tools. Use when the user asks whether files are OK, why processing fails, or how to prepare a gain for MotionCor, RELION or CryoSPARC.
---

# Checking cryo-EM files

Use the `cryoem-files` MCP tools. They return short summaries; never try to read pixel data yourself.

## A whole session folder
1. `scan_directory` on the folder. Report its `problems` first (empty files, possibly truncated files, damaged MRCs, missing gain).
2. For each file it flags, run `identify_file` with `verify_data=true` to get the exact damage (byte, frame).
3. `inspect_movie` on one normal movie, `inspect_gain` on the gain.

## A gain reference
1. `inspect_gain`, then `check_gain_orientation` with one raw movie (integer counts, not gain-normalised).
2. If confidence is high, run `convert_gain` with the suggested `rotate`, `flip` and `invert`, then tell the user to run MotionCor with `-RotGain 0 -FlipGain 0` and no `-InvGain`.
3. If confidence is low, say so and ask for a raw movie or more frames; do not guess an orientation.

## How to report
- Quote the tool's problem text: it already says what is wrong, where, the likely cause and the fix.
- Separate files that are damaged (re-copy them) from ones that are only unusual (warnings).
- Never claim a file is fine unless the tool reported no problems; say when data was only sampled.
