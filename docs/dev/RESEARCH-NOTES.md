# Research notes: fact check of cryoem-files-mcp claims

Checked 2026-11-19 against primary sources: MotionCor3 source (github.com/czimaginginstitute/MotionCor3, commit dd8b683, Oct 2025), RELION source (github.com/3dem/relion, master), tifffile 2026.11.16 and mrcfile 1.5.4 wheels from PyPI, the MCP Registry repo (github.com/modelcontextprotocol/registry, commit 115f852, 13 Nov 2026), the Claude Code and claude.com plugin docs, PyPI docs, and the CryoSPARC guide and forum. MotionCor2 is closed source, so MotionCor3 stands in for it below. Where only a forum post backs a claim, it says so.

## Corrections needed (most important first)

1. **TIFF movies: the baked MRC gain is upside down for MotionCor and RELION.** Both programs flip TIFF rows on read. They read MRC rows as stored. `tifffile` and `mrcfile` both keep file order. So a gain that matches the `tifffile` movie must be written with `np.flipud` before `mrcfile` saves it, or used with `-FlipGain 1`. For EER and MRC movies, no flip is needed. Files: `src/cryoem_files_mcp/gain.py` (`convert_gain`, plus the advice text in `check_gain_orientation`), `README.md` (lines 66-68 and 80), `plugin/skills/cryoem-file-check/SKILL.md` step 2, and `resources/formats.md` ("Orientation" section).
2. **RELION with EER treats any non-`.gain` gain as divisive.** RELION picks the convention from the file extension only: `.gain` multiplies, and everything else, MRC included, divides. A multiplicative MRC from `convert_gain` (for example, a Falcon `.gain` converted without `invert`) is therefore inverted again by RELION for EER movies. Files: `resources/formats.md`, `README.md`, `SKILL.md`, and the `gain.py` advice text. Say that for RELION with EER you should keep the original `.gain`, or write a divisive MRC.
3. **Do not lowercase the registry namespace.** The registry gives `io.github.<login>/*` using the GitHub login exactly as cased, and it compares names with a case-sensitive prefix match. `io.github.MDPuneethReddy/...` is right. The lowercase fallback in `docs/PUBLISHING.md` ("If the registry rejects the capitalisation...") would fail. Delete it.
4. **A local server in the directory works only in Claude Code with this config.** Chat ignores local MCP servers. Cowork ignores a server that references `${user_config.*}` when the option has no default, and Cowork never prompts for values. The `docs/PUBLISHING.md` table row "one-click install with a data-folder prompt" is true only for Claude Code. Fix the PUBLISHING text. Optionally add a `default` to `data_folder` in `plugin/.claude-plugin/plugin.json` so the server can load in Cowork.
5. **`server.json`: `--root` is repeatable but not marked so.** Add `"isRepeated": true`, and preferably `"format": "filepath"`, to the named argument.
6. **`ci.yml` downloads mcp-publisher from the mutable `releases/latest` URL.** The registry docs say to pin `MCP_PUBLISHER_VERSION` and verify the Sigstore bundle with cosign. They also say not to switch back to `releases/latest`. Also add `environment: pypi` to the PyPI publish job. PyPI calls it "optional but strongly recommended", and you would then enter `pypi` in the pending-publisher form instead of leaving it empty. Files: `.github/workflows/ci.yml` and `docs/PUBLISHING.md` (step 1).
7. **Rotation direction naming.** The tool's "rot90 ccw" is `numpy.rot90(g, k)` in array space. MotionCor's `-RotGain k` equals `numpy.rot90(g, -k)` in the same array space. Its help says counter-clockwise because it means y-up (MRC/IMOD) display. The baked-in workflow is unaffected. Any text that maps the tool's `rotate` to a MotionCor flag must use `-RotGain (4-k)%4`, and that holds only for MRC or EER movies. Files: `gain.py` (variant names and docstrings) and `resources/formats.md`.
8. **STAR row-count check gives false positives (`src/cryoem_files_mcp/meta.py`, `_loop_row_problems`).** RELION writes values that contain whitespace inside double quotes, and it reads single or double quotes. It writes empty strings as `""`. It can also append `# comment` after the values on a data row. `s.split()` miscounts all three cases. Use `shlex.split(s, comments=True)` or an equivalent quote- and comment-aware tokenizer.
9. **Install command.** `/plugin install cryoem-files --marketplace MDPuneethReddy/cryoem-files-mcp` is valid, but only on Claude Code v2.1.275 or later. Add the two-step form that works on any version: `/plugin marketplace add MDPuneethReddy/cryoem-files-mcp`, then `/plugin install cryoem-files@cryoem-files-mcp`. Files: `README.md`, `plugin/README.md`, and `docs/PUBLISHING.md`.
10. **Minor fixes in `docs/PUBLISHING.md`:**
    - Eligible plans also include Ultra.
    - The icon size (512-2048 px) and the claim that the icon is "only the first time" are not in Anthropic's docs. Mark both as unverified.
    - "Tool annotations" is not a plugin-bundle check. It is a connector review criterion. Keep the annotations but drop the row, or label it optional.
    - Publishing a passing version needs Publish, and by default a reviewer publishes each version. Pushes to `main` are scanned automatically, but they go live by themselves only if auto-publish applies.
