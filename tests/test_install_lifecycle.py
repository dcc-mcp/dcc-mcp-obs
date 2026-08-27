from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import jsonschema
import pytest

from dcc_mcp_obs.install_cli import RECEIPT_NAME, run

ROOT = Path(__file__).parents[1]
INSTALL_SOP_SCHEMA = json.loads(
    (ROOT / "contracts" / "adapter-install-sop-v1.schema.json").read_text(encoding="utf-8")
)


def _bundle(tmp_path: Path) -> tuple[Path, str, bytes]:
    payload = b"native-plugin-binary"
    platform = (
        "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    )
    manifest = {
        "schema_version": 1,
        "product": "dcc-mcp-obs",
        "version": "0.1.0",
        "platform": platform,
        "files": [
            {
                "source": "payload/dcc-mcp-obs.plugin",
                "target": "bin/dcc-mcp-obs.plugin",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    archive = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("dcc-mcp-obs-plugin.json", json.dumps(manifest))
        package.writestr("payload/dcc-mcp-obs.plugin", payload)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest(), payload


def test_full_install_status_verify_uninstall_lifecycle(tmp_path: Path) -> None:
    archive, digest, payload = _bundle(tmp_path)
    target = tmp_path / "installed"

    code, planned = run(
        [
            "install",
            "--plugin-archive",
            str(archive),
            "--sha256",
            digest,
            "--plugin-dir",
            str(target),
            "--dry-run",
        ]
    )
    assert code == 0 and planned["status"] == "planned" and not target.exists()
    jsonschema.Draft202012Validator(INSTALL_SOP_SCHEMA).validate(planned)

    code, installed = run(
        [
            "install",
            "--plugin-archive",
            str(archive),
            "--sha256",
            digest,
            "--plugin-dir",
            str(target),
        ]
    )
    assert code == 0 and installed["status"] == "requires_restart"
    assert installed["verify"] == {
        "directly_usable": False,
        "failure_stage": "host-readiness",
        "failure_reason": "LIVE_OBS_VERIFICATION_REQUIRED",
    }
    jsonschema.Draft202012Validator(INSTALL_SOP_SCHEMA).validate(installed)
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == payload
    assert (target / RECEIPT_NAME).is_file()

    assert run(["status", "--plugin-dir", str(target)])[0] == 0
    assert run(["verify", "--plugin-dir", str(target)])[0] == 0
    code, removed = run(["uninstall", "--plugin-dir", str(target)])
    assert code == 0 and removed["status"] == "ok" and not target.exists()
    jsonschema.Draft202012Validator(INSTALL_SOP_SCHEMA).validate(removed)


def test_digest_mismatch_and_receipt_link_fail_closed(tmp_path: Path) -> None:
    archive, digest, _payload = _bundle(tmp_path)
    target = tmp_path / "installed"

    code, report = run(
        [
            "install",
            "--plugin-archive",
            str(archive),
            "--sha256",
            "0" * 64,
            "--plugin-dir",
            str(target),
        ]
    )
    assert code == 20 and report["verify"]["failure_reason"] == "OBS_BUNDLE_DIGEST_MISMATCH"
    assert not target.exists()

    assert (
        run(
            [
                "install",
                "--plugin-archive",
                str(archive),
                "--sha256",
                digest,
                "--plugin-dir",
                str(target),
            ]
        )[0]
        == 0
    )
    alias = tmp_path / "receipt-alias.json"
    try:
        import os

        os.link(target / RECEIPT_NAME, alias)
    except OSError:
        return
    code, report = run(["verify", "--plugin-dir", str(target)])
    assert code == 40 and report["verify"]["failure_reason"] == "OBS_RECEIPT_INVALID"


def test_reports_never_contain_obs_password(tmp_path: Path, monkeypatch) -> None:
    archive, digest, _payload = _bundle(tmp_path)
    target = tmp_path / "installed"
    monkeypatch.setenv("DCC_MCP_OBS_WEBSOCKET_PASSWORD", "PRIVATE_OBS_PASSWORD")

    _code, report = run(
        [
            "install",
            "--plugin-archive",
            str(archive),
            "--sha256",
            digest,
            "--plugin-dir",
            str(target),
        ]
    )

    assert "PRIVATE_OBS_PASSWORD" not in json.dumps(report)
    assert "PRIVATE_OBS_PASSWORD" not in (target / RECEIPT_NAME).read_text(encoding="utf-8")


def test_bundle_with_unlisted_or_case_alias_member_fails_preflight(tmp_path: Path) -> None:
    archive, _digest, _payload = _bundle(tmp_path)
    with zipfile.ZipFile(archive, "a") as package:
        package.writestr("payload/UNLISTED.bin", b"private")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    code, report = run(
        [
            "install",
            "--plugin-archive",
            str(archive),
            "--sha256",
            digest,
            "--plugin-dir",
            str(tmp_path / "installed"),
        ]
    )

    assert code == 20
    assert report["verify"]["failure_reason"] == "OBS_BUNDLE_INVALID"


def test_explicit_plugin_directory_link_fails_before_lifecycle_io(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable")

    code, report = run(["status", "--plugin-dir", str(alias)])

    assert code == 10
    assert report["verify"]["failure_reason"] == "OBS_TARGET_UNSAFE"
