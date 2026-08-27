"""Create the canonical, installer-verifiable native plugin bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from collections.abc import Sequence
from pathlib import Path

import tomllib


def collect_payload(root: Path, platform: str) -> list[tuple[Path, str]]:
    root = root.resolve()
    if platform == "windows":
        plugin = root / "dcc-mcp-obs"
        binary = _one(plugin.glob("bin/64bit/dcc-mcp-obs.dll"))
        payload = [(binary, "bin/64bit/dcc-mcp-obs.dll")]
        data = plugin / "data"
        payload.extend(_tree(data, "data"))
        return payload
    if platform == "linux":
        binary = _one(root.glob("lib/*/obs-plugins/dcc-mcp-obs.so"))
        payload = [(binary, "bin/64bit/dcc-mcp-obs.so")]
        data = root / "share" / "obs" / "obs-plugins" / "dcc-mcp-obs"
        payload.extend(_tree(data, "data"))
        return payload
    if platform == "macos":
        plugin = root / "dcc-mcp-obs.plugin"
        return _tree(plugin, "")
    raise ValueError("unsupported platform")


def create_bundle(root: Path, platform: str, version: str, output: Path) -> None:
    payload = collect_payload(root, platform)
    if not payload:
        raise ValueError("native payload is empty")
    manifest_files: list[dict[str, object]] = []
    for source, target in payload:
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("native payload must contain only independent regular files")
        data = source.read_bytes()
        manifest_files.append(
            {
                "source": f"payload/{target}",
                "target": target,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest = {
        "schema_version": 1,
        "product": "dcc-mcp-obs",
        "version": version,
        "platform": platform,
        "files": manifest_files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        bundle.writestr(
            "dcc-mcp-obs-plugin.json",
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        )
        for (source, target), entry in zip(payload, manifest_files, strict=True):
            assert entry["target"] == target
            bundle.write(source, f"payload/{target}")


def _tree(root: Path, target_prefix: str) -> list[tuple[Path, str]]:
    if not root.is_dir():
        raise ValueError("native payload directory is missing")
    values: list[tuple[Path, str]] = []
    for source in sorted(root.rglob("*")):
        if source.is_symlink():
            raise ValueError("native payload links are not allowed")
        if source.is_file():
            relative = source.relative_to(root).as_posix()
            target = f"{target_prefix}/{relative}".strip("/")
            values.append((source, target))
    return values


def _one(values) -> Path:
    matches = list(values)
    if len(matches) != 1:
        raise ValueError("native plugin binary is missing or ambiguous")
    return matches[0]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--platform", choices=("windows", "macos", "linux"), required=True)
    parser.add_argument("--version")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    version = args.version
    if version is None:
        pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
        version = pyproject["project"]["version"]
    output = args.output or Path(f"dcc-mcp-obs-{version}-{args.platform}.zip")
    create_bundle(args.root, args.platform, version, output)


if __name__ == "__main__":
    main()
