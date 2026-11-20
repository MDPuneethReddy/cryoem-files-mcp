# Release plan: cryoem-files-mcp v0.1.0

Owner: PM. Written 2026-11-19 against commit `16a00a5` (code as read before the current Developer pass). Inputs: `docs/QA-REPORT.md`, code, docs, plugin, CI. Release = PyPI, then the official MCP Registry, then the Claude plugin directory (`plugin/`), in that order (see `docs/PUBLISHING.md`).

## 1. Goal and scope

**Goal:** a safe, honest first release that a cryo-EM user can install from the plugin directory or `uvx`, point at a session folder, and get correct damage reports and a gain-orientation answer that never overstates its confidence.

**Ships in v0.1.0**

| Area | Content |
| --- | --- |
| Tools (9) | `identify_file`, `inspect_mrc`, `inspect_movie`, `inspect_gain`, `check_gain_orientation`, `convert_gain`, `summarize_star`, `parse_mdoc`, `scan_directory` |
| Extras | Resources `cryoem://formats`, `cryoem://config`; prompts `diagnose_dataset`, `prepare_for` |
| Packaging | PyPI wheel + sdist, `server.json` for the MCP Registry, `plugin/` bundle (MCP server via pinned `uvx`, skill `cryoem-file-check`, `userConfig.data_folder`) |
| Quality bar | All QA High/Medium fixed, blocking Lows fixed, PM blockers fixed, regression test for each, independent QA re-test |

**Explicitly deferred (not in v0.1.0; README must not imply them)**

| Deferred | Target |
| --- | --- |
| Validation on real EMPIAR data (K3 TIFF, Falcon 4 EER, real `.dm4` gains) and a real MotionCor run with a converted gain | v0.1.x, before claiming "validated" |
| Real EER decoding test (no EER writer available) | v0.1.x |
| Leginon / Magellon-specific tools and layouts | v0.2 |
| Windows and macOS support claims (CI is Linux only) | v0.2 |
| Network-filesystem hang protection beyond the S_ISREG check (timeouts on stat/open) | v0.2 |
| L5, L6, L7, L8, PM-7, PM-8 (non-blocking Lows) | v0.1.1 unless fixed for free |

## 2. Release blockers: QA findings

Each criterion must be proved by a regression test (Tester) and by a black-box re-test over stdio from the built wheel (QA).