11. **CryoSPARC claims rest on forum posts, not the official guide.** The claims are that EER gains must be 4096x4096 and that `.dm4` can't be read. Keep them, but word them as "as of forum reports (2021/2023)". File: `resources/formats.md`.

---

## A. Cryo-EM conventions

### A1. MotionCor gain flags

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| `-RotGain` 0-3 = rotate counter-clockwise by k x 90 (formats.md and gain.py imply that numpy `rot90(k)` is the same thing) | **Partly** | The help text says "Rotate gain reference counter-clockwise", with 1/2/3 = 90/180/270. The kernel (`G90Rotate2D.cu`) maps `out[r,c] = in[Ny-1-c, r]`. A simulation of the kernel matches `np.rot90(a, -k)`, not `np.rot90(a, k)`. It is counter-clockwise with y pointing up (MRC/IMOD display) and clockwise in numpy/imshow display. | MotionCor3 `CInput.cpp` (help), `MrcUtil/G90Rotate2D.cu`, `CLoadRefs.cpp` | See correction 7. Baking with `-RotGain 0` avoids the problem. |
| `-FlipGain` 1 = upside down, 2 = left-right | **Correct** | Help: "Flip gain reference after gain rotation. 1 - flip upside down, 2 - flip left right". `GFlip2D.cu`: `Vertical` swaps row y with row Ny-1-y (`np.flipud`). `Horizontal` swaps columns (`np.fliplr`). RELION's `--gain_flip` documents the same values. | MotionCor3 `CInput.cpp`, `MrcUtil/GFlip2D.cu`; RELION `src/motioncorr_runner.cpp` (`--gain_flip` help) | None |
| Rotation is applied before the flip (gain.py `transform` does `rot90` then `flipud`) | **Correct** | `PostProcess` calls `mRotate`, then `mFlip`, then `mInverse`. | MotionCor3 `CLoadRefs.cpp::PostProcess` | None |
| "MotionCor multiplies by the gain unless -InvGain is set" | **Correct** | `GApplyGainToFrame.cu` does `gfFrame[i] *= gfGain[i]`. `-InvGain` replaces each value with 1/f (zeros stay zero) and is applied after rotate and flip. The gain must be MRC mode 2 (float32). Otherwise MotionCor3 skips gain correction with a warning. | MotionCor3 `MrcUtil/GApplyGainToFrame.cu`, `CInput.cpp` (InvGain help), `CLoadRefs.cpp::sCheckMode` | None. `convert_gain` already writes float32. |

