# Cryo-EM file formats and gain conventions (cheat sheet)

## Gain conventions
- Gatan K2/K3: raw frames are MULTIPLIED by the gain. Gains come as .dm4 or TIFF.
- Falcon (EER), TIFF `.gain` written by newer Falcon software: MULTIPLY (RELION multiplies).
- Falcon (EER), MRC gain: DIVIDE (RELION divides by an MRC gain for EER). Convert with invert=true to get a multiplicative gain.
- MotionCor multiplies by the gain unless -InvGain is set.
- EER gains can be 4K or 8K; CryoSPARC requires 4096x4096 for EER (convert with bin=2).
- CryoSPARC cannot read .dm4 gains; convert to MRC first.

## Orientation
- The gain may be rotated/flipped relative to the movie. There are 8 distinct variants.
- check_gain_orientation scores all 8 against raw summed frames. convert_gain can bake the best one in,
  so MotionCor runs with -RotGain 0 -FlipGain 0.
- Movies must be raw counts (integer, not gain-normalised) for the check to work.

## Formats
- MRC2014: 1024-byte header, "MAP " at byte 208. Movies as stacks, micrographs 2D, maps 3D, particles .mrcs.
- TIFF movies: one page per frame, often LZW compressed, integer counts.
- EER: BigTIFF with compression 65000-65002; each page is a short sub-frame of electron events.
- DM4/DM3: Gatan DigitalMicrograph.
- STAR: RELION metadata (optics block + data blocks).
- mdoc: SerialEM per-tilt metadata.