| ID | Sev | Blocks | Acceptance criterion |
| --- | --- | --- | --- |
| H1 | High | Yes | - [ ] `mkfifo root/fifo.mrc`; `identify_file("fifo.mrc")` returns within 2 s with an error/problem saying it is not a regular file (no open attempted). <br>- [ ] Same for a Unix socket. <br>- [ ] `scan_directory(".")` with a FIFO present returns within 2 s, skips it and reports "1 special file skipped". <br>- [ ] Every reader (`inspect_mrc`, `inspect_movie`, `inspect_gain`, `check_gain_orientation`, `convert_gain`, `summarize_star`, `parse_mdoc`) on the FIFO returns a clear error within 2 s; MCP session still answers the next call. |
| H2 | High | Yes | - [ ] Repeat QA's 30 trials (256x256 movie from gain A, checked against unrelated gain B): 0 runs report high or medium; all return an explicit "no match: this gain does not belong to these movies" result with **no** `next_step` recommending `convert_gain`. <br>- [ ] Output includes the significance score (z = abs(r)·sqrt(n)) and n. <br>- [ ] All 8 variants in `test_orientation_recovers_every_variant` still return high. |
| M1 | Med | Yes | - [ ] `ln -s /etc/passwd root/leak.mrc`; `scan_directory(".")` output contains no bytes or size from the target (no `72 6f 6f 74`), counts it under "N links to outside the allowed folders skipped", and the target path is not printed. <br>- [ ] A symlink to a file inside the root is still scanned normally. |
| M2 | Med | Yes | - [ ] `convert_gain(invert=true, bin=2)` equals `1/bin2(g)` (bin first, then invert) to float32 tolerance on a gain with one defect pixel; QA's simulated defect-pixel error is about 1%, not 44%. <br>- [ ] `applied` lists the steps in the order performed. |
| M3 | Med | Yes | - [ ] QA's Falcon-style 8K gain (4% variation) vs 4K movie returns `rot0`, confidence high. <br>- [ ] 2% and 1% variation at 0.5 e/px/frame return the correct variant at high (or medium with the documented margin). <br>- [ ] Skill and server `instructions` say when to convert (high, or medium with a clear margin) and when to stop (low, no match), using the exact field names and values the tool returns. |
| M4 | Med | Yes | - [ ] `scan_directory` has a `max_depth` argument in the tool schema; default at least 8. <br>- [ ] File at `a/b/c/d/e/f/hidden_movie.tif` is found with the default. <br>- [ ] With `max_depth=2`, the result says "N deeper folders not scanned" instead of "folder is empty". |
| M5 | Med | Yes | - [ ] With 10,000 MRC files and one truncated at position ~1,200, the result either reports it as damaged or states "header-checked 500 of 10,000 MRC files" (exact counts). <br>- [ ] When all are checked, no such note appears. |
| M6 | Med | Yes | - [ ] STAR row `'my mic 1.mrc' 12000` in a 2-column loop gives no row problem; double quotes too. <br>- [ ] A real 3-values-in-2-columns row is still reported with its line number. |
| M7 | Med | Yes | - [ ] 64x64x64 MRC: `role_guess` = 3D map. <br>- [ ] 1x256x256 MRC: 2D image (not movie). <br>- [ ] `.mrcs` 100x64x64: particle stack. <br>- [ ] 40x4096x4096 MRC: movie. |
| M8 | Med | Yes | - [ ] `cryoem-files-mcp --root /nonexistent` exits non-zero within 2 s with "root ... does not exist" on stderr. <br>- [ ] `--root some_file.txt` exits non-zero with "is not a folder". <br>- [ ] Same for `CRYOEM_MCP_ROOTS`. Valid roots still start. |
| M9 | Med | Yes | - [ ] `plugin/README.md` "What it runs" states the server makes no network requests and that `uvx` downloads the pinned package and dependencies from PyPI on first run (and may download a Python interpreter). <br>- [ ] Matches the `network.hosts` declaration (PM-2). |
| L1 | Low | Yes | - [ ] `tools/list` shows a human `title` on all 9 tools. Cheap; shown in Claude's UI and the directory listing. |
| L2 | Low | Yes | - [ ] `identify_file` on a folder returns an "is a folder" error, not `integrity: damaged`. <br>- [ ] On an unrecognised file it returns `integrity: unknown format`, never "header ok". Reason: "header ok" contradicts the skill rule "never claim a file is fine". |
| L3 | Low | Yes | - [ ] `scan_directory(limit=-5)` and `limit=0` are rejected with a clear message; same for `sample_frames<1`, `n_frames<1` (already) and `max_depth<1`. Trivial; current output is wrong data. |
| L4 | Low | Yes | - [ ] Missing-file error uses the cause/fix format and shows the path relative to the root. <br>- [ ] Symlink-to-outside error does not print the outside target path. Security-adjacent, the directory scan reads error text. |
| L5 | Low | No | EPU `*_fractions.mrc` counted as movies; tiny text `.tif` not counted. Heuristic only; note in README limits. Target v0.1.1. |
| L6 | Low | No | No "RELION 3.0-style" note for `model.star` / `job.star`. A note, not a problem; v0.1.1. |
| L7 | Low | No | One format-name vocabulary across tools. Cosmetic; fix if touched. |
| L8 | Low | No | Friendlier argument errors. QA confirmed they are already clear; SDK-level. |