### A2. Gain convention per detector and per program

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| Gatan K2/K3: raw frames are multiplied by the gain | **Correct** | RELION docs: for K2/K3 the counts are multiplied by the gain reference. MotionCor multiplies. | relion.readthedocs.io Movie Compression page | None |
| Falcon EER, TIFF `.gain` from newer Falcon software: multiply | **Correct** | RELION docs say RELION multiplies when the gain is a TIFF with the `.gain` extension. In the source, `is_multiplicative = (extension == "gain")`. CryoSPARC staff (2021) said production-format EER gains multiply, like other cameras. | RELION `src/renderEER.h::loadEERGain`; relion.readthedocs.io Movie Compression; discuss.cryosparc.com/t/error-when-processing-empiar-10500/7391 | None |
| Falcon EER, MRC gain: divide | **Partly** | True for RELION, which divides for EER and MRC gains. The source decides by extension: anything other than `.gain` is divisive, and it normalises the gain as mean/gain. This is a RELION rule, not a property of the file. A multiplicative MRC fed to RELION for EER is inverted wrongly. | RELION `src/renderEER.h` lines 108-186; Movie Compression page | Correction 2 |
| EER gains can be 4K or 8K | **Correct** | RELION docs: the gain can be 8K x 8K or 4K x 4K, and RELION resamples it to match `--eer_upsampling`. | Movie Compression page; `renderEER.h` | None |
| CryoSPARC requires 4096x4096 for EER gains; convert with `bin=2` | **Partly** (forum only) | Assertion text: "Gain reference for EER must be 4096 x 4096". CryoSPARC staff (Sept 2021): the final Falcon 4 format gain is always 4K. For an old 8K divisive gain, the advice was to downsample to 4K and take the reciprocal. The current guide's Import Movies page doesn't state the size rule. | discuss.cryosparc.com/t/assertionerror-gain-reference-for-eer-must-be-4096-x-4096/6826; .../error-when-processing-empiar-10500/7391; guide.cryosparc.com Import Movies | Correction 11. Add: an old 8K MRC gain also needs `invert=true`. |
| CryoSPARC cannot read `.dm4` gains | **Partly** (forum only, 2023) | A March 2023 traceback reads "Cannot read gain reference of type dm4". Users converted with IMOD `dm2mrc`, which may flip Y. The v5 notes mention TIFF gain support. Nothing found says `.dm4` is supported now. | discuss.cryosparc.com/t/gain-reference-in-dm4-format-what-should-i-do/10795; cryosparc.com/updates/v5 | Correction 11 |
| (extra) MotionCor3 with EER and a TIFF `.gain` | Observation, **Unverified** on data | MotionCor3 loads a TIFF gain through its TIFF reader, which flips rows. It decodes EER frames in file order without a flip. RELION explicitly reverts its TIFF flip for `.gain` with EER ("Revert Y flip in TIFF reader"). From the code alone, MotionCor3 would need `-FlipGain 1` for a raw `.gain` with EER movies. | MotionCor3 `CLoadRefs.cpp::mLoadGainTiff`, `TiffUtil/CLoadTiffImage.cpp`, `EerUtil/CDecodeEerFrame.cpp`; RELION `renderEER.h` | Optional: mention in formats.md as "check". This doesn't affect the bake-to-MRC path. |

### A3. Row order: TIFF and MRC (the key question)

**Verdict: Wrong for TIFF movies. Correct for EER and MRC movies.**

- **RELION** (`src/rwTIFF.h`, comment above the flip loop): MRC has its origin at the bottom left with +Y up, while TIFF (orientation 1) has its origin at the top left. The comment says IMOD, EMAN2, SerialEM and MotionCor2 flip the Y axis whenever they read or write a TIFF, and RELION does the same. It then swaps row y with row Ny-1-y on every TIFF read.
- **MotionCor3** (`TiffUtil/CLoadTiffImage.cpp`, both the strip and tile paths): TIFF strip 0 is written to the last row, so TIFF rows are y-flipped on load. The MRC loader (`LibSrc/Mrcfile/Image/CLoadImage.cpp`) reads rows in file order without a flip. The EER decoder (`EerUtil/CDecodeEerFrame.cpp`) places events at `index % width`, `index / width` in file order without a flip.
- **Python:** `tifffile` returns row 0 as the first TIFF row, which is the top. `mrcfile` returns row 0 as the first stored row (y=0). Neither flips.

