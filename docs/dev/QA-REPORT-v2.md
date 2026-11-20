# QA report v2: cryoem-files-mcp 0.1.0 (re-test)

Tested 2026-11-19 against commit `18fbba8`, black-box. The repo was copied to a snapshot (`qa/src_v2`), the wheel was built with `python -m build`, and the wheel was installed in a new clean venv (`qa/venv2`: mcp 2.3.0, numpy 2.5.5, tifffile 2026.11.16, pandas 3.1.1). Everything ran through a real MCP stdio client with a per-call timeout (a hung call counts as FAIL). Peak memory is the server's VmHWM. Scripts and data are in `/home/claude/qa/t2` and `/home/claude/qa/d2`.

Publishing files (README install line, `plugin/README.md`, `plugin.json`, `server.json`, CI) were being edited by the Release Engineer during this pass. The PM-1/2/6 and M9 rows below refer to the **uncommitted working tree** at the time of testing.

## Verdict: not ready (close)

- All 15 original QA blockers pass, including both Highs.
- PM-3, PM-4 and PM-8 pass. PM-1, PM-2 and PM-6 pass in the working tree. PM-5 passes for exceptions raised inside a tool, but not for failures while the result is serialised (N1).
- The core claim holds. For all 48 combinations (8 orientations × 3 gain kinds × TIFF/MRC movie), applying the advised `convert_gain` call in a numpy emulation of MotionCor/RELION gave a flat corrected sum (CV 0.046 = Poisson floor; a wrong gain gives 0.31). The same was true for every `motioncor_flags_for_original_gain`.
- **New: 5 Medium and 11 Low findings.** No new High. Per RELEASE-PLAN step 3, the Mediums go back to the Developer. N1–N3 are small code fixes; N4 and N5 are advice/doc fixes.

## Install and startup

| Check | Result |
| --- | --- |
| Wheel builds; clean venv install; `cryoem-files-mcp` console script; `--help` lists `--root`, `--timeout` | PASS |
| `formats.md` in wheel; `pandas` in Requires-Dist (PM-8) | PASS |
| No root (cwd `/` or elsewhere) → exit 2, explains why | PASS |
| `--root /nonexistent`, `--root file.txt`, `CRYOEM_MCP_ROOTS=/nope` → exit 2, "does not exist" / "is a file, not a folder" | PASS |
| `--timeout -1` / `abc` rejected | PASS |
| Relative `--root`, a root that is itself a symlink, `CRYOEM_MCP_ROOTS` with 2 paths and `os.pathsep` (empty segments ignored) | PASS (relative paths resolve against the first root; `--root` overrides the env var) |

## Original findings