**QA blockers: 15** (H1, H2, M1-M9, L1-L4).

## 3. Additional release gates and new PM issues

### 3a. New issues found by PM review

| ID | Sev | Blocks | Finding | Acceptance criterion |
| --- | --- | --- | --- | --- |
| PM-1 | High | Yes | Install command in `README.md`, `plugin/README.md` and `docs/PUBLISHING.md` is invalid: `/plugin install cryoem-files --marketplace ...`. The CLI has no `--marketplace` flag; it takes `plugin@marketplace` (`claude plugin install --help`). Every user's first command fails. | - [ ] All three files use `/plugin marketplace add MDPuneethReddy/cryoem-files-mcp` then `/plugin install cryoem-files@cryoem-files-mcp` (marketplace name from `.claude-plugin/marketplace.json`). <br>- [ ] Release engineer runs both commands against a local checkout (`claude plugin marketplace add ./`) and the plugin installs. |
| PM-2 | Med | Yes | `claude plugin validate ./plugin` passes but warns "Hosts not declared: list what these parts reach under network.hosts in plugin.json". `uvx` reaches PyPI. | - [ ] `plugin.json` declares `network.hosts` (at least `pypi.org`, `files.pythonhosted.org`; plus what `uv` uses to fetch Python if applicable; Researcher confirms the exact list and schema). <br>- [ ] `claude plugin validate ./plugin` prints no "Hosts not declared" line. |
| PM-3 | Med | Yes | With no `--root`, the root is the current directory (`paths.set_roots`). Clients often launch servers with cwd `/` or `$HOME`, so the whole disk becomes readable. `server.json` marks `--root` optional and does not list `CRYOEM_MCP_ROOTS`. | - [ ] Starting with no `--root`/env from cwd `/` (and from `$HOME`) exits non-zero with "pass --root" (or an equivalent explicit refusal); from a normal folder, behaviour is documented. <br>- [ ] `server.json`: `--root` `isRequired: true` (or documented default that is safe) and `CRYOEM_MCP_ROOTS` listed under `environmentVariables`; `mcp-publisher validate` passes. <br>- [ ] README "Default" sentence matches. Fix together with M8. |
| PM-4 | Med | Yes | `check_gain_orientation.next_step`, `convert_gain.use_with_motioncor`, the skill and `formats.md` tell users to run MotionCor with `-RotGain 0 -FlipGain 0` as fact, but the README admits the tifffile/mrcfile vs MotionCor row-order assumption is unverified. A wrong assumption silently flips every converted gain. | - [ ] `next_step` and the skill say to confirm on one movie first (run MotionCor on one movie with the converted gain, check the corrected sum shows no fixed pattern) until real-data validation is done. <br>- [ ] README "Known limits" states the same in its first bullet. |
| PM-5 | Low | Yes | `_call` converts only `ValueError`/`OSError`. Anything else (MemoryError on a huge gain, a library `RuntimeError`/`KeyError`, an unexpected EER layout) reaches the model as the SDK's opaque "Error executing tool X". With untested real data this is the main support path. | - [ ] A forced unexpected exception (monkeypatch a reader to raise `RuntimeError("boom")`) returns a tool error containing the exception type, message and "please report at .../issues". <br>- [ ] No traceback text in the message. |
| PM-6 | Med | Yes | CI publishes to PyPI on any `v*` tag without running `scripts/check_versions.py` or checking tag == package version; `mcp-publisher` is fetched from `latest` (unpinned); registry job waits a fixed `sleep 60`. A PyPI version can never be re-uploaded, so a mistake is permanent. | - [ ] Test job runs `python scripts/check_versions.py` and fails if `GITHUB_REF_NAME` != `v<version>`. <br>- [ ] `mcp-publisher` pinned to a specific release. <br>- [ ] Registry job polls `https://pypi.org/pypi/cryoem-files-mcp/<version>/json` until 200 (with timeout) instead of `sleep 60`. <br>- [ ] Optional but recommended: one TestPyPI dry run before the real tag. |
| PM-7 | Low | No | `convert_gain.use_with_motioncor` appends "(multiplicative)" only when `invert=false`, though the output is multiplicative either way; README table says "New file only" though `overwrite=true` replaces. | - [ ] Text says the output is always multiplicative; table says "New file (overwrite only if asked)". v0.1.1 if not free. |
| PM-8 | Low | No | `meta.py` imports `pandas` directly; it is only a transitive dependency of `starfile`. | - [ ] `pandas` listed in `pyproject.toml` dependencies. v0.1.1 if not free. |

