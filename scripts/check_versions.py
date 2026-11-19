"""Fail if the version is not the same everywhere it must match (run before tagging a release)."""

import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
found = {
    "pyproject.toml": re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1),
    "__init__.py": re.search(r'__version__ = "([^"]+)"', (root / "src/cryoem_files_mcp/__init__.py").read_text()).group(1),
    "server.json version": json.loads((root / "server.json").read_text())["version"],
    "server.json package": json.loads((root / "server.json").read_text())["packages"][0]["version"],
    "plugin.json": json.loads((root / "plugin/.claude-plugin/plugin.json").read_text())["version"],
    "plugin .mcp.json pin": re.search(r"cryoem-files-mcp==([^\"]+)", (root / "plugin/.mcp.json").read_text()).group(1),
}
for k, v in found.items():
    print(f"{k:24} {v}")
if len(set(found.values())) != 1:
    sys.exit("Versions differ: update them all before tagging.")
print("All versions match.")
