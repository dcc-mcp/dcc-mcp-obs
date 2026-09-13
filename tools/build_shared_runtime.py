"""Build a deterministic OBS adapter bundle for the shared runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of one release input."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_member(archive: zipfile.ZipFile, name: str, payload: bytes | str) -> None:
    """Write one reproducible, read-only regular-file ZIP member."""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def build_bundle(
    *,
    output: Path,
    version: str,
    platform: str,
    runtime_version: str,
    runtime_wheel: Path,
    runtime_manifest: Path,
    adapter_manifest: Path,
    adapter_wheel: Path,
    native_plugin: Path,
) -> Path:
    """Build a deterministic runtime/adapter/native archive."""
    adapter_metadata = json.loads(adapter_manifest.read_text(encoding="utf-8"))
    adapter_metadata["version"] = version
    adapter_metadata["wheel_sha256"] = _sha256(adapter_wheel)
    files = {
        "install.py": (ROOT / "packaging" / "install_runtime.py").read_bytes(),
        "install.ps1": (ROOT / "packaging" / "install.ps1").read_bytes(),
        "install.sh": (ROOT / "packaging" / "install.sh").read_bytes(),
        "runtime/manifest.json": runtime_manifest.read_bytes(),
        "runtime/manifests/obs.json": (json.dumps(adapter_metadata, indent=2) + "\n").encode(),
        f"wheels/dcc_mcp_runtime-{runtime_version}-py3-none-any.whl": runtime_wheel.read_bytes(),
        f"wheels/dcc_mcp_obs-{version}-py3-none-any.whl": adapter_wheel.read_bytes(),
        "native/dcc-mcp-obs-plugin.zip": native_plugin.read_bytes(),
    }
    entries = [
        {"path": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        for name, payload in sorted(files.items())
    ]
    manifest = {
        "schema_version": 1,
        "product": "dcc-mcp-obs-runtime",
        "version": version,
        "platform": platform,
        "runtime_version": runtime_version,
        "files": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_member(
            archive,
            "dcc-mcp-obs-runtime.json",
            json.dumps(manifest, indent=2) + "\n",
        )
        for name, payload in sorted(files.items()):
            _write_member(archive, name, payload)
    return output


def main() -> int:
    """Parse the release-build contract and create one shared-runtime bundle."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--runtime-version", required=True)
    parser.add_argument("--runtime-wheel", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--adapter-manifest", type=Path, required=True)
    parser.add_argument("--adapter-wheel", type=Path, required=True)
    parser.add_argument("--native-plugin", type=Path, required=True)
    args = parser.parse_args()
    build_bundle(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
