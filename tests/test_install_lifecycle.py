from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import jsonschema
import pytest

from dcc_mcp_obs import install_cli
from dcc_mcp_obs.install_cli import RECEIPT_NAME, run

ROOT = Path(__file__).parents[1]
INSTALL_SOP_SCHEMA = json.loads(
    (ROOT / "contracts" / "adapter-install-sop-v1.schema.json").read_text(encoding="utf-8")
)


def _bundle(
    tmp_path: Path,
    *,
    source: str = "payload/dcc-mcp-obs.plugin",
    target: str = "bin/dcc-mcp-obs.plugin",
    payload: bytes = b"native-plugin-binary",
    name: str = "plugin.zip",
) -> tuple[Path, str, bytes]:
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
                "source": source,
                "target": target,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("dcc-mcp-obs-plugin.json", json.dumps(manifest))
        package.writestr(source, payload)
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


def test_status_verifies_owned_files_and_preserves_unmanaged_extras(tmp_path: Path) -> None:
    archive, digest, _payload = _bundle(tmp_path)
    target = tmp_path / "installed"
    install_args = [
        "--plugin-archive",
        str(archive),
        "--sha256",
        digest,
        "--plugin-dir",
        str(target),
    ]
    assert run(["install", *install_args])[0] == 0
    unmanaged = target / "operator-owned.txt"
    unmanaged.write_text("preserve me", encoding="utf-8")

    code, report = run(["status", "--plugin-dir", str(target)])

    assert code == 0
    assert report["status"] == "partial"
    assert unmanaged.read_text(encoding="utf-8") == "preserve me"
    assert target.is_dir()


def test_upgrade_replaces_owned_files_and_preserves_unmanaged_extras(tmp_path: Path) -> None:
    archive, digest, _payload = _bundle(tmp_path)
    target = tmp_path / "installed"
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
    unmanaged = target / "bin" / "operator-notes.txt"
    unmanaged.write_text("preserve me", encoding="utf-8")
    replacement = b"replacement-native-plugin"
    upgrade, upgrade_digest, _payload = _bundle(tmp_path, payload=replacement, name="upgrade.zip")

    code, report = run(
        [
            "upgrade",
            "--plugin-archive",
            str(upgrade),
            "--sha256",
            upgrade_digest,
            "--plugin-dir",
            str(target),
        ]
    )

    assert code == 0
    assert report["status"] == "requires_restart"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == replacement
    assert unmanaged.read_text(encoding="utf-8") == "preserve me"


def test_upgrade_collision_restores_previous_owned_files_and_preserves_unmanaged_file(
    tmp_path: Path,
) -> None:
    archive, digest, original = _bundle(tmp_path)
    target = tmp_path / "installed"
    install_args = [
        "--plugin-archive",
        str(archive),
        "--sha256",
        digest,
        "--plugin-dir",
        str(target),
    ]
    assert run(["install", *install_args])[0] == 0
    unmanaged = target / "bin" / "operator-owned.plugin"
    unmanaged.write_bytes(b"operator-owned")
    upgrade, upgrade_digest, _payload = _bundle(
        tmp_path,
        target="bin/operator-owned.plugin",
        payload=b"replacement-native-plugin",
        name="collision-upgrade.zip",
    )

    code, report = run(
        [
            "upgrade",
            "--plugin-archive",
            str(upgrade),
            "--sha256",
            upgrade_digest,
            "--plugin-dir",
            str(target),
        ]
    )

    assert code == 40
    assert report["verify"]["failure_reason"] == "OBS_PLUGIN_DRIFT"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
    assert unmanaged.read_bytes() == b"operator-owned"
    assert run(["status", "--plugin-dir", str(target)])[0] == 0
    assert not target.with_name(f".{target.name}.backup").exists()


def test_uninstall_removes_only_owned_files_and_preserves_unmanaged_extras(
    tmp_path: Path,
) -> None:
    archive, digest, _payload = _bundle(tmp_path)
    target = tmp_path / "installed"
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
    unmanaged = target / "bin" / "operator-notes.txt"
    unmanaged.write_text("preserve me", encoding="utf-8")

    code, report = run(["uninstall", "--plugin-dir", str(target)])

    assert code == 0
    assert report["status"] == "ok"
    assert unmanaged.read_text(encoding="utf-8") == "preserve me"
    assert not (target / "bin" / "dcc-mcp-obs.plugin").exists()
    assert not (target / RECEIPT_NAME).exists()
    assert (target / "bin").is_dir()