| ID | Result | Evidence |
| --- | --- | --- |
| H1 | PASS | FIFO and Unix socket each sent through all 9 tools: every call returns in ≤0.01 s (`summarize_star` 0.95 s cold import) with "is a named pipe (FIFO) / is a socket, not a regular file, so it was not opened". `scan_directory` reports `special_files: 2`, and the next call works. |
| H2 | PASS | 30 trials (256² movie from gain A vs unrelated gain B): 30 `no_match`, 0 `next_step`, max z = 3.5. Output has `z_best`, `z_gap` and `pixels_compared`. All 8 variants on 256×384 gains are `high` (z ≈ 126). |
| M1 | PASS | `leak.mrc -> /etc/passwd` and `etcdir -> /etc`: not read, counted as `outside_symlinks: 2`, only the link name printed. An inside symlink is scanned normally. |
| M2 | PASS | `invert=true, bin=2` equals `1/bin2(g)` exactly. `applied` lists binned, then inverted. Error at the defect's binned pixel is 0.0%. |
| M3 | PASS | 8K Falcon-style vs 4K movie: `high`, z = 183. 2% and 1% variation at 0.5 e/px/frame, 512²: `high` (z 31, 16). Server instructions, skill and prompts use the real field values. |
| M4 | PASS | `a/b/c/d/e/f/hidden_movie.tif` found with the default depth. With `max_depth=2`: "1 folder deeper than max_depth=2 not scanned". |
| M5 | PASS | 10,000 MRC files: `mrc_headers_checked: "500 of 10000"` plus a note. With 50 files: "51 of 51" and no note. (The note says the rest were "checked by size", but only movie extensions get the size check.) |
| M6 | PASS | Single- and double-quoted values, `""` and a trailing `# comment` give no row problem. The real 3-in-2 row is reported as "line 9". See N7 for remaining STAR gaps. |
| M7 | PASS | 64³ → 3D map; 1×256×256 → 2D image; `.mrcs` 100×64×64 → particle stack; 40×4096×4096 → movie. |
| M8 | PASS | See Install and startup. |
| M9 | PASS (working tree) | `plugin/README.md` discloses the PyPI download and the possible uv Python download, and these match `network.hosts`. |
| L1 | PASS | All 9 tools have a `title`, and the annotations are correct. |
| L2 | PASS | A folder gives "is a folder, not a file". An unrecognised file gives `integrity: "unrecognised format: not checked"`. |
| L3 | PASS | `limit=-5` or `0`, `max_depth=0`, `sample_frames=0` and `n_frames=0` are all rejected with one clear line. |
| L4 | PASS | A missing file shows the relative path plus cause and fix. A symlink-escape error does not show the target. |
| L5, L6, L7, L8 | PASS | `*_fractions.mrc` is counted as a movie. A 3-byte `.tif` is not. `model.star` / `job.star` get no RELION 3.0 note. Format names are consistent. Argument errors are one line with no pydantic URLs. |
| PM-1, PM-2 | PASS (working tree) | `claude plugin validate ./plugin` prints "Validation passed" with nothing to report. The two-step install command is documented. |
| PM-3, PM-4 | PASS | No cwd fallback. `check_first` appears in every `next_step` and convert result, and is the README's first limit. |
| PM-5 | **PARTIAL** | A forced MemoryError gives "internal error in check_gain_orientation: MemoryError: … Please report at …/issues" with no traceback. But a failure while the result is serialised gives a bare "Error executing tool scan_directory" (N1). |
| PM-6 | PASS (working tree, not run) | `check_versions.py` runs against the tag; `MCP_PUBLISHER_VERSION` is pinned to v1.8.9 with cosign; CI polls the PyPI JSON. |
| "What passed" re-run | PASS | Path safety for reads and `out=` (`..`, absolute paths, symlinked dirs, a symlinked target file: all refused; the victim file is untouched). Stats match numpy. 1,600 mixed calls including error paths: fd count stays at 8. 24 concurrent mixed calls: 0 errors in 1.06 s. 30× sequential `overwrite=true`: OK. Unicode, `-rf`, newline and quote characters, a 254-character name and a 1,000-character path all work for reads. Windows-style mdoc parses. |

## Performance (full size, one session per case)

| Case | Time | Peak RSS | Result |
| --- | --- | --- | --- |
| K3 5760×4092×40: `inspect_movie` / `identify_file(verify_data)` / `verify_all_frames` | 0.34 / 0.67 / 1.08 s | 0.52 / 0.12 / 0.52 GB | ok |
| K3 `check_gain_orientation(n_frames=40)` | 3.5 s | 0.64 GB | high, rot180, z = 506 |
| K3 `convert_gain(movie_type='tiff')` | 0.31 s | 0.31 GB | flipped rows |
| Falcon 8K gain: `inspect_gain` / orientation vs 4K movie / `convert_gain(bin=2)` | 1.0 / 9.1 / 0.68 s | 0.86 / 0.87 / 0.40 GB | high, rot0, z = 183 |
| STAR, 2,000,000 rows (117 MB) | 6 s | 1.26 GB | exact counts |
| `scan_directory`, 10,000 files | 0.24 s | n/a | ok |

## Verdicts and advice, end to end

- **Verdicts.** All four appear where they should:
  - `high` when the signal is clear.
  - `medium` for a 1024² gain with 0.25% variation (z = 8.2, gap 6.6). It comes with a warning and "Probably: …" `next_step`.
  - `low` for a flip-symmetric gain (gap 0). No `next_step`.
  - `no_match` for unrelated gains and for a gain-normalised float movie, which also gets the float note.