**PM blockers: 6** (PM-1 to PM-6). **Total blockers: 21.**

### 3b. Release gates (all must pass on the release candidate commit)

- [ ] **G1 Tests:** `pytest -q` passes locally and in CI on Python 3.10, 3.12, 3.13; test count is higher than 65 and includes a named regression test for every blocking ID above.
- [ ] **G2 Versions:** `python scripts/check_versions.py` prints "All versions match." at 0.1.0 (six places).
- [ ] **G3 Plugin:** `claude plugin validate ./plugin` prints "Validation passed" with no warnings (including network hosts).
- [ ] **G4 Wheel:** `python -m build`; in a fresh venv `pip install dist/*.whl` (no dev extras); `cryoem-files-mcp --help` works; `cryoem_files_mcp/resources/formats.md` is in the wheel; `uvx --from dist/<wheel> cryoem-files-mcp --root <tmp>` starts.
- [ ] **G5 sdist content:** `tar tzf dist/*.tar.gz` contains no `.pytest_cache`, data files or local paths.
- [ ] **G6 MCP smoke (stdio, from wheel):** `tools/list` returns 9 tools with titles and correct read-only/destructive annotations; `resources/list` has 2; `prompts/list` has 2; one call to each tool on a synthetic session succeeds.
- [ ] **G7 Registry metadata:** `mcp-publisher validate` passes; `server.json` description <= 100 chars; README line 3 `mcp-name` marker equals `server.json` `name`.
- [ ] **G8 Docs match behaviour:** README tool table matches `tools/list` (names, args such as `max_depth`, write behaviour); skill steps use only real tool names, argument names and returned field values (confidence levels, "no match"); server `instructions` and both prompts do not recommend converting without checking confidence.
- [ ] **G9 Error catalogue:** `python scripts/error_catalog.py` regenerates `docs/error-messages.md` with no unexpected diff; new messages (special file, outside link, no match) added.
- [ ] **G10 Disclosure:** `plugin/README.md` "What it runs" accurately lists: process run (`uvx cryoem-files-mcp==0.1.0`), network (PyPI download on first run only), reads (chosen folder only), writes (`convert_gain` only). Same facts in root README "Safety".
- [ ] **G11 Install path:** the PM-1 commands work end to end in Claude Code on a clean machine after the PyPI upload (post-publish check, before directory submission).

## 4. Roles, order and Definition of Done

Order: **Developer -> Tester + QA (in parallel) -> Developer (leftovers) -> Release engineer -> PM sign-off.** Nobody pushes or tags before PM sign-off.

