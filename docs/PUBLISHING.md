# Publishing cryoem-files-mcp

A runbook for releasing. Run the steps in order: each target depends on the one before it.

| # | Where | What it gives you | Who can use it |
| --- | --- | --- | --- |
| 1 | PyPI | `pip install` / `uvx cryoem-files-mcp` | Anyone with Python |
| 2 | Official MCP Registry | A listing at registry.modelcontextprotocol.io that MCP clients and directories read | Users of any MCP client |
| 3 | Claude plugin directory | A listing on claude.ai. Install with a data-folder prompt works **in Claude Code only** (see below) | Claude Code users |

Anthropic's directory no longer accepts MCPB desktop extensions; a local MCP server goes in inside a **plugin bundle** (the `plugin/` folder). Where it runs:

- **Claude Code:** works. Prompts for the data folder (`userConfig.data_folder`, required, no default).
- **Claude Cowork:** skips the server. Cowork never prompts for plugin settings and ignores an MCP server that uses `${user_config.*}` without a default. No default is set on purpose: there is no folder that is safe for everyone (a home-folder default would expose the whole home directory).
- **Claude chat:** does not run local MCP servers.

Sources (checked 2026-11-19): [Publish to the directory](https://claude.com/docs/directory/publish), [Submit your plugin](https://claude.com/docs/plugins/submit), [Plugin pre-submission checklist](https://claude.com/docs/plugins/pre-submission-checklist), [Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference), [MCP Registry GitHub Actions](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/github-actions.mdx), [PyPI: creating a project through OIDC](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/). Details and evidence: `docs/dev/RESEARCH-NOTES.md`.

## Version strings

One version lives in these places; `scripts/check_versions.py` checks them all:

- `pyproject.toml`, `src/cryoem_files_mcp/__init__.py`
- `server.json`: `version` and `packages[0].version`
- `plugin/.claude-plugin/plugin.json` `version`
- the `cryoem-files-mcp==X.Y.Z` pins in `plugin/.mcp.json` and `README.md`
- a `## [X.Y.Z]` entry in `CHANGELOG.md`

## Pre-flight checklist (on the release commit, before any tag)

Run from the repo root. Every line must pass.

```bash
# 1. Tests
pip install -e '.[dev,dm4]'
pytest -q

# 2. Versions + CHANGELOG (the CI gate runs the same with the tag name)
python3 scripts/check_versions.py v0.1.0          # ends with "All versions match."

# 3. Plugin and marketplace manifests (Claude Code CLI)
claude plugin validate --strict --profile directory ./plugin   # "Validation passed", no "Hosts not declared"
claude plugin validate .                                       # marketplace.json: "Validation passed"

# 4. server.json against the 2025-12-11 schema
pip install jsonschema
curl -sSLo /tmp/server.schema.json https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
python3 -c "import json,jsonschema; s=json.load(open('/tmp/server.schema.json')); jsonschema.validators.validator_for(s)(s).validate(json.load(open('server.json'))); print('server.json OK')"
mcp-publisher validate                                         # needs network; checks server.json on the registry side

# 5. Build, check metadata, install the wheel in a clean venv
rm -rf dist && pip install build twine
python3 -m build
twine check --strict dist/*
tar tzf dist/*.tar.gz | grep -E 'pytest_cache|\.mrc$|\.tif$' && echo "UNEXPECTED FILES IN SDIST" || echo "sdist clean"
python3 -m venv /tmp/cryoem-venv && /tmp/cryoem-venv/bin/pip install dist/*.whl
/tmp/cryoem-venv/bin/cryoem-files-mcp --help
/tmp/cryoem-venv/bin/cryoem-files-mcp; echo "exit $? (expect 2: no --root)"
rm -rf dist /tmp/cryoem-venv

# 6. Local install of the plugin from this checkout (Claude Code)
claude plugin marketplace add ./
claude plugin install cryoem-files@cryoem-files-mcp
claude plugin marketplace remove cryoem-files-mcp              # clean up afterwards
```

## Step 0. Create the GitHub repo and push `main` (once)

```bash
# On github.com: create an empty PUBLIC repo MDPuneethReddy/cryoem-files-mcp (no README, no license). Then:
cd cryoem-files-mcp
git remote add origin https://github.com/MDPuneethReddy/cryoem-files-mcp.git
git push -u origin main
```

The name `MDPuneethReddy/cryoem-files-mcp` is used in `server.json`, `README.md`, `plugin/.claude-plugin/plugin.json`, `plugin/README.md`, `.claude-plugin/marketplace.json`, `src/cryoem_files_mcp/server.py` (issues link) and this file. If you change it, search and replace it everywhere.

Then, in the GitHub repo: **Settings > Environments > New environment**, name it `pypi`. Optionally add yourself under **Required reviewers** so every PyPI upload waits for your click.

After this push, Claude Code users can add the marketplace, but the plugin runs `uvx cryoem-files-mcp==0.1.0`, so installs only work once step 1 is done.

## Step 1. PyPI (CI, trusted publishing)

### One-time: pending publisher

On pypi.org (log in, enable 2FA): **Your account > Publishing > Add a new pending publisher > GitHub**:

| Field | Value |
| --- | --- |
| PyPI Project Name | `cryoem-files-mcp` |
| Owner | `MDPuneethReddy` |
| Repository name | `cryoem-files-mcp` |
| Workflow name | `ci.yml` |
| Environment name | `pypi` |

A pending publisher does **not** reserve the name. If someone else registers `cryoem-files-mcp` before your first upload, the pending publisher stops working. So tag soon after adding it, and check https://pypi.org/project/cryoem-files-mcp/ is still free right before.

Optional dry run: create a TestPyPI account and a pending publisher there, then upload a build by hand (`twine upload -r testpypi dist/*`) and try `uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ cryoem-files-mcp==0.1.0 --help`.

### Release

```bash
python3 scripts/check_versions.py v0.1.0
git tag v0.1.0
git push origin v0.1.0
```

CI (`.github/workflows/ci.yml`) then runs, in this order, each job only if the one before passed:

1. `test`: pytest on Python 3.10, 3.12, 3.13, plus `check_versions.py`.
2. `release-gate`: `check_versions.py "$GITHUB_REF_NAME"`; fails unless the tag equals `v` + package version and all versions and the CHANGELOG entry match.
3. `build`: `python -m build`, `twine check --strict`, uploads `dist/` as an artifact.
4. `publish` (environment `pypi`): uploads to PyPI with trusted publishing (no token).
5. `mcp-registry`: see step 2.

A PyPI version can never be uploaded again, even after deletion. If anything fails before `publish`, fix it, delete the tag (`git tag -d v0.1.0 && git push origin :refs/tags/v0.1.0`) and tag again. If `publish` succeeded, the next fix needs a new version number.

Check: https://pypi.org/project/cryoem-files-mcp/0.1.0/ shows the README, and `uvx cryoem-files-mcp==0.1.0 --help` works.

## Step 2. Official MCP Registry (CI, after PyPI)

Ownership is proved by two things already in the repo:

- `server.json` `name`: `io.github.MDPuneethReddy/cryoem-files-mcp`. The case must match your GitHub login exactly; the registry compares it case-sensitively. Do not lowercase it.
- the marker `<!-- mcp-name: io.github.MDPuneethReddy/cryoem-files-mcp -->` near the top of `README.md` (the registry reads it from the PyPI page).

The `mcp-registry` job downloads `mcp-publisher` at a pinned release (`MCP_PUBLISHER_VERSION`, currently `v1.8.9`), verifies its Sigstore bundle with `cosign verify-blob`, waits until `https://pypi.org/pypi/cryoem-files-mcp/<version>/json` answers 200 (up to 10 minutes), logs in with GitHub OIDC and runs `mcp-publisher publish`. To upgrade the publisher, change `MCP_PUBLISHER_VERSION` on purpose; never switch to `releases/latest`.

By hand (if the CI job fails after PyPI succeeded):

```bash
brew install mcp-publisher        # or a verified release from github.com/modelcontextprotocol/registry/releases
mcp-publisher validate
mcp-publisher login github        # device-code login in the browser, as MDPuneethReddy
mcp-publisher publish
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.MDPuneethReddy/cryoem-files-mcp"
```

Common failures: the `server.json` version is not yet on PyPI (wait and retry); `description` over 100 characters (it is 95); README marker missing from the PyPI page (it is in the README, so it ships with the package).

## Step 3. Claude plugin directory (portal, once per plugin)

Requirements:

- A Claude plan that can submit: Pro, Max, Ultra, Team or Enterprise. On Team/Enterprise an Owner submits (on Enterprise, an Owner can also grant a Directory permission).
- GitHub connected on claude.ai (in the organization you submit from), with push access to `MDPuneethReddy/cryoem-files-mcp`.
- The repo public before the listing goes live, and PyPI 0.1.0 live (step 1), because the plugin runs `uvx cryoem-files-mcp==0.1.0`.

Steps:

1. Pre-flight item 3 passes.
2. Open https://claude.ai/directory/manage, select **Submit new**, then **Plugin bundle**.
3. **Source:** Repository `MDPuneethReddy/cryoem-files-mcp`; Plugin path `plugin`; Branch or tag `main` (or leave empty for the default branch).
4. Select **Validate**. Fix anything **Blocking**, push, and validate again (a result applies to one commit).
5. **Listing details:** read from `plugin/.claude-plugin/plugin.json` and `plugin/README.md`. Edit those files, not the portal.
6. **Data handling** (suggested answers, true for this plugin): reads or stores personal data: no (it reads cryo-EM files in the folder the user picks, locally; nothing is stored or sent); sends data to services other than declared connectors: no (the server makes no network requests; `uvx` downloads the package from PyPI on first run, and `uv` may download a Python build); data retention: none; intended for people under 18: no.
7. **Compliance:** check the contact email, select all four acknowledgements (Software Directory Terms and Policy).
8. **Review and submit:** keep **GitHub push webhook** (needs repo admin; **Set up push updates**) or choose **Scheduled check only**. Select **Submit for review**.
9. Expect the policy hold *Runs a pinned npx or uvx package*: a reviewer reads it; it is not a rejection. Track it under **Submissions**.
10. When the version passes, select **Publish**. By default an Anthropic reviewer then publishes each version.

What the checks look at, and status:

| Check | Status |
| --- | --- |
| `.claude-plugin/plugin.json` with name, version, description, author | Done (`cryoem-files`, not a reserved name) |
| README of 40+ words in the plugin folder (code blocks do not count) | Done |
| LICENSE in the plugin folder | Done (BSD-3-Clause) |
| Package launcher pinned to an exact version | Done (`uvx cryoem-files-mcp==0.1.0`) |
| No credentials; user values through `userConfig` | Done (`data_folder`, a directory picker) |
| Network hosts declared (`network.hosts`) | Done: `pypi.org`, `files.pythonhosted.org` (package download); `releases.astral.sh`, `github.com`, `release-assets.githubusercontent.com` (Python build, only if uv must download one) |
| README describes everything the plugin runs, sends or fetches | Done ("What it runs" in `plugin/README.md`) |
| Icon | `.claude-plugin/icon.png`, 512x512 PNG. Size limits and whether the icon updates after the first save are **unverified** (not in Anthropic's docs) |
| Tool annotations (read-only vs writes) | Optional for plugins (a connector review topic); set in the server anyway |

## Releasing a new version (after 0.1.0)

1. Change the version in every place listed under "Version strings", and add a `## [X.Y.Z]` entry to `CHANGELOG.md`.
2. Run the pre-flight checklist.
3. Commit on a branch that is **not** the directory's tracked branch yet (or do not push `main`): `git tag vX.Y.Z && git push origin vX.Y.Z`. CI publishes PyPI, then the registry.
4. When that run is green and PyPI serves X.Y.Z, merge/push to `main`.
5. The directory scans the new commit (webhook or schedule). Each passing version still needs **Publish** in the portal, and by default a reviewer publishes it; versions go live by themselves only if Anthropic has turned on auto-publish for the plugin.

Order matters: if `main` with the new `==` pin reaches the directory before PyPI has that version, installs fail until it does.
