from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from dcc_mcp_obs import install_cli
from dcc_mcp_obs.install_cli import RECEIPT_NAME, run


def _bundle(tmp_path: Path, *, payload: bytes, name: str) -> tuple[Path, str]:
    archive = tmp_path / name
    source = "payload/dcc-mcp-obs.plugin"
    manifest = {
        "schema_version": 1,
        "product": "dcc-mcp-obs",
        "version": install_cli.__version__,
        "platform": install_cli._platform_name(),
        "files": [
            {
                "source": source,
                "target": "bin/dcc-mcp-obs.plugin",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("dcc-mcp-obs-plugin.json", json.dumps(manifest))
        package.writestr(source, payload)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def _args(archive: Path, digest: str, target: Path) -> list[str]:
    return [
        "--plugin-archive",
        str(archive),
        "--sha256",
        digest,
        "--plugin-dir",
        str(target),
    ]


def _replace_same_object_bytes(path: Path) -> None:
    replacement = path.with_name(f".{path.name}.contender")
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)


def _replace_parent_with_same_content(parent: Path) -> None:
    original = parent.with_name(f".{parent.name}.prior")
    parent.replace(original)
    parent.mkdir()
    for child in original.iterdir():
        replacement = parent / child.name
        replacement.write_bytes(child.read_bytes())
        child.unlink()
    original.rmdir()


@pytest.mark.parametrize("command", ["upgrade", "uninstall"])
@pytest.mark.parametrize("swap", ["payload", "receipt", "parent", "parent-before-receipt"])
def test_lifecycle_cas_rejects_new_owned_objects_at_each_move_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    swap: str,
) -> None:
    original = b"transaction-original"
    archive, digest = _bundle(tmp_path, payload=original, name="install.zip")
    target = tmp_path / "installed"
    assert run(["install", *_args(archive, digest, target)])[0] == 0
    recovery = target.with_name(
        f".{target.name}.backup" if command == "upgrade" else f".{target.name}.remove"
    )
    original_prepare = install_cli._prepare_owned_destination
    swapped = False

    def swap_immediately_before_move(destination_root: Path, relative: str) -> Path:
        nonlocal swapped
        if destination_root == recovery and not swapped:
            if swap == "payload" and relative.endswith("dcc-mcp-obs.plugin"):
                _replace_same_object_bytes(target / "bin" / "dcc-mcp-obs.plugin")
                swapped = True
            elif swap == "receipt" and relative == RECEIPT_NAME:
                _replace_same_object_bytes(target / RECEIPT_NAME)
                swapped = True
            elif (swap == "parent" and relative.endswith("dcc-mcp-obs.plugin")) or (
                swap == "parent-before-receipt" and relative == RECEIPT_NAME
            ):
                _replace_parent_with_same_content(target / "bin")
                swapped = True
        return original_prepare(destination_root, relative)

    monkeypatch.setattr(install_cli, "_prepare_owned_destination", swap_immediately_before_move)
    upgrade, upgrade_digest = _bundle(tmp_path, payload=b"transaction-upgrade", name="upgrade.zip")
    argv = (
        ["upgrade", *_args(upgrade, upgrade_digest, target)]
        if command == "upgrade"
        else ["uninstall", "--plugin-dir", str(target)]
    )

    code, report = run(argv)

    assert swapped
    assert code == install_cli.EXIT_VERIFY
    assert report["verify"]["failure_reason"] == "OBS_PLUGIN_DRIFT"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
    assert (target / RECEIPT_NAME).is_file()
    assert not recovery.exists()


def test_partial_retirement_failure_restores_complete_previous_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"transaction-original"
    archive, digest = _bundle(tmp_path, payload=original, name="install.zip")
    target = tmp_path / "installed"
    assert run(["install", *_args(archive, digest, target)])[0] == 0
    previous_receipt = (target / RECEIPT_NAME).read_bytes()
    backup = target.with_name(f".{target.name}.backup")
    original_retire = install_cli._retire_owned_recovery
    injected = False

    def partially_delete_then_fail(
        path: Path, expected: dict[str, install_cli._FileIdentity]
    ) -> None:
        nonlocal injected
        if Path(path) == backup and not injected:
            injected = True
            (backup / "bin" / "dcc-mcp-obs.plugin").unlink()
            raise OSError("injected partial retirement failure")
        original_retire(path, expected)

    monkeypatch.setattr(install_cli, "_retire_owned_recovery", partially_delete_then_fail)
    upgrade, upgrade_digest = _bundle(tmp_path, payload=b"transaction-upgrade", name="upgrade.zip")

    code, report = run(["upgrade", *_args(upgrade, upgrade_digest, target)])

    assert code == install_cli.EXIT_INSTALL
    assert report["verify"]["failure_reason"] == "OBS_INSTALL_FAILED"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
    assert (target / RECEIPT_NAME).read_bytes() == previous_receipt
    assert run(["status", "--plugin-dir", str(target)])[0] == 0
    assert not backup.exists()


def test_corrupt_published_payload_is_verified_before_backup_retirement_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"transaction-original"
    archive, digest = _bundle(tmp_path, payload=original, name="install.zip")
    target = tmp_path / "installed"
    assert run(["install", *_args(archive, digest, target)])[0] == 0
    previous_receipt = (target / RECEIPT_NAME).read_bytes()
    unrelated = target / "operator-owned.txt"
    unrelated.write_bytes(b"preserve-me")
    original_prune = install_cli._prune_empty_owned_directories
    corrupted = False

    def corrupt_after_publication(prune_target: Path, relatives: list[str]) -> None:
        nonlocal corrupted
        original_prune(prune_target, relatives)
        if prune_target == target and not corrupted:
            (target / "bin" / "dcc-mcp-obs.plugin").write_bytes(b"corrupt-published")
            corrupted = True

    monkeypatch.setattr(install_cli, "_prune_empty_owned_directories", corrupt_after_publication)
    upgrade, upgrade_digest = _bundle(tmp_path, payload=b"transaction-upgrade", name="upgrade.zip")

    code, report = run(["upgrade", *_args(upgrade, upgrade_digest, target)])

    assert corrupted
    assert code != 0
    assert report["verify"]["failure_reason"] == "OBS_PLUGIN_DRIFT"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
    assert (target / RECEIPT_NAME).read_bytes() == previous_receipt
    assert unrelated.read_bytes() == b"preserve-me"
    assert run(["status", "--plugin-dir", str(target)])[0] == 0
    assert not target.with_name(f".{target.name}.backup").exists()


@pytest.mark.parametrize("swap", ["payload", "receipt"])
@pytest.mark.parametrize("window", ["before-retire", "post-verify"])
def test_upgrade_keeps_recovery_when_verified_publication_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap: str,
    window: str,
) -> None:
    original = b"transaction-original"
    archive, digest = _bundle(tmp_path, payload=original, name="install.zip")
    target = tmp_path / "installed"
    assert run(["install", *_args(archive, digest, target)])[0] == 0
    previous_receipt = (target / RECEIPT_NAME).read_bytes()
    backup = target.with_name(f".{target.name}.backup")
    published_path = (
        target / "bin" / "dcc-mcp-obs.plugin" if swap == "payload" else target / RECEIPT_NAME
    )
    replacement_identity = 0
    replaced = False

    def replace_publication() -> None:
        nonlocal replacement_identity, replaced
        _replace_same_object_bytes(published_path)
        replacement_identity = published_path.stat().st_ino
        replaced = True

    if window == "before-retire":
        original_retire = install_cli._retire_owned_recovery

        def replace_before_retirement(
            path: Path, expected: dict[str, install_cli._FileIdentity]
        ) -> None:
            if Path(path) == backup and not replaced:
                replace_publication()
            original_retire(path, expected)

        monkeypatch.setattr(install_cli, "_retire_owned_recovery", replace_before_retirement)
    else:
        original_verify = install_cli._verify
        verification_count = 0

        def replace_after_published_verification(path: Path) -> install_cli._VerifiedReceipt:
            nonlocal verification_count
            receipt = original_verify(path)
            verification_count += 1
            if verification_count == 3:
                replace_publication()
            return receipt

        monkeypatch.setattr(install_cli, "_verify", replace_after_published_verification)

    upgrade, upgrade_digest = _bundle(tmp_path, payload=b"transaction-upgrade", name="upgrade.zip")

    code, report = run(["upgrade", *_args(upgrade, upgrade_digest, target)])

    assert replaced
    assert code == install_cli.EXIT_INSTALL
    assert report["verify"]["failure_reason"] == "OBS_RECOVERY_REQUIRED"
    assert published_path.stat().st_ino == replacement_identity
    assert backup.is_dir()
    assert (backup / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
    assert (backup / RECEIPT_NAME).read_bytes() == previous_receipt


def test_upgrade_rolls_back_in_place_drift_after_published_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"transaction-original"
    archive, digest = _bundle(tmp_path, payload=original, name="install.zip")
    target = tmp_path / "installed"
    assert run(["install", *_args(archive, digest, target)])[0] == 0
    published_path = target / "bin" / "dcc-mcp-obs.plugin"
    backup = target.with_name(f".{target.name}.backup")
    original_verify = install_cli._verify
    verification_count = 0
    corrupted = False

    def corrupt_after_published_verification(path: Path) -> install_cli._VerifiedReceipt:
        nonlocal corrupted, verification_count
        receipt = original_verify(path)
        verification_count += 1
        if verification_count == 3:
            published_path.write_bytes(b"corrupt-after-verify")
            corrupted = True
        return receipt

    monkeypatch.setattr(install_cli, "_verify", corrupt_after_published_verification)
    upgrade, upgrade_digest = _bundle(tmp_path, payload=b"transaction-upgrade", name="upgrade.zip")

    code, report = run(["upgrade", *_args(upgrade, upgrade_digest, target)])

    assert corrupted
    assert code == install_cli.EXIT_VERIFY
    assert report["verify"]["failure_reason"] == "OBS_PLUGIN_DRIFT"
    assert published_path.read_bytes() == original
    assert run(["status", "--plugin-dir", str(target)])[0] == 0
    assert not backup.exists()


@pytest.mark.parametrize("replacement_payload", [b"archive-original", b"archive-changed"])
def test_archive_replacement_after_validation_fails_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_payload: bytes,
) -> None:
    archive, digest = _bundle(tmp_path, payload=b"archive-original", name="install.zip")
    target = tmp_path / "installed"
    original_validate = install_cli._validate_bundle

    def replace_after_validation(path: Path, expected: str) -> dict[str, object]:
        plan = original_validate(path, expected)
        replacement, _replacement_digest = _bundle(
            tmp_path, payload=replacement_payload, name="replacement.zip"
        )
        replacement.replace(path)
        return plan

    monkeypatch.setattr(install_cli, "_validate_bundle", replace_after_validation)

    code, report = run(["install", *_args(archive, digest, target)])

    assert code == install_cli.EXIT_ACQUIRE
    assert report["verify"]["failure_reason"] == "OBS_BUNDLE_DRIFT"
    assert not target.exists()


