# Cryo-EM file formats and gain conventions (cheat sheet)

## Gain conventions
- Gatan K2/K3: raw frames are MULTIPLIED by the gain. Gains come as .dm4 or TIFF.
- Falcon (EER), TIFF `.gain` written by newer Falcon software: MULTIPLY.
- Older Falcon MRC gains for EER: DIVIDE.
- MotionCor multiplies frames by the gain unless -InvGain is set (it needs a float32 MRC gain).
- RELION, EER movies: decides by file extension only. It MULTIPLIES by a `.gain` file and DIVIDES by any other
  gain, MRC included. So a multiplicative MRC (e.g. a converted Falcon .gain) would be inverted wrongly: for
  RELION with EER keep the original `.gain`, or write a divisive MRC (check_gain_orientation's next_step says how).
- RELION, TIFF/MRC movies: multiplies, like MotionCor.
- EER gains can be 4K or 8K (RELION resamples them). According to CryoSPARC forum reports (2021), CryoSPARC needs
  a 4096x4096 gain for EER: convert with bin=2, and invert=true for an old divisive 8K MRC gain.
- According to CryoSPARC forum reports (2023), CryoSPARC cannot read .dm4 gains; convert to MRC first.

## Orientation and row order
- The gain may be rotated/flipped relative to the movie. There are 8 distinct variants.
- check_gain_orientation scores all 8 against raw summed frames, in array view as tifffile/mrcfile read the
  files (row 0 = first row in the file). Variant rotN = numpy.rot90(gain, N/90): counter-clockwise in array view.
- MotionCor's -RotGain k is the opposite direction in array view (numpy.rot90(gain, -k); its "counter-clockwise"
  assumes y pointing up). The equivalent flag for the tool's rotate=k is -RotGain (4-k)%4. -FlipGain 1 = flipud,
  2 = fliplr; MotionCor rotates, then flips, then inverts.
- Row order: MotionCor and RELION read TIFF movie rows bottom-up (flipped), and MRC and EER rows as stored.
  So a gain matched to a TIFF movie must be flipped upside down before it is written as MRC:
  convert_gain(movie_type='tiff') does this. EER and MRC movies need no flip.
- convert_gain bakes the orientation (and row flip) in, so MotionCor runs with -RotGain 0 -FlipGain 0.
- Movies must be raw counts (integer, not gain-normalised) for the check to work.
- None of this has been validated on real data yet: run motion correction on one movie with the converted gain
  and check that the corrected sum shows no detector pattern before processing a whole dataset.

## Formats
- MRC2014: 1024-byte header, "MAP " at byte 208. Movies as stacks, micrographs 2D, maps 3D, particles .mrcs.
- TIFF movies: one page per frame, often LZW compressed, integer counts.
- EER: BigTIFF with compression 65000-65002; each page is a short sub-frame of electron events. tifffile
  decodes EER at the stored detector size (4K on Falcon 4) by default.
- DM4/DM3: Gatan DigitalMicrograph.
- STAR: RELION metadata (optics block + data blocks).
- mdoc: SerialEM per-tilt metadata.
