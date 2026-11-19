---
name: cryoem-file-check
description: Check cryo-EM sessions and files (MRC, TIFF/EER movies, gain references, STAR, mdoc) for damage and gain problems using the cryoem-files tools. Use when the user asks whether files are OK, why processing fails, or how to prepare a gain for MotionCor, RELION or CryoSPARC.
---

# Checking cryo-EM files

Use the `cryoem-files` MCP tools. They return short summaries; never try to read pixel data yourself.

## A whole session folder
1. `scan_directory` on the folder. Report its `problems` first (empty files, possibly truncated files, damaged MRCs, missing gain, folders deeper than `max_depth`). Mention its `notes` and `skipped` counts (MRC headers only partly checked, symlinks outside the allowed folders, pipes or devices); if deeper folders were skipped, re-run with a larger `max_depth`.
2. For each file it flags, run `identify_file` with `verify_data=true` to get the exact damage (byte, frame).
3. `inspect_movie` on one normal movie, `inspect_gain` on the gain.

## A gain reference
1. `inspect_gain`, then `check_gain_orientation` with one raw movie (integer counts, not gain-normalised). Ask which program the user runs (MotionCor or RELION) if it is not clear.
2. Act on `confidence`:
   - `high`: run the exact `convert_gain` call from `next_step.motioncor` or `next_step.relion` (they differ for EER movies; both pass `movie_type`, which adds the row flip TIFF movies need). For MotionCor, the converted gain is used with `-Gain <output> -RotGain 0 -FlipGain 0` and no `-InvGain`. For RELION with EER, follow `next_step.relion` (keep the original `.gain`, or a divisive MRC).
   - `medium`: report the suggested orientation as probable, quote the `warning` and `z_best`/`z_gap`, and re-run with `n_frames=50` or more. Convert only if the re-run is `high`, or if the user asks to proceed.
   - `low`: the gain matches but two orientations tie. Say so, quote the `warning`, and ask for another raw movie or more frames. Do not pick one.
   - `no_match`: tell the user this gain does not belong to these movies (or the movie is already gain-normalised); quote the `problem`. Do not suggest an orientation or convert anything.
3. Always tell the user to check one movie first (`next_step.check_first`): run motion correction on one movie with the converted gain and confirm the corrected sum shows no detector pattern before processing the whole dataset. This has not been validated on real data.
4. Do not translate the tool's `rotate` into MotionCor flags yourself: MotionCor's `-RotGain` turns the other way, and TIFF movies need an extra row flip. Use the converted gain, or `motioncor_flags_for_original_gain` when the tool gives it.
5. Mention the z values when you explain a result (z below 6 is what an unrelated gain gives by chance).

## How to report
- Quote the tool's problem text: it already says what is wrong, where, the likely cause and the fix.
- Separate files that are damaged (re-copy them) from ones that are only unusual (warnings).
- Never claim a file is fine unless the tool reported no problems; say when data was only sampled.
