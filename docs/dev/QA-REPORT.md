# QA report: cryoem-files-mcp 0.1.0

Tested 2026-11-19 against the built wheel installed in a clean virtualenv, mostly through a real MCP client over stdio (black-box), with in-process runs for memory and handle counts.

**Verdict: not ready to publish.** Two high-severity bugs: a special file in a data folder hangs the server, and the orientation check confidently recommends a rotation for a gain that does not belong to the movies. Core correctness is otherwise strong: every orientation was found correctly, statistics match numpy exactly, and performance is fine at full detector size.

## Summary

| ID | Severity | Area | Finding |
| --- | --- | --- | --- |
| H1 | High | Robustness | A named pipe (FIFO) or other non-regular file hangs the server forever (`identify_file`, `scan_directory`, every reader) |
| H2 | High | Correctness | With a gain from a different detector, 12 of 30 runs return a "best" orientation at **medium** confidence, with no warning and a recommendation to convert |
| M1 | Medium | Security | `scan_directory` follows symlinks to files outside the allowed folders and returns their first bytes (leaked `root:x:0` from `/etc/passwd`) |
| M2 | Medium | Correctness | `convert_gain(invert=true, bin=2)` inverts before binning: 44% error at every binned pixel containing a defect (1% if binned first) |
| M3 | Medium | Usability | Confidence is under-reported for correct answers on realistic data (Falcon 8K: right answer, "medium"); the bundled skill only converts on "high", so the plugin workflow stalls |
| M4 | Medium | Correctness | `scan_directory` silently ignores files more than 4 folder levels deep and reports "folder is empty" |
| M5 | Medium | Correctness | `scan_directory` header-checks only the first 500 MRC files and does not say so |
| M6 | Medium | Correctness | Legal STAR values in quotes (`'my mic 1.mrc'`) are reported as damaged rows |
| M7 | Medium | Correctness | Role guess: a 64³ map is called a "particle stack"; a one-section MRC (1×256×256) is called a "movie" |
| M8 | Medium | Startup | A nonexistent folder or a file as `--root` is accepted; every call then fails with "does not exist" |
| M9 | Medium | Disclosure | Plugin README says it "makes no network requests"; `uvx` downloads the package from PyPI on first run. The directory's security scan checks disclosures |
| L1 | Low | Directory | No tool has a `title` |
| L2 | Low | Wording | A folder passed to `identify_file` reports `integrity: damaged`; an unrecognised file reports "header ok" |
| L3 | Low | Validation | `scan_directory(limit=-5)` is accepted and reports `files_scanned: -5` |
| L4 | Low | Errors | Missing-file errors show absolute paths and lack the cause/fix format; a symlink error reveals the outside target path |
| L5 | Low | Heuristic | EPU `*_fractions.mrc` movies are not counted as movies, so no missing-gain warning; a 3-byte text `.tif` is counted as a movie |
| L6 | Low | Wording | model.star / job.star get "no optics block: RELION 3.0-style", which is wrong for those files |
| L7 | Low | Consistency | Format names differ between tools (`tiff` in `inspect_gain`, `TIFF` in `identify_file`) |
| L8 | Low | Cosmetic | Argument errors show raw pydantic text with documentation URLs |

## Details and reproduction

### H1: special files hang the server
- **Repro:** `mkfifo data/fifo.mrc`, then call `identify_file(path="fifo.mrc")` or `scan_directory(path=".")`. No response; the client was killed after 20 s. The whole MCP session is stuck.
- **Cause:** `check_basic` opens the file and reads a byte; opening a FIFO for reading blocks until a writer appears. `scan_directory` calls the same check on every `.mrc`.
- **Real-world risk:** FIFOs are rare, but the same blocking happens on stale network mounts (GPFS/NFS), which are common at facilities.
- **Fix:** reject anything that is not a regular file (`stat.S_ISREG`) before opening, with a clear message; skip and count such entries in `scan_directory`.

### H2: confident answer for a gain that does not match
- **Repro:** 30 trials, each with a 256×256 movie made from one gain and checked against a different random gain of the same size. Results: 0 high, **12 medium**, 18 low. The medium ones carry no warning and a `next_step` that says to convert.
- **Cause:** confidence uses a fixed |r| > 0.1 threshold and the ratio to the runner-up. With unrelated data every |r| is noise (~0.005), so a ratio above 1.5 happens by chance.
- **Fix:** score by significance. Under no match, r ≈ N(0, 1/√n) for n pixels compared. Use z = |r|·√n. Report "no match: this gain does not belong to these movies" when the best z is below about 5. Base high/medium on z and on z(best) − z(runner-up). This also fixes M3.

