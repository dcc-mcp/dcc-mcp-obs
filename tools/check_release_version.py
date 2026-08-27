from __future__ import annotations

import os
from pathlib import Path

import tomllib

version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
tag = os.environ.get("GITHUB_REF_NAME", "")
if tag != f"v{version}":
    raise SystemExit("release tag does not match the package version")