The tool's current behaviour: it scores the gain against a movie read with `tifffile`, then writes the matched array with `mrcfile`. For a TIFF movie, MotionCor and RELION then see `movie_mem = flipud(movie_tifffile)` but `gain_mem = gain_matched`. The two are off by one vertical flip, so `-RotGain 0 -FlipGain 0` is wrong. Fix it in either of two ways:

- When the movie is TIFF (not EER), write `np.flipud(g)` to the MRC and keep the `-RotGain 0 -FlipGain 0` advice.
- Or keep the array and advise `-FlipGain 1`. The first is cleaner, and RELION has no flip option in its own motion correction.

For EER movies and MRC movies, file order matches on both sides, so no flip is needed. Inside the tool, comparing a TIFF gain with a TIFF movie is consistent, because both come from `tifffile`. This holds only if `imagecodecs.eer_decode` places rows in file order like RELION and MotionCor3. That is the natural reading of the format, but it was not checked in the imagecodecs C source.

The remaining uncertainty is only whether the claims above hold on real data. They come from source reading, not from a run. One real-data test would settle it:

1. Take one K3 TIFF movie with raw counts and its gain.
2. Run `check_gain_orientation`, then `convert_gain` with the suggested settings.
3. Run MotionCor3 twice on the same movie: once with `-RotGain 0 -FlipGain 0` and once with `-FlipGain 1`. Turn alignment off (`-Patch 0 0 -Iter 0`) or use a single frame.
4. Compare the two corrected sums. The wrong one shows the detector's fixed pattern, for example K3 quadrant or column seams. Measure it as higher power along the axes of the sum's FFT, or as a clear correlation between the sum and the high-pass filtered gain.

Running the same test on one EER movie with an MRC gain checks the EER claim. With a Falcon `.gain`, the tool should report rot0 with no flip, which matches RELION's handling.

| File | Change |
| --- | --- |
| `src/cryoem_files_mcp/gain.py` | In `convert_gain`, take a `movie_format` argument (or `for_tiff_movie: bool`). When the target movie is TIFF, apply `np.flipud` after the user's rotate and flip. Record it in `applied`, for example "flipped upside down for MRC row order (TIFF movies)". `check_gain_orientation` knows the movie format and should pass it on in `suggested`. |
| `README.md` line 80 | Replace the "Assumes MotionCor reads ... same row order" caveat with the finding: MotionCor and RELION flip TIFF rows, and the tool compensates. |
| `README.md` line 68, `SKILL.md` step 2, `formats.md` "Orientation" | Keep `-RotGain 0 -FlipGain 0`, but only once the flip above is in. Otherwise say `-FlipGain 1` for TIFF movies. |

### A4. MRC2014 header

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| nx, ny, nz at bytes 0/4/8; mode at 12; nsymbt at 92; "MAP " at 208; machine stamp at 212; 1024-byte header | **Correct** | CCP-EM table (1-based): NX bytes 1-4, NY 5-8, NZ 9-12, MODE 13-16, NSYMBT 93-96, MAP 209-212, MACHST 213-216. MACHST uses bytes 213-214, and 215-216 are unused. | ccpem.ac.uk/mrc_format/mrc2014.php | None. Accepting `MAP\0` as well is a harmless leniency. |
| Valid modes 0, 1, 2, 3, 4, 6, 12, 101 with byte sizes 1, 2, 4, 4, 8, 2, 2, 0.5 | **Correct** | The CCP-EM list has exactly these modes: 0 int8, 1 int16, 2 float32, 3 complex int16, 4 complex float32, 6 uint16, 12 float16, 101 4-bit. | CCP-EM MRC2014 page | None |
| Mode 101 data size = `((nx+1)//2) * ny * nz` | **Correct** | IMOD: two pixels per byte, lower-coordinate pixel in the low nibble. Each row uses int((nx+1)/2) bytes, so a row with odd nx has 4 bits of padding. MotionCor3 uses the same per-row stride. `mrcfile` does not support modes 3 and 101 as numpy arrays. | bio3d.colorado.edu/imod/doc/mrc_format.txt; MotionCor3 `GApplyGainToFrame.cu`; `mrcfile/utils.py` | None. Note that `mrcfile` can't read mode-101 data. |