| Step | Role | Scope | Definition of Done |
| --- | --- | --- | --- |
| 1 | Developer | Fix all 21 blockers; fix non-blocking Lows only if trivial. Edits `src/`, `plugin/`, `README.md`, `docs/PUBLISHING.md`, `server.json`, CI. | - [ ] Each blocker has a code/doc change referenced by ID in the commit message. <br>- [ ] `pytest` green, G2 and G3 pass locally. <br>- [ ] Short change log per ID handed to Tester and QA. <br>- [ ] No behaviour change outside the listed IDs. |
| 2a | Tester | Regression tests in `tests/` for every blocking ID, using the acceptance inputs above. | - [ ] One test (or parametrised set) per ID, named `test_<id>_...`. <br>- [ ] Each test fails on `16a00a5` and passes on the fix (proved by running it against both). <br>- [ ] H1 tests have a timeout so a regression fails instead of hanging CI. |
| 2b | QA | Independent black-box re-test from a freshly built wheel in a clean venv, over stdio with a real MCP client, using the original QA repros plus PM-1 to PM-6. | - [ ] Each blocking ID marked pass/fail with evidence in an appendix to `docs/QA-REPORT.md`. <br>- [ ] Re-runs the "What passed" table (no regressions in path safety, stats, performance, handle leaks). <br>- [ ] Lists any new finding with severity. |
| 3 | Developer | Fix anything Tester or QA failed; new High/Medium findings go back to step 2. | - [ ] Zero open blockers; QA re-confirms the failed items. |
| 4 | Release engineer | Gates G1-G10; TestPyPI dry run; prepare tag. Follows `docs/PUBLISHING.md`. | - [ ] Gate checklist in section 3b ticked with command output saved. <br>- [ ] PyPI pending publisher configured; CI release-path changes (PM-6) verified on TestPyPI. <br>- [ ] Release notes drafted (scope, known limits from section 5). <br>- [ ] Tag not pushed until PM signs off. |
| 5 | PM | Sign-off. | - [ ] All blockers closed with test + QA evidence. <br>- [ ] Gates G1-G10 green; G11 scheduled immediately after PyPI. <br>- [ ] README limits text matches section 5. <br>- [ ] Owner (Puneeth) approves push, tag and directory submission. |

## 5. Known risks at launch and README wording

| Risk | Impact | Mitigation in v0.1.0 | README must say (in "Status and known limits") |
| --- | --- | --- | --- |
| Not validated on real data (K3, Falcon 4 EER, real `.dm4`) | Wrong answer on a format quirk the synthetic tests miss | PM-5 error path; issue link; deferred validation | "Tested only on synthetic data. Not yet validated on real K3, Falcon 4 or EER datasets; please report results." |
| MotionCor row order vs tifffile/mrcfile unverified | Converted gain could be flipped for every user | PM-4 wording | "Before processing a whole dataset with a converted gain, run MotionCor on one movie and check the result." (first bullet) |
| EER decoding never exercised on a real file | EER inspection may fail or mis-report | Clear error path (PM-5) | "EER support is untested on real files." |
| `.dm4` via `ncempy` untested on real Gatan gains | Wrong orientation/values | Optional extra | Keep the existing `dm2mrc` cross-check sentence. |
| `mcp>=2.3,<3` (SDK 2.x, `MCPServer` API, released recently) | Breakage on SDK minor updates or with older clients | Upper bound `<3`; CI on 3 Pythons | "Requires the MCP Python SDK 2.3 or newer (installed automatically)." |
| `uvx` required for the plugin and Desktop configs | Install fails without uv; first run needs internet | M9/PM-2 disclosure | "Requires uv (uvx). The first run downloads the package from PyPI; after that it runs offline." |
| Stale network mounts (GPFS/NFS) can still hang `stat`/`open` | Session hangs on facility storage | H1 fixes FIFOs only | "On network filesystems a stale mount can make a call hang; restart the server if a call does not return." |
| Linux only tested (Python 3.10-3.13 in CI; QA on 3.13) | Unknown behaviour on macOS/Windows | Defer claims | "Tested on Linux. macOS and Windows are untested." |
| Registry name capitalisation (`io.github.MDPuneethReddy`) may be rejected | Registry publish fails after PyPI is live | Fallback in `PUBLISHING.md` | (none; release-engineer risk) |
| Directory policy hold for a pinned `uvx` package | Listing delayed | Expected, documented | (none) |
| Silent scan limits (depth, header-check cap) | User trusts an incomplete scan | M4/M5 make limits explicit | "scan_directory reports when it did not look at everything; read its notes." |