- **Advice simulation** (`t2/e2e.py`):
  - The movie is read the way MotionCor/RELION read it: TIFF rows flipped, MRC rows as stored.
  - The gain is produced by executing the exact `convert_gain(...)` string from `next_step.motioncor` over MCP.
  - The corrected sum is flat in 48/48 cases.
  - `next_step.relion` equals `next_step.motioncor` for TIFF and MRC movies, as the research notes require.
  - `motioncor_flags_for_original_gain` was emulated as `rot90(g, -k)` → flip → invert, and it is correct in 32/32 MRC-gain cases.
- **`convert_gain` outputs:**
  - `movie_type='tiff'` adds the row flip and says so in `applied` and `row_order`.
  - `'eer'` and `'mrc'` make no flip.
  - Omitting `movie_type` adds a `row_order` warning.
  - `'TIFF'` is rejected with a clear message.
  - `out=` into a new nested Unicode folder works.
- **Not testable:** RELION/EER branches with a real EER file. A hand-made EER did not decode reliably with `imagecodecs`; that remains deferred, as the plan says.

## New findings

### Medium

**N1. A non-UTF-8 filename makes `scan_directory` fail with no message (PM-5 gap).**
- Repro: `python3 -c "open(b'root/bad\xffutf8.gain','wb').write(b'x')"`, then call `scan_directory(".")`.
- Expected: a summary, with the name shown escaped.
- Actual: `Error executing tool scan_directory` and nothing else. Stderr shows `PydanticSerializationError: … surrogates not allowed`, raised by the SDK while converting the result, outside `_call`.
- Scope: one badly named file (common after Windows/Samba copies) breaks the whole session scan whenever that name lands in `gain_references_found` or `newest_file`.
- Fix: sanitise every path string in results, for example `os.fsdecode` → `encode('utf-8', 'backslashreplace')`.

**N2. A valid MRC mode 101 (SerialEM 4-bit) file is reported as damaged.**
- Repro: a correct mode-101 file, 63×64×10, 21,504 bytes. Call `identify_file(verify_data=true)`, `inspect_mrc` or `inspect_movie`.
- Expected: recognised. The 4-bit size check itself works: the truncated copy is correctly reported as "100 bytes missing".
- Actual: `integrity: damaged`, "MRC header was read but its data block could not be mapped. Fix: re-copy the file…".
- Mode 3 behaves the same. `formats.md` and the research notes list both modes as valid. Telling users to re-copy good K2/K3 4-bit movies is wrong advice.
- Fix: header and size check only (`mrcfile` cannot map these modes), with "data not decoded: mode 101 is not supported for pixel statistics".

**N3. `scan_directory` names no file in its damaged-MRC problem.**
- Actual: `"2 damaged MRC files; first: is truncated: the header describes 16x16x1 float32 data …"`. The EPU fixture behaves the same.
- Expected: the file name or names. The skill's step 2 ("for each file it flags, run identify_file") cannot be followed literally.
- Fix: prefix the relative path and list up to about 5 files.

**N4. Gain 2× the movie size: `next_step` omits `bin=2` for MotionCor/RELION with TIFF or MRC movies.**
- Repro: `check_gain_orientation(gain=Falcon_8k.gain, movie=F4_movie.tif)` (8K gain, 4K TIFF).
- Actual: `next_step.motioncor` = `convert_gain(rotate=0, flip='none', movie_type='tiff')`, which writes an 8192² gain. `next_step.size` says to add `bin=2` only "if your program needs a gain of the movie's size (CryoSPARC for EER…)".
- Expected: MotionCor and RELION need a gain the same size as TIFF/MRC frames (RELION resamples only for EER). Include `bin=2` in those calls, or state it in `motioncor`/`relion`.
- Impact: a likely loud size-mismatch failure, not a silent one. Not verified on the real programs.

**N5. `prepare_for(pipeline="CryoSPARC")` refers to a `next_step` entry that does not exist.**
- Actual: the prompt says "as tool calls (from check_gain_orientation's next_step for this pipeline…)", but `next_step` has only `motioncor`, `relion`, `check_first` and `size`.
- A model following it will improvise. The likely result is the MotionCor file, whose TIFF row flip has not been researched for CryoSPARC (CryoSPARC has its own gain flip/rotate options).
- Fix: add a `cryosparc` entry, or say in the prompt that only MotionCor/RELION advice is provided. This bears on gate G8.