def test_uninstall_failure_restores_owned_files_without_leaving_recovery_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, digest, original = _bundle(tmp_path)
    target = tmp_path / "installed"
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
    unmanaged = target / "bin" / "operator-notes.txt"
    unmanaged.write_text("preserve me", encoding="utf-8")
    original_retire = install_cli._retire_owned_recovery
    injected = False

    def fail_quarantine_delete(path: Path, expected: dict[str, install_cli._FileIdentity]) -> None:
        nonlocal injected
        if Path(path).name == f".{target.name}.remove" and not injected:
            injected = True
            raise OSError("injected quarantine failure")
        original_retire(path, expected)

    monkeypatch.setattr(install_cli, "_retire_owned_recovery", fail_quarantine_delete)

    code, report = run(["uninstall", "--plugin-dir", str(target)])

    assert code == 30
    assert report["verify"]["failure_reason"] == "OBS_UNINSTALL_FAILED"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
    assert unmanaged.read_text(encoding="utf-8") == "preserve me"
    assert run(["status", "--plugin-dir", str(target)])[0] == 0
    assert not target.with_name(f".{target.name}.remove").exists()


@pytest.mark.parametrize("command", ["status", "upgrade", "uninstall"])
def test_owned_path_drift_blocks_lifecycle_without_touching_unmanaged_files(
    tmp_path: Path, command: str
) -> None:
    archive, digest, _payload = _bundle(tmp_path)
    target = tmp_path / "installed"
    install_args = [
        "--plugin-archive",
        str(archive),
        "--sha256",
        digest,
        "--plugin-dir",
        str(target),
    ]
    assert run(["install", *install_args])[0] == 0
    owned = target / "bin" / "dcc-mcp-obs.plugin"
    owned.write_bytes(b"operator replaced owned payload")
    unmanaged = target / "operator-owned.txt"
    unmanaged.write_text("preserve me", encoding="utf-8")
    args = (
        [command, *install_args] if command == "upgrade" else [command, "--plugin-dir", str(target)]
    )

    code, report = run(args)

    assert code == 40
    assert report["verify"]["failure_reason"] == "OBS_PLUGIN_DRIFT"
    assert owned.read_bytes() == b"operator replaced owned payload"
    assert unmanaged.read_text(encoding="utf-8") == "preserve me"


@pytest.mark.parametrize(
    "unsafe",
    [
        "payload/CON.dll",
        "payload/aux.txt",
        "payload/name:stream",
        "payload/trailing.",
        "payload/trailing ",
    ],
)
@pytest.mark.parametrize("projection", ["source", "target"])
def test_bundle_rejects_nonportable_windows_aliases(
    tmp_path: Path, unsafe: str, projection: str
) -> None:
    kwargs = {projection: unsafe}
    archive, digest, _payload = _bundle(tmp_path, **kwargs)

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


@pytest.mark.parametrize("projection", ["source", "target"])
@pytest.mark.parametrize(
    "unsafe",
    [
        f"payload/{'a' * 256}",
        "/".join(["payload", *(["a" * 70] * 15)]),
    ],
)
def test_bundle_rejects_overlong_portable_paths_before_install_io(
    tmp_path: Path, unsafe: str, projection: str
) -> None:
    if projection == "target" and unsafe.startswith("payload/"):
        unsafe = unsafe.removeprefix("payload/")
    archive, digest, _payload = _bundle(tmp_path, **{projection: unsafe})

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
    assert not (tmp_path / "installed").exists()