### A5. EER

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| BigTIFF with compression 65000-65002 | **Correct** | In tifffile, 65000 is 8-bit RLE (skip 8), 65001 is 7-bit (skip 7), and 65002 is variable, read from tags 65007-65009. `is_eer` requires BigTIFF plus tag 65001 holding XML that starts with `<metadata>`. | tifffile 2026.11.16 `tifffile.py` (lines ~7275-7314, 9337-9344) | None |
| tifffile and imagecodecs decode EER frames | **Correct, with conditions** | Decoding goes through `imagecodecs.eer_decode`. tifffile raises `ValueError` for 65000-65002 pages when the file isn't recognised as EER (tag 65001 metadata missing). | same | Optional: the "unsupported compression" error in `formats.py::decode_page` could mention the missing EER metadata tag. |
| Default decoded resolution | **Correct (4K for Falcon 4)** | `TiffFile(..., superres=None)` defaults to 0, meaning no super-resolution, so frames come out at the stored detector size (4096x4096 on Falcon 4/4i). With superres 1 or 2, width and height scale by 2^min(bits, superres). | tifffile `__init__` docstring and lines ~6582-6589 | None. Worth stating in formats.md: "tifffile decodes EER at 4K by default". |

### A6. STAR

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| formats.md: "STAR: RELION metadata (optics block + data blocks)" | **Correct** | (RELION 3.1 and later layout) | | None |
| meta.py counts fields per loop row with `str.split()` (implied claim: whitespace separates values) | **Wrong in edge cases** | RELION's writer puts values that contain whitespace, or start with a quote, inside double quotes, and writes the empty string as `""`. Its reader accepts single or double quotes, and `#` starts a comment only at the start of a token. The writer appends `# <comment>` after a row's values when the row has an rlnComment. Lines that start with `#` or `;` are skipped. RELION doesn't support multi-line `;` text fields. | RELION `src/strings.cpp` (`escapeStringForSTAR`, `nextTokenForSTAR`), `src/metadata_table.cpp` (~1048, 1177, 1443) | Correction 8 |

---

## B. Publishing

### B7. Official MCP Registry

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| `$schema` `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json` | **Correct (current)** | `pkg/model/constants.go`: `CurrentSchemaVersion = "2025-12-11"`. The changelog's only later work is an unreleased draft. | registry repo `pkg/model/constants.go`, `docs/reference/server-json/CHANGELOG.md` | None |
| Required fields | **Correct** | `ServerDetail` requires `name`, `description` and `version`. `title` is optional (max 100). `name` matches `^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`. `repository` requires `url` and `source`. | `docs/reference/server-json/draft/server.schema.json` | None |
| `description` 100 characters or fewer (current: 95) | **Correct** | maxLength 100. Measured length: 95. | same | None |
| PyPI ownership through `mcp-name:` in the README | **Correct** | The registry looks for `mcp-name: $SERVER_NAME` in the PyPI README. It may sit in an HTML comment, but it must be followed by a boundary such as a space, newline, tag or `-->`. The registry also checks that `packages[].version` exists on PyPI, and gives a "may take a moment" error otherwise. | `docs/modelcontextprotocol-io/package-types.mdx`; `internal/validators/registries/pypi.go` | None. The README marker is in the correct form. |
| "If the registry rejects the capitalisation, change it to lowercase" | **Wrong** | The GitHub login grants `io.github.<username>/*`, and OIDC grants `io.github.<repository_owner>/*`, both using GitHub's casing. `isResourceMatch` is a case-sensitive `HasPrefix`. Lowercasing would cause a permission error. | `internal/api/handlers/v0/auth/github_at.go:297`, `github_oidc.go:293`; `internal/auth/jwt.go::isResourceMatch` | Correction 3 (docs/PUBLISHING.md) |
| `mcp-publisher login github-oidc` in Actions with `id-token: write` | **Correct** | Both match the documented OIDC workflow. | `docs/modelcontextprotocol-io/github-actions.mdx` | None |
| Download mcp-publisher from `releases/latest` | **Wrong (against guidance)** | The docs pin `MCP_PUBLISHER_VERSION`, download the archive plus `.sigstore.json`, run `cosign verify-blob` with the registry release workflow identity, and say not to switch back to the mutable `releases/latest` URL. The doc example uses v1.8.1, and git tags reach at least v1.8.9. | same file | Correction 6 (ci.yml) |
| packageArguments named `--root`, "repeatable" | **Partly** | `NamedArgument` has `type: "named"`, `name` (with dashes) and `isRepeated` (default false). Input fields include `description`, `isRequired`, `format` (`string`, `number`, `boolean`, `filepath`), `default` and `placeholder`. Without `isRepeated: true`, clients assume a single value. | `server.schema.json` definitions `NamedArgument` and `Input` | Correction 5 (server.json) |
| Manual steps: `brew install mcp-publisher`, `validate`, `login github`, the `v0.1/servers?search=` URL | **Correct** | The quickstart shows Homebrew, the `validate` command, `login github`, and `curl ".../v0.1/servers?search=..."`. | `docs/modelcontextprotocol-io/quickstart.mdx` | None |