@pytest.mark.parametrize("command", ["upgrade", "uninstall"])
def test_directory_link_count_drift_does_not_change_physical_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    original = b"transaction-original"
    archive, digest = _bundle(tmp_path, payload=original, name="install.zip")
    target = tmp_path / "installed"
    assert run(["install", *_args(archive, digest, target)])[0] == 0
    original_snapshot = install_cli._snapshot_owned_files
    original_identity = install_cli._file_identity
    drifted = False

    def capture_then_change_directory_metadata(
        *args: object, **kwargs: object
    ) -> dict[str, tuple[bytes, int]]:
        nonlocal drifted
        snapshot = original_snapshot(*args, **kwargs)
        drifted = True
        return snapshot

    def macos_like_directory_identity(path: Path) -> install_cli._FileIdentity:
        identity = original_identity(path)
        if drifted and stat.S_ISDIR(identity.mode):
            return replace(identity, links=identity.links + 1)
        return identity

    monkeypatch.setattr(
        install_cli, "_snapshot_owned_files", capture_then_change_directory_metadata
    )
    monkeypatch.setattr(install_cli, "_file_identity", macos_like_directory_identity)
    upgrade, upgrade_digest = _bundle(tmp_path, payload=b"transaction-upgrade", name="upgrade.zip")
    argv = (
        ["upgrade", *_args(upgrade, upgrade_digest, target)]
        if command == "upgrade"
        else ["uninstall", "--plugin-dir", str(target)]
    )

    code, report = run(argv)

    assert drifted
    assert code == 0, report
    if command == "upgrade":
        assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == b"transaction-upgrade"
        assert run(["status", "--plugin-dir", str(target)])[0] == 0
    else:
        assert not target.exists()


