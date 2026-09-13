from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from tools.build_shared_runtime import build_bundle

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "shared_runtime_release_delivery", ROOT / "tools/release_delivery.py"
)
assert SPEC is not None and SPEC.loader is not None
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def _inputs(tmp_path: Path) -> dict[str, Path]:
    """Create the minimal immutable inputs accepted by the bundle builder."""
    inputs = {
        "runtime_wheel": tmp_path / "dcc_mcp_runtime-0.1.0-py3-none-any.whl",
        "runtime_manifest": tmp_path / "manifest.json",
        "adapter_manifest": tmp_path / "obs.json",
        "adapter_wheel": tmp_path / "dcc_mcp_obs-1.4.0-py3-none-any.whl",
        "native_plugin": tmp_path / "dcc-mcp-obs-plugin.zip",
    }
    inputs["runtime_wheel"].write_bytes(b"runtime-wheel")
    inputs["runtime_manifest"].write_text('{"runtime_id":"dcc-mcp-external"}\n')
    inputs["adapter_manifest"].write_text(
        json.dumps({"adapter_id": "obs", "version": "old", "wheel_sha256": "0" * 64})
    )
    inputs["adapter_wheel"].write_bytes(b"adapter-wheel")
    native_payload = b"native-plugin"
    native_manifest = {
        "version": "1.4.0",
        "product": "dcc-mcp-obs",
        "platform": _platform(),
        "files": [
            {
                "source": "payload/plugin",
                "sha256": hashlib.sha256(native_payload).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(inputs["native_plugin"], "w") as archive:
        archive.writestr("dcc-mcp-obs-plugin.json", json.dumps(native_manifest))
        archive.writestr("payload/plugin", native_payload)
    return inputs


def _platform() -> str:
    """Return the release platform identifier for the current test runner."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _build(tmp_path: Path, name: str) -> Path:
    return build_bundle(
        output=tmp_path / name,
        version="1.4.0",
        platform=_platform(),
        runtime_version="0.1.0",
        **_inputs(tmp_path),
    )


def test_shared_runtime_bundle_is_byte_reproducible(tmp_path: Path) -> None:
    first = _build(tmp_path, "first.zip")
    second = _build(tmp_path, "second.zip")

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        for member in archive.infolist():
            assert member.date_time == (1980, 1, 1, 0, 0, 0)
            assert member.compress_type == zipfile.ZIP_DEFLATED
            assert member.create_system == 3
            assert member.external_attr >> 16 == 0o100644


def test_extracted_bundle_exposes_zero_mutation_agent_install_plan(tmp_path: Path) -> None:
    archive_path = _build(tmp_path, "agent.zip")
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)

    result = subprocess.run(
        [sys.executable, str(extracted / "install.py"), "--dry-run"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "planned"
    assert report["dry_run"] is True
    assert report["files_verified"] == 8
    assert report["steps"] == [
        "install-runtime-and-adapter-wheels",
        "install-native-plugin",
    ]

    wrapper = (
        ["pwsh", "-NoProfile", "-File", str(extracted / "install.ps1"), "-DryRun"]
        if sys.platform == "win32"
        else ["bash", str(extracted / "install.sh"), "--dry-run"]
    )
    wrapper_result = subprocess.run(
        wrapper,
        env={**os.environ, "DCC_MCP_INSTALL_PYTHON": sys.executable},
        text=True,
        capture_output=True,
        check=False,
    )
    assert wrapper_result.returncode == 0, wrapper_result.stderr
    assert json.loads(wrapper_result.stdout)["status"] == "planned"


def test_extracted_bundle_rejects_tampered_wheel_before_install(tmp_path: Path) -> None:
    archive_path = _build(tmp_path, "tampered.zip")
    extracted = tmp_path / "tampered"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)
    (extracted / "wheels/dcc_mcp_obs-1.4.0-py3-none-any.whl").write_bytes(b"tampered")

    result = subprocess.run(
        [sys.executable, str(extracted / "install.py"), "--dry-run"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["error"].startswith("BUNDLE_DIGEST_MISMATCH:")


def test_release_validator_accepts_complete_bundle(tmp_path: Path) -> None:
    archive_path = _build(tmp_path, "complete.zip")

    delivery._validate_shared_runtime_archive(
        archive_path,
        archive_path.read_bytes(),
        "1.4.0",
        _platform(),
        "0.1.0",
        (tmp_path / "dcc_mcp_obs-1.4.0-py3-none-any.whl").read_bytes(),
        (tmp_path / "dcc-mcp-obs-plugin.zip").read_bytes(),
    )


def test_release_validator_rejects_bundle_without_runtime_wheel(tmp_path: Path) -> None:
    archive_path = _build(tmp_path, "complete.zip")
    with zipfile.ZipFile(archive_path) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    runtime_name = "wheels/dcc_mcp_runtime-0.1.0-py3-none-any.whl"
    contents.pop(runtime_name)
    manifest = json.loads(contents["dcc-mcp-obs-runtime.json"])
    manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != runtime_name]
    contents["dcc-mcp-obs-runtime.json"] = (json.dumps(manifest) + "\n").encode()
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, payload in contents.items():
            archive.writestr(name, payload)

    with pytest.raises(ValueError, match="shared runtime payload incomplete"):
        delivery._validate_shared_runtime_archive(
            archive_path,
            archive_path.read_bytes(),
            "1.4.0",
            _platform(),
            "0.1.0",
            b"adapter-wheel",
            (tmp_path / "dcc-mcp-obs-plugin.zip").read_bytes(),
        )