### B8. Anthropic Claude directory

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| Local servers go in only as a plugin bundle, and MCPB is no longer accepted | **Correct** | Desktop extension listings are deprecated and MCPB local servers are no longer accepted. To distribute a local server, include it in a plugin bundle. | claude.com/docs/directory/publish | None |
| Portal claude.ai/directory/manage → Submit new → Plugin bundle → Validate / Re-validate | **Correct** | Same steps. The Source step fields are Repository, Plugin path (optional) and Branch or tag (optional, defaults to the default branch). The flow continues with Data handling and Compliance steps that PUBLISHING.md doesn't mention. | claude.com/docs/plugins/submit; .../pre-submission-checklist | Optional: add the data handling and compliance steps to PUBLISHING.md. |
| Plans: Pro, Max, Team or Enterprise, with an Owner submitting on Team/Enterprise | **Partly** | The list is Pro, Max, Ultra, Team or Enterprise. On Enterprise, an Owner can also grant a Directory permission. You also need GitHub connected on claude.ai with push access to the repo. | claude.com/docs/directory/publish; .../plugins/submit | Correction 10 |
| Policy hold "Runs a pinned npx or uvx package" | **Correct** | Exact title. An unpinned launcher blocks ("Unpinned uvx launcher"). `uvx pkg==1.2.3` is the documented pinned form. | pre-submission checklist | None |
| README of 40+ words; LICENSE file or `license` field; name not reserved | **Correct** | Words inside code blocks don't count toward the 40. `cryoem-files` is fine. `mcp` or `plugin` as the whole name would block. | pre-submission checklist | None |
| Icon: square PNG, 512-2048 px; becomes the listing icon only on first save | **Unverified** | The docs say only that `icon` is a path to an image inside the plugin, read by the directory, and that the listing reads its details from `plugin.json`. No size rule or "first time only" rule was found in Anthropic docs. The current icon is a 512x512 PNG, which is a complete PNG and allowed. | code.claude.com/docs/en/plugins-reference (Directory listing fields); pre-submission checklist | Correction 10 |
| "Tool annotations (read-only vs writes)" as a directory check | **Partly** | Not among the plugin-bundle checks. Annotations are a connector review topic. The server sets them correctly (`readOnlyHint` and so on). | pre-submission checklist | Correction 10 |
| Privacy policy | **Not required** (as far as found) | `privacyPolicyUrl` is optional. The portal asks data-handling questions instead. | plugins-reference; plugins/submit | Optional: add a `privacyPolicyUrl` to plugin.json. |
| Disclosure: the README must describe what the plugin runs, sends or fetches | **Correct** | The security scan checks undisclosed behaviour. A complete README doesn't make a behaviour allowed. Readable source is required. | pre-submission checklist | None |
| New versions picked up automatically after merging to `main` | **Partly** | Commits on the tracked branch are scanned on a schedule, or immediately with the push webhook. A passing version still needs Publish, and by default a reviewer publishes each version. Auto-publish applies only if Anthropic sets it. | claude.com/docs/plugins/submit | Correction 10 |
| "Listing inside Claude, one-click install with a data-folder prompt" | **Partly** | Chat ignores local MCP servers. Cowork loads them only on the user's computer, and ignores an MCP server that references `${user_config.*}` options without a default (no prompt). Only Claude Code prompts for the values. | claude.com/docs/plugins/platform-support | Correction 4 |

