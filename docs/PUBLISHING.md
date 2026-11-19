# Publishing cryoem-files-mcp

There are three places to publish, in this order. Each one depends on the one before it.

| Where | What it gives you | Who finds it |
| --- | --- | --- |
| 1. PyPI | `pip install` / `uvx cryoem-files-mcp` | Anyone with Python |
| 2. Official MCP Registry | A listing at registry.modelcontextprotocol.io that MCP clients and directories pull from | Users of any MCP client |
| 3. Claude plugin directory | A listing inside Claude (Customize page), one-click install with a data-folder prompt | Claude users |

Anthropic's directory does not list local MCP servers (like this one) on their own, and no longer accepts MCPB desktop extensions. A local server goes into the directory inside a **plugin bundle**; that is what the `plugin/` folder is. Sources: [Publish to the directory](https://claude.com/docs/directory/publish), [Plugin pre-submission checklist](https://claude.com/docs/plugins/pre-submission-checklist), [MCP Registry quickstart](https://modelcontextprotocol.io/registry/quickstart) (checked 2026-11-19).

## 0. Push the code to GitHub

```bash
# create an empty public repo named cryoem-files-mcp under github.com/MDPuneethReddy, then:
cd cryoem-files-mcp
git remote add origin https://github.com/MDPuneethReddy/cryoem-files-mcp.git
git push -u origin main
```

The repo name and owner are used in `server.json`, `plugin/.claude-plugin/plugin.json`, `plugin/README.md` and `.claude-plugin/marketplace.json`. If you use a different name, search for `MDPuneethReddy/cryoem-files-mcp` and update all of them.

Right after this push, Claude Code users can already install it from your repo:

```
/plugin install cryoem-files --marketplace MDPuneethReddy/cryoem-files-mcp
```

(The plugin runs `uvx cryoem-files-mcp==0.1.0`, so it only works once step 1 is done.)

## 1. PyPI (automated by CI)

One-time setup: on pypi.org, go to **Your projects > Publishing > Add a new pending publisher** and enter:

- PyPI project name: `cryoem-files-mcp`
- Owner: `MDPuneethReddy`, repository: `cryoem-files-mcp`
- Workflow: `ci.yml`, environment: leave empty

Then every release is:

```bash
python scripts/check_versions.py      # all six version strings must match
git tag v0.1.0 && git push origin v0.1.0
```

CI runs the tests, builds, and publishes to PyPI without a token (trusted publishing). Check https://pypi.org/project/cryoem-files-mcp/.

## 2. Official MCP Registry (automated by CI after PyPI)

The registry stores metadata only and checks that you own the PyPI package. Two things make that work, both already in this repo:

- `server.json` at the repo root, with `name: io.github.MDPuneethReddy/cryoem-files-mcp`
- the line `<!-- mcp-name: io.github.MDPuneethReddy/cryoem-files-mcp -->` at the top of `README.md` (PyPI shows the README, and the registry reads it there)

The `mcp-registry` job in `.github/workflows/ci.yml` runs after the PyPI job on each tag. It logs in with GitHub OIDC (no secrets) and runs `mcp-publisher publish`.

To do it by hand instead (first time, or if CI fails):

```bash
brew install mcp-publisher          # or download from github.com/modelcontextprotocol/registry/releases
mcp-publisher validate              # checks server.json
mcp-publisher login github          # opens a device-code login in the browser
mcp-publisher publish
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.MDPuneethReddy/cryoem-files-mcp"
```

Rules that cause most failures:

- The `server.json` version must equal a version that is already live on PyPI.
- `description` in `server.json` must be 100 characters or fewer (it is 95).
- With GitHub login, the name must start with `io.github.<your-github-username>/`. If the registry rejects the capitalisation, change it to `io.github.mdpuneethreddy/cryoem-files-mcp` in both `server.json` and the README marker, and release a new version.

## 3. Claude plugin directory (manual, once)

Requirements: a paid Claude plan (Pro, Max, Team or Enterprise; on Team/Enterprise an Owner submits) and a public GitHub repo.

1. Check locally: `claude plugin validate ./plugin` (should print `Validation passed`).
2. Open https://claude.ai/directory/manage, select **Submit new**, choose **Plugin bundle**.
3. Source: repository `MDPuneethReddy/cryoem-files-mcp`, plugin folder `plugin`, branch `main`.
4. Select **Validate**. Fix anything marked **Blocking** and select **Re-validate**.
5. Submit for review. Expect one **Policy hold** that is normal for this design: *Runs a pinned npx or uvx package*. A reviewer reads it; it is not a rejection.
6. Track the status in the portal. After it is published, new versions are picked up automatically when you merge to `main` (each one is scanned again).

What the reviewer and scanner look at, and how this plugin meets it:

| Check | Status |
| --- | --- |
| `.claude-plugin/plugin.json` with name, version, description, author | Done (`cryoem-files`, not a reserved word) |
| README of 40+ words in the plugin folder | Done |
| LICENSE in the plugin folder | Done (BSD-3-Clause) |
| Package launcher pinned to an exact version | Done (`uvx cryoem-files-mcp==0.1.0`) |
| No credentials; user values through `userConfig` | Done (`data_folder`, a directory picker) |
| README describes everything the plugin runs, sends or fetches | Done ("What it runs" section) |
| Icon, 512-2048 px square PNG | Done (`.claude-plugin/icon.png`). It becomes the listing icon only the first time you save or submit, so replace it before then if you want a different one. |
| Tool annotations (read-only vs writes) | Done in the server code |

## Releasing a new version

1. Change the version in all six places: `pyproject.toml`, `src/cryoem_files_mcp/__init__.py`, `server.json` (twice), `plugin/.claude-plugin/plugin.json`, and the `==` pin in `plugin/.mcp.json`.
2. `python scripts/check_versions.py` and `pytest`.
3. Commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. CI publishes PyPI, then the registry.
4. When that CI run is green, `git push origin main`. The directory picks up the plugin change from `main`.

The order matters: if `main` (with the new `==` pin) reaches the directory before PyPI has that version, installs fail until it does.