@pytest.mark.parametrize("swap", ["payload", "receipt", "parent"])
def test_verify_rejects_same_content_replacement_before_identity_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap: str,
) -> None:
    archive, digest = _bundle(tmp_path, payload=b"owned-payload", name="install.zip")
    target = tmp_path / "installed"
    assert run(["install", *_args(archive, digest, target)])[0] == 0
    original_capture = install_cli._capture_owned_install_identity
    replaced = False

    def replace_after_validation(
        capture_target: Path, relatives: list[str]
    ) -> tuple[tuple[str, install_cli._FileIdentity], ...]:
        nonlocal replaced
        if not replaced:
            if swap == "payload":
                _replace_same_object_bytes(target / "bin" / "dcc-mcp-obs.plugin")
            elif swap == "receipt":
                _replace_same_object_bytes(target / RECEIPT_NAME)
            else:
                _replace_parent_with_same_content(target / "bin")
            replaced = True
        return original_capture(capture_target, relatives)

    monkeypatch.setattr(install_cli, "_capture_owned_install_identity", replace_after_validation)

    code, report = run(["status", "--plugin-dir", str(target)])

    assert replaced
    assert code == install_cli.EXIT_VERIFY
    assert report["verify"]["failure_reason"] == "OBS_PLUGIN_DRIFT"


