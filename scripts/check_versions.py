"""Fail if the version is not the same everywhere it must match (run before tagging a release).

Checks: pyproject.toml, __init__.py, server.json (top level and every package), plugin.json, the `==` pins in
plugin/.mcp.json and README.md, and a `## [X.Y.Z]` entry in CHANGELOG.md.

Optional: pass a tag (e.g. `v0.1.0`) to also require tag == "v" + version (used by CI before publishing).
"""

import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


server = json.loads(read("server.json"))
found = {
    "pyproject.toml": re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M).group(1),
    "__init__.py": re.search(r'__version__ = "([^"]+)"', read("src/cryoem_files_mcp/__init__.py")).group(1),
    "server.json version": server["version"],
    "plugin.json": json.loads(read("plugin/.claude-plugin/plugin.json"))["version"],
}
for i, pkg in enumerate(server["packages"]):
    found[f"server.json package {i}"] = pkg["version"]
for rel in ("plugin/.mcp.json", "README.md"):
    for j, pin in enumerate(re.findall(r"cryoem-files-mcp==([0-9A-Za-z.+-]+)", read(rel))):
        found[f"{rel} pin {j + 1}"] = pin

for k, v in found.items():
    print(f"{k:28} {v}")

version = found["pyproject.toml"]
errors = []
if len(set(found.values())) != 1:
    errors.append("Versions differ: update them all before tagging.")
if not re.search(rf"^## \[{re.escape(version)}\]", read("CHANGELOG.md"), re.M):
    errors.append(f"CHANGELOG.md has no '## [{version}]' entry.")
if len(sys.argv) > 1 and sys.argv[1] != f"v{version}":
    errors.append(f"Tag {sys.argv[1]!r} does not match package version (expected 'v{version}').")
if errors:
    sys.exit("\n".join(errors))
print(f"{'CHANGELOG.md entry':28} {version}")
print("All versions match.")