### M1: scan reads files outside the allowed folders
- **Repro:** inside the root, `ln -s /etc/passwd leak.mrc`, then `scan_directory(".")`. The output includes `first bytes: 72 6f 6f 74 3a 78 3a 30` (`root:x:0`). File sizes of outside targets are also counted.
- **Fix:** in `scan_directory`, skip symlinks whose resolved target is outside the roots (and say how many were skipped).

### M2: invert/bin order for divisive gains
- **Repro:** simulated divisive 8K gain with one defect pixel, raw counts proportional to the gain, summed 2×2 to 4K. Corrected with the tool's output: defect-pixel error 44.3%; with bin-then-invert: 1.0%. Pattern residuals are otherwise the same.
- **Fix:** when both are requested, bin first, then invert.

### M3: confidence too low on correct, realistic results
- Falcon-style 8K gain (4% pixel variation) vs 4K movie: correct `rot0`, reported **medium**.
- 2% and 1% pixel variation at 0.5 e/px/frame: correct, |r| 10× the runner-up, reported medium.
- The skill tells Claude to convert only when confidence is high, so users with typical data never get the conversion. Fixed by the significance score in H2; then loosen the skill to "high, or medium with a clear margin".

### M4 and M5: silent limits in `scan_directory`
- A file at `a/b/c/d/e/f/hidden_movie.tif` is not found and the result says "folder is empty". EPU layouts (`Images-Disc1/GridSquare_*/Data/`) are close to the limit.
- 10,000 MRC files: the truncated one at position ~1,200 was caught only by the size heuristic, not by the header check, which stops at 500.
- **Fix:** expose `max_depth`, raise the default, and report "N deeper folders not scanned" and "header-checked 500 of 10,000 MRC files".

### M6: quoted STAR values
- **Repro:** a loop row `'my mic 1.mrc' 12000` gives "line 6: 4 values but 2 columns", although `starfile` parsed it correctly.
- **Fix:** split rows with `shlex.split` (or a quote-aware regex) in `_loop_row_problems`.

### M7: role guess
- **Cause:** the "particle stack" test (`shape[1] < 1024`) runs before the cube test, and `nz == 1` is not treated as 2D.
- **Fix:** check `nz == 1` → 2D image, then cube → map, then `.mrcs` or many small sections → particles.

### M8: bad `--root`
- **Fix:** at startup, refuse a root that does not exist or is not a folder, and print the reason to stderr.

### M9: network disclosure
- **Fix:** "The server makes no network requests and sends no data anywhere. On first run, `uvx` downloads the pinned package and its dependencies from PyPI."

## What passed

| Area | Result |
| --- | --- |
| Install | Clean venv from wheel; entry point and `--help` work; version strings match |
| MCP schemas | 9 tools; required args correct; read-only/destructive annotations correct |
| Bad arguments | Wrong types, missing args, bad enums and unknown tools are all rejected with clear errors |
| Path safety | `..` escapes, absolute paths and symlinks outside the root are refused for reads and for `out=`; overwrite protection works; no partial files |
| Orientation correctness | All valid variants found correctly on rectangular K3-shaped gains; super-resolution movies (2× binning) and 8K-vs-4K gains work |
| Stats and conversion | MRC statistics and voxel size match numpy exactly; `convert_gain` transforms match numpy for every rotate/flip combination |
| STAR / mdoc | 200,000-particle STAR: rows, micrographs, class counts and median defocus all exact, in 0.6 s, output 1 KB. CRLF STAR and Windows-style SerialEM mdoc parsed correctly |
| Damaged files | The 65-test suite passes; truncation, corrupted frames and wrong extensions give exact messages |
| Resource use | 1,600 calls including error paths: no file-handle leaks. 24 concurrent calls in one session: all succeed (1.1 s) |
| Performance | K3 5760×4092×40: inspect 1.8 s, orientation 3.0 s, verify all frames 0.8 s, peak 0.6 GB RAM. 8K Falcon gain: orientation 8.5 s, convert 0.7 s, peak 0.8 GB. Scan of 20,000 files: 0.7 s |
| Odd names | Spaces, accents and a leading `-` in filenames work |

## Not tested
- Real data: EMPIAR K3 and Falcon 4 EER datasets, real `.dm4` gains, a MotionCor run with a converted gain.
- Real EER decoding (no EER writer is available to generate a test file).
- Windows and macOS; Python 3.10–3.12 (only 3.13 here; CI covers 3.10/3.12/3.13).
- End to end inside Claude Code or Claude Desktop with the plugin installed.
- Network filesystems (stale mounts are the realistic version of H1).