### B9. Claude Code plugin manifest and marketplace

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| plugin.json keys `displayName`, `documentationUrl`, `supportUrl`, `icon` | **Correct** | All four are documented. `displayName` is shown in the UI. The other three are directory listing fields that Claude Code ignores, and `claude plugin validate` before v2.1.281 warns about them as unknown. `homepage` must parse as a URL. `author.url` is valid. Unknown top-level keys are stripped with a warning. | code.claude.com/docs/en/plugins-reference | None |
| `userConfig` `"type": "directory"` with `title`, `description`, `required` | **Correct** | `type` is one of string, number, boolean, directory or file. `title` and `description` are required. Other keys: `required`, `default`, `options`, `multiple`, `sensitive`, `min`/`max`. The object is strict, so an unknown key fails. Keys use letters, digits and underscores. | same | None. A `default` may be worth adding (correction 4). |
| `.mcp.json` at the plugin root | **Correct** | It is the default MCP location and loads first. | same (Standard layout) | None |
| `${user_config.data_folder}` in `args` | **Correct** | It is substituted in MCP server config. For stdio servers, `${...}` resolves in `command`, `args` and `env`. | same | None |
| marketplace.json format (`name`, `owner{name,url}`, `plugins[{name, source:"./plugin", description}]`) | **Correct** | `name`, `owner` and `plugins` are required. `owner.name` is required, and `email` and `url` are optional. An entry requires `name` and `source`, and a relative `./` path is valid. `cryoem-files-mcp` isn't reserved. | code.claude.com/docs/en/plugins/marketplace-reference | None |
| `/plugin install cryoem-files --marketplace MDPuneethReddy/cryoem-files-mcp` | **Correct, with a version caveat** | It is documented, but requires Claude Code v2.1.275 or later. You give the plugin name without `@`. The general form is `/plugin marketplace add owner/repo`, then `/plugin install <plugin>@<marketplace-name>`, where the marketplace name is the `name` in marketplace.json (`cryoem-files-mcp`). | code.claude.com/docs/en/plugins/install.md | Correction 9 |
| `claude plugin validate ./plugin` prints "Validation passed" | **Correct** | The output is `✔ Validation passed`. | pre-submission checklist; plugins-reference | None |

### B10. PyPI trusted publishing

| Claim in repo | Verdict | What sources say | Source | Required change |
| --- | --- | --- | --- | --- |
| Account → Publishing → add a pending publisher with project name, owner, repo and workflow `ci.yml`, environment left empty | **Partly** | The steps and fields are right. The environment is "optional but strongly recommended". A pending publisher does not reserve the name, and if someone else registers the name before the first publish, the pending publisher is invalidated. | docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/ | Correction 6. Add `environment: pypi` to the publish job, enter `pypi` in the form, and note that the name isn't reserved. |
| `pypa/gh-action-pypi-publish@release/v1` with `id-token: write` and no token | **Correct** | Matches the README. The README also advises setting `id-token: write` only on the publish job (done), using an environment with the URL `https://pypi.org/p/<name>`, building in a separate job from publishing (optional here), and pinning to a tag or SHA for stability. It uploads `dist/` by default, which matches `python -m build`. | github.com/pypa/gh-action-pypi-publish | Optional |

## Not verified

- `imagecodecs.eer_decode` row order (assumed to be file order; settled by the A3 test).
- Icon size limits and the "first save" rule for the directory listing icon.
- Whether current CryoSPARC (v5.x) still rejects 8K EER gains and `.dm4` gains (only 2021 and 2023 forum evidence was found).
- MotionCor2 (closed source): assumed to behave like MotionCor3. RELION's source comment says MotionCor2 flips Y on TIFF read and write.