def test_upgrade_rechecks_exact_owned_identities_immediately_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, digest, original = _bundle(tmp_path)
    target = tmp_path / "installed"
    install_args = [
        "--plugin-archive",
        str(archive),
        "--sha256",
        digest,
        "--plugin-dir",
        str(target),
    ]
    assert run(["install", *install_args])[0] == 0
    owned = target / "bin" / "dcc-mcp-obs.plugin"
    original_identity = owned.stat().st_ino
    original_verify = install_cli._verify
    swapped = False

    def swap_after_verify(path: Path) -> dict[str, object]:
        nonlocal swapped
        receipt = original_verify(path)
        if not swapped:
            replacement = owned.with_suffix(".replacement")
            replacement.write_bytes(original)
            replacement.replace(owned)
            swapped = True
            assert owned.stat().st_ino != original_identity
        return receipt

    monkeypatch.setattr(install_cli, "_verify", swap_after_verify)
    upgrade, upgrade_digest, _payload = _bundle(
        tmp_path, payload=b"new-native-plugin", name="upgrade.zip"
    )

    code, report = run(
        [
            "upgrade",
            "--plugin-archive",
            str(upgrade),
            "--sha256",
            upgrade_digest,
            "--plugin-dir",
            str(target),
        ]
    )

    assert code == 40
    assert report["verify"]["failure_reason"] == "OBS_PLUGIN_DRIFT"
    assert owned.read_bytes() == original
    assert not target.with_name(f".{target.name}.backup").exists()


def test_upgrade_backup_cleanup_failure_rolls_back_without_recovery_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, digest, original = _bundle(tmp_path)
    target = tmp_path / "installed"
    install_args = [
        "--plugin-archive",
        str(archive),
        "--sha256",
        digest,
        "--plugin-dir",
        str(target),
    ]
    assert run(["install", *install_args])[0] == 0
    backup = target.with_name(f".{target.name}.backup")
    original_retire = install_cli._retire_owned_recovery
    injected = False

    def fail_backup_cleanup(path: Path, expected: dict[str, install_cli._FileIdentity]) -> None:
        nonlocal injected
        if Path(path) == backup and not injected:
            injected = True
            raise OSError("injected backup cleanup failure")
        original_retire(path, expected)

    monkeypatch.setattr(install_cli, "_retire_owned_recovery", fail_backup_cleanup)
    upgrade, upgrade_digest, _payload = _bundle(
        tmp_path, payload=b"new-native-plugin", name="upgrade.zip"
    )

    code, report = run(
        [
            "upgrade",
            "--plugin-archive",
            str(upgrade),
            "--sha256",
            upgrade_digest,
            "--plugin-dir",
            str(target),
        ]
    )

    assert code == 30
    assert report["verify"]["failure_reason"] == "OBS_INSTALL_FAILED"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
    assert (target / RECEIPT_NAME).is_file()
    assert not backup.exists()


@pytest.mark.parametrize("command", ["status", "upgrade", "uninstall"])
@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("schema_version", 2),
        ("platform", "foreign-platform"),
        ("product", "foreign-product"),
        ("version", "999.0.0"),
        ("unexpected", "not-owned"),
    ],
)
def test_lifecycle_rejects_noncanonical_receipt_envelope(
    tmp_path: Path, command: str, mutation: str, value: object
) -> None:
    archive, digest, original = _bundle(tmp_path)
    target = tmp_path / "installed"
    install_args = [
        "--plugin-archive",
        str(archive),
        "--sha256",
        digest,
        "--plugin-dir",
        str(target),
    ]
    assert run(["install", *install_args])[0] == 0
    receipt_path = target / RECEIPT_NAME
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[mutation] = value
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    args = (
        [command, *install_args] if command == "upgrade" else [command, "--plugin-dir", str(target)]
    )

    code, report = run(args)

    assert code == 40
    assert report["verify"]["failure_reason"] == "OBS_RECEIPT_INVALID"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original


@pytest.mark.parametrize("command", ["status", "upgrade", "uninstall"])
def test_lifecycle_rejects_noncanonical_receipt_ownership_entry(
    tmp_path: Path, command: str
) -> None:
    archive, digest, original = _bundle(tmp_path)
    target = tmp_path / "installed"
    install_args = [
        "--plugin-archive",
        str(archive),
        "--sha256",
        digest,
        "--plugin-dir",
        str(target),
    ]
    assert run(["install", *install_args])[0] == 0
    receipt_path = target / RECEIPT_NAME
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["files"][0]["unexpected"] = True
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    args = (
        [command, *install_args] if command == "upgrade" else [command, "--plugin-dir", str(target)]
    )

    code, report = run(args)

    assert code == 40
    assert report["verify"]["failure_reason"] == "OBS_RECEIPT_INVALID"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