@pytest.mark.parametrize("command", ["upgrade", "uninstall"])
@pytest.mark.parametrize("swap", ["payload", "parent"])
def test_recovery_retirement_preserves_independently_replaced_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    swap: str,
) -> None:
    original = b"transaction-original"
    archive, digest = _bundle(tmp_path, payload=original, name="install.zip")
    target = tmp_path / "installed"
    assert run(["install", *_args(archive, digest, target)])[0] == 0
    recovery = target.with_name(
        f".{target.name}.backup" if command == "upgrade" else f".{target.name}.remove"
    )
    original_prune = install_cli._prune_empty_owned_directories
    replaced = False

    def replace_before_retirement(prune_target: Path, relatives: list[str]) -> None:
        nonlocal replaced
        original_prune(prune_target, relatives)
        if prune_target == target and not replaced:
            if swap == "payload":
                _replace_same_object_bytes(recovery / "bin" / "dcc-mcp-obs.plugin")
            else:
                _replace_parent_with_same_content(recovery / "bin")
            replaced = True

    monkeypatch.setattr(install_cli, "_prune_empty_owned_directories", replace_before_retirement)
    upgrade, upgrade_digest = _bundle(tmp_path, payload=b"transaction-upgrade", name="upgrade.zip")
    argv = (
        ["upgrade", *_args(upgrade, upgrade_digest, target)]
        if command == "upgrade"
        else ["uninstall", "--plugin-dir", str(target)]
    )

    code, report = run(argv)

    assert replaced
    assert code == install_cli.EXIT_INSTALL
    assert report["verify"]["failure_reason"] == "OBS_RECOVERY_REQUIRED"
    assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
    assert run(["status", "--plugin-dir", str(target)])[0] == 0
    assert (recovery / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original