### Low

- **N6. `convert_gain` silently keeps NaN/Inf gain pixels.** For `nanrows.gain` (3.9% NaN), `inspect_gain` flags it, but the convert result has no warning and the MRC still holds NaN rows (with `invert=false`). MotionCor multiplies, so those rows become NaN. Fix: warn, or zero non-finite pixels as the invert path already does.
- **N7. STAR edge cases.**
  - `mic#1.mrc` (`#` inside a token is legal in RELION) rejects the whole file with pandas text "Expected 1 fields in line 2, saw 2". That is the wrong line, and "fix or remove the listed rows" lists no rows.
  - A `;` line inside a loop is counted as a data row: rows 4 instead of 3, plus spurious problems. This is a `starfile` limitation.
- **N8. Raw library errors without file, cause or fix:**
  - RGB TIFF to `inspect_movie`: "non-broadcastable output operand with shape (64,3)…".
  - A 254-character name to `convert_gain`: "[Errno 36] File name too long: '/home/…'" (absolute path).
  - A bad mdoc value: pydantic text with an `errors.pydantic.dev` URL.
  - `inspect_mrc` on a TIFF: "is TIFF, but this tool needs MRC." (no fix).
- **N9. Memory exhaustion while decoding is misreported** as "frame 1 of 1 cannot be decoded … compressed data … corrupted … re-copy the file" (`inspect_gain` under `ulimit -v 1 GB`).
- **N10. Timeouts abandon threads that keep computing.** With `--timeout 1.5`, 5 retries of the 8K orientation check give a 2.58 GB peak (0.86 GB for 1). README says a stuck thread remains only for hung mounts. Rare at the default 300 s.
- **N11. Concurrent `convert_gain` calls to the same `out=` share one `.partial` temp name.** Of 24 parallel calls, 4–8 fail with "could not be written: No such file or directory. Likely cause: no write permission or the disk is full", which is misleading. The final file was always intact (5/5 small, 6/6 K3-size runs).
- **N12. `out=` equal to the input with `overwrite=true` silently writes `<name>_converted.mrc`** and overwrites that file instead, without saying so. README says it "refuses to overwrite … its input unless overwrite=true".
- **N13. Mode-12 (float16) MRC gets `mrc2014_valid: false`** because `mrcfile` computes an RMS of inf. `validation_issues` includes the absolute path.
- **N14. The `no_match` text overstates.** A correct but weak gain (0.25% variation, 512², z = 4.2) is told it "belongs to a different detector or session". Suggest "no significant match (could also be too little signal: raise n_frames)".
- **N15. The sdist includes whatever is untracked in the checkout:** `docs/RELEASE-PLAN.md`, `RESEARCH-NOTES.md` and `QA-REPORT*.md` in this build. Build releases from a clean tag checkout, or exclude these in `pyproject`.
- **N16. Minor:**
  - `--root /` is accepted without a warning.
  - `convert_gain` returns an absolute `output` path, while other results use relative paths.
  - `summarize_star` peaks at about 10× the file size (1.26 GB for 117 MB), so a 10M-particle STAR needs about 6 GB.
  - `motioncor_flags_for_original_gain` on a converted MRC says "not with a converted one", although the flags are correct for that file.

## Docs vs behaviour

- **README:**
  - The tool table matches `tools/list`, including `max_depth`, `limit`, `movie_type` and the write behaviour.
  - The verdict table matches the thresholds the tool returns.
  - The Safety section is accurate except for N12 and the N10 nuance.
- **SKILL.md:** followed literally, it produces correct calls and field names (`confidence`, `next_step.motioncor/relion/check_first`, `warning`, `problem`, `z_best`/`z_gap`, `motioncor_flags_for_original_gain`). Two exceptions:
  - Step 2 of the session flow breaks on N3.
  - The `high` rule converts an 8K gain at the wrong size for TIFF movies (N4).
- **Server instructions, `diagnose_dataset`, `formats.md`:** consistent with the research facts (TIFF flip, RELION EER extension rule, `-RotGain (4-k)%4`, forum-sourced CryoSPARC claims). `prepare_for` fails for CryoSPARC (N5).
