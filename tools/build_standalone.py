"""Build and archive the self-contained DCC-MCP OBS sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
OUTPUT = DIST / "standalone"
MANIFEST_NAME = "dcc-mcp-obs-standalone.json"
PLUGIN_NAME = "dcc-mcp-obs-plugin.zip"
CONFIG = ROOT / "packaging" / "standalone.toml"


def _binary_name(platform: str) -> str:
    return "dcc-mcp-obs.exe" if platform == "windows" else "dcc-mcp-obs"


def _find_binary(platform: str) -> Path:
    name = _binary_name(platform)
    matches = sorted(path for path in BUILD.rglob(name) if "standalone" not in path.parts)
    if not matches:
        raise FileNotFoundError(f"PyOxidizer did not produce {name} under {BUILD}")
    return matches[-1]


def _reset_output() -> None:
    resolved = OUTPUT.resolve()
    if resolved.parent != DIST.resolve() or resolved.name != "standalone":
        raise ValueError("refusing to reset an unexpected standalone output path")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def _copy_runtime(binary: Path, platform: str, plugin_archive: Path | None) -> None:
    shutil.copy2(binary, OUTPUT / binary.name)
    runtime = binary.parent / "lib"
    if runtime.is_dir():
        shutil.copytree(runtime, OUTPUT / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    if platform == "windows":
        for library in binary.parent.glob("*.dll"):
            shutil.copy2(library, OUTPUT / library.name)
    if plugin_archive is not None:
        if not plugin_archive.is_file():
            raise FileNotFoundError(f"native plugin archive does not exist: {plugin_archive}")
        shutil.copy2(plugin_archive, OUTPUT / PLUGIN_NAME)
    shutil.copy2(ROOT / "docs" / "standalone.md", OUTPUT / "README.md")


def _payload_files() -> list[Path]:
    return sorted(
        path for path in OUTPUT.rglob("*") if path.is_file() and path.name != MANIFEST_NAME
    )


def write_manifest(platform: str, version: str, core_version: str) -> Path:
    files = []
    for path in _payload_files():
        payload = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(OUTPUT).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    if not files:
        raise ValueError("standalone payload is empty")
    manifest = {
        "schema_version": 1,
        "product": "dcc-mcp-obs-standalone",
        "version": version,
        "core_version": core_version,
        "platform": platform,
        "files": files,
    }
    path = OUTPUT / MANIFEST_NAME
    path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def create_archive(platform: str, version: str) -> Path:
    base = DIST / f"dcc-mcp-obs-{version}-{platform}-standalone"
    files = sorted(path for path in OUTPUT.rglob("*") if path.is_file())
    if platform == "windows":
        archive_path = Path(f"{base}.zip")
        if archive_path.exists():
            archive_path.unlink()
        with zipfile.ZipFile(
            archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in files:
                archive.write(path, path.relative_to(OUTPUT).as_posix())
        return archive_path
    archive_path = Path(f"{base}.tar.gz")
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "x:gz") as archive:
        for path in files:
            relative = path.relative_to(OUTPUT).as_posix()
            info = archive.gettarinfo(str(path), arcname=relative)
            if path.name == _binary_name(platform):
                info.mode = stat.S_IMODE(info.mode) | 0o111
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    return archive_path


def _version() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["version"]


def _core_version() -> str:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    version = config["core_version"]
    if (
        not isinstance(version, str)
        or not version
        or any(part == "" or not part.isdigit() for part in version.split("."))
    ):
        raise ValueError("standalone Core version must be an exact numeric version")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows", "macos", "linux"), required=True)
    parser.add_argument("--plugin-archive", type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    core_version = _core_version()
    subprocess.run(
        [
            "pyoxidizer",
            "build",
            "--release",
            "--path",
            str(ROOT),
            "--var",
            "core_version",
            core_version,
            *(["--verbose"] if args.verbose else []),
        ],
        cwd=ROOT,
        check=True,
    )
    binary = _find_binary(args.platform)
    _reset_output()
    _copy_runtime(binary, args.platform, args.plugin_archive)
    version = _version()
    write_manifest(args.platform, version, core_version)
    print(create_archive(args.platform, version))


if __name__ == "__main__":
    main()
