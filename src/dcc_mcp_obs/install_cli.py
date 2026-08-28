"""Install SOP v1 lifecycle for signed native OBS plugin bundles."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import unicodedata
import zipfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .__version__ import __version__

EXIT_OK = 0
EXIT_PREFLIGHT = 10
EXIT_ACQUIRE = 20
EXIT_INSTALL = 30
EXIT_VERIFY = 40
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 128
MAX_COMPRESSION_RATIO = 100
MAX_PORTABLE_COMPONENT_BYTES = 255
MAX_PORTABLE_PATH_BYTES = 1024
RECEIPT_NAME = ".dcc-mcp-obs-install.json"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
RECEIPT_KEYS = frozenset({"schema_version", "product", "version", "platform", "files"})
RECEIPT_FILE_KEYS = frozenset({"path", "sha256", "size"})


class InstallError(RuntimeError):
    def __init__(self, code: str, stage: str, exit_code: int) -> None:
        self.code = code
        self.stage = stage
        self.exit_code = exit_code
        super().__init__(code)


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    attributes: int


class _VerifiedReceipt(dict[str, Any]):
    def __init__(
        self,
        receipt: dict[str, Any],
        *,
        ownership_identity: tuple[tuple[str, _FileIdentity], ...],
    ) -> None:
        super().__init__(receipt)
        self.ownership_identity = ownership_identity


class _VerifiedBundle(dict[str, Any]):
    def __init__(
        self,
        manifest: dict[str, Any],
        *,
        archive_path: Path,
        archive_identity: _FileIdentity,
        archive_bytes: bytes,
    ) -> None:
        super().__init__(manifest)
        self.archive_path = archive_path
        self.archive_identity = archive_identity
        self.archive_bytes = archive_bytes


class _RecoveryIdentity(dict[str, _FileIdentity]):
    def __init__(
        self,
        identities: dict[str, _FileIdentity],
        *,
        published_target: Path,
        published_relatives: Sequence[str],
    ) -> None:
        super().__init__(identities)
        self.published_target = published_target
        self.published_relatives = tuple(published_relatives)
        self.published_identity: dict[str, _FileIdentity] = {}
        self.publication_guard_active = False

    def activate_publication_guard(self, published_identity: dict[str, _FileIdentity]) -> None:
        self.published_identity = dict(published_identity)
        self.publication_guard_active = True

    def release_publication_guard(self) -> None:
        self.publication_guard_active = False


def default_plugin_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return root / "obs-studio" / "plugins" / "dcc-mcp-obs"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "obs-studio"
            / "plugins"
            / "dcc-mcp-obs.plugin"
        )
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "obs-studio"
        / "plugins"
        / "dcc-mcp-obs"
    )


def run(argv: Sequence[str]) -> tuple[int, dict[str, Any]]:
    parser = argparse.ArgumentParser(description="Install or verify the native DCC-MCP OBS plugin.")
    parser.add_argument("command", choices=("install", "upgrade", "status", "verify", "uninstall"))
    parser.add_argument("--plugin-archive", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--plugin-dir", type=Path, default=default_plugin_dir())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv))
    target = Path(os.path.abspath(args.plugin_dir.expanduser()))
    steps: list[dict[str, str]] = []
    try:
        _require_safe_target_path(target)
        if args.command in {"install", "upgrade"}:
            if args.plugin_archive is None or args.sha256 is None:
                raise InstallError("OBS_BUNDLE_REQUIRED", "preflight", EXIT_PREFLIGHT)
            plan = _validate_bundle(args.plugin_archive, args.sha256)
            if args.dry_run:
                steps.append({"id": "plugin", "status": "planned"})
                return EXIT_OK, _report("planned", steps, target, directly_usable=False)
            _install(plan, target, allow_existing=args.command == "upgrade")
            steps.append({"id": "plugin", "status": "ok"})
            _verify(target)
            steps.append({"id": "verify", "status": "ok"})
            return EXIT_OK, _report(
                "requires_restart",
                steps,
                target,
                directly_usable=False,
                failure_stage="host-readiness",
                failure_reason="LIVE_OBS_VERIFICATION_REQUIRED",
            )
        if args.command in {"status", "verify"}:
            if args.dry_run:
                steps.append({"id": "verify", "status": "planned"})
                return EXIT_OK, _report("planned", steps, target, directly_usable=False)
            _verify(target)
            steps.append({"id": "verify", "status": "ok"})
            return EXIT_OK, _report(
                "partial",
                steps,
                target,
                directly_usable=False,
                failure_stage="host-readiness",
                failure_reason="LIVE_OBS_VERIFICATION_REQUIRED",
            )
        if args.dry_run:
            steps.append({"id": "uninstall", "status": "planned"})
            return EXIT_OK, _report("planned", steps, target, directly_usable=False)
        _uninstall(target)
        steps.append({"id": "uninstall", "status": "ok"})
        return EXIT_OK, _report("ok", steps, None, directly_usable=False)
    except InstallError as exc:
        steps.append({"id": exc.stage, "status": "failed", "message": exc.code})
        return exc.exit_code, _report(
            "failed",
            steps,
            target if target.exists() else None,
            directly_usable=False,
            failure_stage=exc.stage,
            failure_reason=exc.code,
        )


def _report(
    status: str,
    steps: list[dict[str, str]],
    target: Path | None,
    *,
    directly_usable: bool,
    failure_stage: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    try:
        core_version = importlib.metadata.version("dcc-mcp-core")
    except importlib.metadata.PackageNotFoundError:
        core_version = "unknown"
    return {
        "schema_version": 1,
        "status": status,
        "dcc_type": "obs",
        "adapter_version": __version__,
        "core_version": core_version,
        "steps": steps,
        "next_steps": [],
        "receipt_path": str(target / RECEIPT_NAME) if target is not None else None,
        "verify": {
            "directly_usable": directly_usable,
            "failure_stage": failure_stage,
            "failure_reason": failure_reason,
        },
    }


def _acquire_archive(archive: Path) -> tuple[Path, _FileIdentity, bytes]:
    archive = Path(os.path.abspath(archive.expanduser()))
    try:
        path_identity = _file_identity(archive)
    except OSError as exc:
        raise InstallError("OBS_BUNDLE_UNAVAILABLE", "acquire", EXIT_ACQUIRE) from exc
    try:
        attributes = path_identity.attributes
        if (
            not stat.S_ISREG(path_identity.mode)
            or path_identity.links != 1
            or path_identity.size > MAX_ARCHIVE_BYTES
            or attributes & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise OSError("unsafe archive")
        with archive.open("rb") as stream:
            opened_identity = _identity_from_stat(os.fstat(stream.fileno()))
            if opened_identity != path_identity:
                raise OSError("archive identity changed before read")
            payload = stream.read(MAX_ARCHIVE_BYTES + 1)
            if len(payload) != path_identity.size or len(payload) > MAX_ARCHIVE_BYTES:
                raise OSError("archive size changed while reading")
            if _identity_from_stat(os.fstat(stream.fileno())) != opened_identity:
                raise OSError("archive identity changed while reading")
        if _file_identity(archive) != opened_identity:
            raise OSError("archive path changed while reading")
    except OSError as exc:
        raise InstallError("OBS_BUNDLE_UNSAFE", "acquire", EXIT_ACQUIRE) from exc
    return archive, opened_identity, payload


def _bundle_bytes(plan: dict[str, Any]) -> bytes:
    if not isinstance(plan, _VerifiedBundle):
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
    return plan.archive_bytes


def _require_bundle_current(plan: dict[str, Any]) -> None:
    if not isinstance(plan, _VerifiedBundle):
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
    try:
        identity = _file_identity(plan.archive_path)
    except OSError as exc:
        raise InstallError("OBS_BUNDLE_DRIFT", "acquire", EXIT_ACQUIRE) from exc
    if identity != plan.archive_identity:
        raise InstallError("OBS_BUNDLE_DRIFT", "acquire", EXIT_ACQUIRE)


def _validate_bundle(archive: Path, expected_digest: str) -> dict[str, Any]:
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise InstallError("OBS_BUNDLE_DIGEST_INVALID", "acquire", EXIT_ACQUIRE)
    try:
        int(expected_digest, 16)
    except ValueError as exc:
        raise InstallError("OBS_BUNDLE_DIGEST_INVALID", "acquire", EXIT_ACQUIRE) from exc
    archive, archive_identity, archive_bytes = _acquire_archive(archive)
    if hashlib.sha256(archive_bytes).hexdigest() != expected_digest.casefold():
        raise InstallError("OBS_BUNDLE_DIGEST_MISMATCH", "acquire", EXIT_ACQUIRE)
    try:
        raw_manifest = _read_archive_member(archive_bytes, "dcc-mcp-obs-plugin.json")
        manifest = json.loads(raw_manifest)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("product") != "dcc-mcp-obs"
        or manifest.get("version") != __version__
        or manifest.get("platform") != _platform_name()
        or not isinstance(manifest.get("files"), list)
        or not 1 <= len(manifest["files"]) <= MAX_MEMBERS
    ):
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    payload_checks: list[tuple[str, str]] = []
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
        source = _safe_relative(entry.get("source"))
        target = _safe_relative(entry.get("target"))
        digest = entry.get("sha256")
        source_key = source.casefold()
        target_key = target.casefold()
        if (
            not source.startswith("payload/")
            or source_key in seen_sources
            or target_key in seen_targets
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
        try:
            int(digest, 16)
        except ValueError as exc:
            raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE) from exc
        seen_sources.add(source_key)
        seen_targets.add(target_key)
        payload_checks.append((source, digest.casefold()))
    if any(
        left != right and right.startswith(f"{left}/")
        for left in seen_targets
        for right in seen_targets
    ):
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
    _validate_archive_inventory(archive_bytes, seen_sources)
    for source, digest in payload_checks:
        payload = _read_archive_member(archive_bytes, source)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise InstallError("OBS_BUNDLE_MEMBER_MISMATCH", "acquire", EXIT_ACQUIRE)
    return _VerifiedBundle(
        manifest,
        archive_path=archive,
        archive_identity=archive_identity,
        archive_bytes=archive_bytes,
    )


def _install(plan: dict[str, Any], target: Path, *, allow_existing: bool) -> None:
    _require_bundle_current(plan)
    previous_receipt: dict[str, Any] | None = None
    if target.exists():
        if not allow_existing:
            raise InstallError("OBS_PLUGIN_ALREADY_INSTALLED", "preflight", EXIT_PREFLIGHT)
        previous_receipt = _verify(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _require_safe_target_path(target)
    stage = Path(tempfile.mkdtemp(prefix=".dcc-mcp-obs-stage-", dir=target.parent))
    backup = target.with_name(f".{target.name}.backup")
    try:
        receipt_files: list[dict[str, object]] = []
        for entry in plan["files"]:
            relative = _safe_relative(entry["target"])
            destination = stage / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = _read_archive_member(_bundle_bytes(plan), _safe_relative(entry["source"]))
            with destination.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            receipt_files.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }
            )
        receipt = {
            "schema_version": 1,
            "product": "dcc-mcp-obs",
            "version": __version__,
            "platform": _platform_name(),
            "files": receipt_files,
        }
        _write_json_exclusive(stage / RECEIPT_NAME, receipt)
        _require_bundle_current(plan)
        if previous_receipt is None:
            stage.replace(target)
        else:
            _replace_owned_install(target, stage, backup, previous_receipt, receipt)
    except InstallError:
        raise
    except BaseException as exc:
        raise InstallError("OBS_INSTALL_FAILED", "install", EXIT_INSTALL) from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _replace_owned_install(
    target: Path,
    stage: Path,
    backup: Path,
    previous_receipt: dict[str, Any],
    next_receipt: dict[str, Any],
) -> None:
    recaptured_receipt = _verify(target)
    if (
        not isinstance(previous_receipt, _VerifiedReceipt)
        or dict(previous_receipt) != dict(recaptured_receipt)
        or previous_receipt.ownership_identity != recaptured_receipt.ownership_identity
    ):
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
    previous_receipt = recaptured_receipt
    if os.path.lexists(backup):
        raise InstallError("OBS_RECOVERY_REQUIRED", "preflight", EXIT_PREFLIGHT)
    backup.mkdir()
    previous = _receipt_relatives(previous_receipt)
    upcoming = _receipt_relatives(next_receipt)
    expected = dict(previous_receipt.ownership_identity)
    snapshot = _snapshot_owned_files(target, [*previous, RECEIPT_NAME], expected)
    moved_previous: list[str] = []
    installed: list[str] = []
    installed_ownership: dict[str, _FileIdentity] = {".": expected["."]}
    recovery_expected = _RecoveryIdentity(
        {".": _file_identity(backup)},
        published_target=target,
        published_relatives=upcoming,
    )
    try:
        for relative in [*previous, RECEIPT_NAME]:
            source = _owned_path(target, relative)
            destination = _prepare_recovery_destination(backup, relative, recovery_expected)
            _require_owned_source_identity(target, relative, expected)
            source.replace(destination)
            if _file_identity(destination) != expected[relative]:
                raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
            recovery_expected[relative] = expected[relative]
            moved_previous.append(relative)
        for relative in [*upcoming, RECEIPT_NAME]:
            source = _owned_path(stage, relative)
            destination = _prepare_owned_destination(target, relative)
            _require_expected_directory_objects(target, expected)
            if os.path.lexists(destination):
                raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
            source.replace(destination)
            _record_owned_destination(target, relative, installed_ownership)
            installed.append(relative)
        _prune_empty_owned_directories(target, previous)
        published_receipt = _verify(target)
        if dict(published_receipt) != next_receipt or not _identity_maps_match(
            installed_ownership,
            dict(published_receipt.ownership_identity),
        ):
            raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
        recovery_expected.activate_publication_guard(dict(published_receipt.ownership_identity))
        _require_published_identity(recovery_expected)
        _retire_owned_recovery(backup, recovery_expected)
        _require_published_identity(recovery_expected)
    except BaseException as exc:
        rollback_failed = False
        transaction_started = bool(moved_previous or installed)
        restored_ownership: dict[str, _FileIdentity] = {".": expected["."]}
        if recovery_expected.publication_guard_active and not _published_objects_owned(
            recovery_expected
        ):
            rollback_failed = True
        if not rollback_failed:
            for relative in reversed(installed):
                try:
                    path = _owned_path(target, relative)
                    observed = _file_identity(path)
                    published = installed_ownership[relative]
                    if observed != published and not _same_file_object(observed, published):
                        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
                    path.unlink()
                except (InstallError, OSError):
                    rollback_failed = True
            _prune_empty_owned_directories(target, upcoming)
        if not rollback_failed:
            for relative in reversed(moved_previous):
                try:
                    source = _owned_path(backup, relative)
                    destination = _prepare_owned_destination(target, relative)
                    if os.path.lexists(destination):
                        rollback_failed = True
                        continue
                    if relative not in recovery_expected or not os.path.lexists(source):
                        _restore_owned_snapshot(destination, snapshot[relative])
                        _record_owned_destination(target, relative, restored_ownership)
                        recovery_expected.pop(relative, None)
                    elif _recovery_source_matches(backup, relative, recovery_expected):
                        source.replace(destination)
                        _record_owned_destination(target, relative, restored_ownership)
                        recovery_expected.pop(relative)
                    else:
                        _restore_owned_snapshot(destination, snapshot[relative])
                        rollback_failed = True
                except (InstallError, OSError):
                    rollback_failed = True
        if not rollback_failed and transaction_started:
            try:
                restored_receipt = _verify(target)
                if dict(restored_receipt) != dict(
                    previous_receipt
                ) or not _identity_maps_same_objects(
                    restored_ownership,
                    dict(restored_receipt.ownership_identity),
                    subset=True,
                ):
                    raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
                recovery_expected.release_publication_guard()
            except (InstallError, OSError):
                rollback_failed = True
        elif not transaction_started:
            recovery_expected.release_publication_guard()
        try:
            _retire_owned_recovery(backup, recovery_expected)
        except (InstallError, OSError):
            rollback_failed = True
        if rollback_failed:
            raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL) from exc
        raise


def _verify(target: Path) -> _VerifiedReceipt:
    _require_safe_target_path(target)
    if not target.is_dir():
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    initial_identity = dict(_capture_owned_install_identity_raw(target, []))
    receipt = _parse_receipt(
        _guarded_read_owned_file(
            target,
            RECEIPT_NAME,
            initial_identity,
            maximum_bytes=65_536,
            receipt=True,
        )
    )
    if (
        set(receipt) != RECEIPT_KEYS
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("product") != "dcc-mcp-obs"
        or receipt.get("version") != __version__
        or receipt.get("platform") != _platform_name()
    ):
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    files = receipt.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_MEMBERS:
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    expected_files: set[str] = set()
    relatives: list[str] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != RECEIPT_FILE_KEYS:
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
        try:
            relative = _portable_relative(entry.get("path"))
        except ValueError as exc:
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY) from exc
        if relative.casefold() == RECEIPT_NAME.casefold():
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
        digest = entry.get("sha256")
        size = entry.get("size")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
        try:
            int(digest, 16)
        except ValueError as exc:
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY) from exc
        relative_key = relative.casefold()
        if relative_key in expected_files:
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
        expected_files.add(relative_key)
        relatives.append(relative)
    if any(
        left != right and right.startswith(f"{left}/")
        for left in expected_files
        for right in expected_files
    ):
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    captured = _capture_owned_install_identity_raw(target, relatives)
    expected = dict(captured)
    if not _identity_maps_match(expected, initial_identity, subset=True):
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
    if not _identity_maps_match(
        expected,
        dict(_capture_owned_install_identity(target, relatives)),
    ):
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
    for entry, relative in zip(files, relatives, strict=True):
        payload = _guarded_read_owned_file(
            target,
            relative,
            expected,
            maximum_bytes=MAX_ARCHIVE_BYTES,
        )
        if (
            len(payload) != entry["size"]
            or hashlib.sha256(payload).hexdigest() != entry["sha256"].casefold()
        ):
            raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
    if not _identity_maps_match(
        expected,
        dict(_capture_owned_install_identity(target, relatives)),
    ):
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
    return _VerifiedReceipt(
        receipt,
        ownership_identity=captured,
    )


def _capture_owned_install_identity(
    target: Path, relatives: Sequence[str]
) -> tuple[tuple[str, _FileIdentity], ...]:
    return _capture_owned_install_identity_raw(target, relatives)


def _capture_owned_install_identity_raw(
    target: Path, relatives: Sequence[str]
) -> tuple[tuple[str, _FileIdentity], ...]:
    paths: dict[str, Path] = {".": target, RECEIPT_NAME: target / RECEIPT_NAME}
    for relative in relatives:
        parts = relative.split("/")
        for index in range(1, len(parts)):
            parent_relative = "/".join(parts[:index])
            paths[parent_relative] = target / Path(*parts[:index])
        paths[relative] = target / Path(*parts)
    try:
        return tuple((relative, _file_identity(path)) for relative, path in sorted(paths.items()))
    except OSError as exc:
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY) from exc


def _identity_maps_match(
    expected: dict[str, _FileIdentity],
    actual: dict[str, _FileIdentity],
    *,
    subset: bool = False,
) -> bool:
    if (not subset and expected.keys() != actual.keys()) or (
        subset and not actual.keys() <= expected.keys()
    ):
        return False
    for relative, observed in actual.items():
        prior = expected[relative]
        if stat.S_ISDIR(prior.mode):
            if not _same_directory_object(observed, prior):
                return False
        elif observed != prior:
            return False
    return True


def _guarded_read_owned_file(
    target: Path,
    relative: str,
    expected: dict[str, _FileIdentity],
    *,
    maximum_bytes: int,
    receipt: bool = False,
) -> bytes:
    failure = "OBS_RECEIPT_INVALID" if receipt else "OBS_PLUGIN_DRIFT"
    try:
        _require_expected_directory_objects(target, expected)
        path = _owned_path(target, relative)
        prior = expected[relative]
        if (
            not stat.S_ISREG(prior.mode)
            or prior.links != 1
            or prior.attributes & FILE_ATTRIBUTE_REPARSE_POINT
            or prior.size > maximum_bytes
            or _file_identity(path) != prior
        ):
            raise OSError("unsafe owned file")
        with path.open("rb") as stream:
            opened = _identity_from_stat(os.fstat(stream.fileno()))
            if opened != prior:
                raise OSError("owned file changed before read")
            payload = stream.read(maximum_bytes + 1)
            if len(payload) != opened.size or len(payload) > maximum_bytes:
                raise OSError("owned file changed during read")
            if _identity_from_stat(os.fstat(stream.fileno())) != opened:
                raise OSError("owned file changed during read")
        if _file_identity(path) != opened:
            raise OSError("owned file changed after read")
        _require_expected_directory_objects(target, expected)
        return payload
    except (KeyError, OSError) as exc:
        raise InstallError(failure, "verify", EXIT_VERIFY) from exc


def _require_owned_source_identity(
    target: Path,
    relative: str,
    expected: dict[str, _FileIdentity],
) -> None:
    parts = relative.split("/")
    try:
        _require_expected_directory_objects(target, expected)
        if _file_identity(target / Path(*parts)) != expected[relative]:
            raise OSError("owned file identity changed")
    except (KeyError, OSError) as exc:
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY) from exc


def _require_expected_directory_objects(target: Path, expected: dict[str, _FileIdentity]) -> None:
    for relative, prior in expected.items():
        if not stat.S_ISDIR(prior.mode):
            continue
        path = target if relative == "." else target / Path(*relative.split("/"))
        if not _same_directory_object(_file_identity(path), prior):
            raise OSError("owned directory identity changed")


def _same_directory_object(actual: _FileIdentity, expected: _FileIdentity) -> bool:
    # Directory link counts are topology metadata and can change while the same
    # physical directory remains open (notably on APFS during child moves).
    return (
        stat.S_ISDIR(actual.mode)
        and actual.device == expected.device
        and actual.inode == expected.inode
        and actual.mode == expected.mode
        and actual.attributes == expected.attributes
    )


def _same_file_object(actual: _FileIdentity, expected: _FileIdentity) -> bool:
    return (
        stat.S_ISREG(actual.mode)
        and stat.S_ISREG(expected.mode)
        and actual.device == expected.device
        and actual.inode == expected.inode
        and actual.attributes == expected.attributes
    )


def _snapshot_owned_files(
    target: Path,
    relatives: Sequence[str],
    expected: dict[str, _FileIdentity],
) -> dict[str, tuple[bytes, int]]:
    snapshot: dict[str, tuple[bytes, int]] = {}
    for relative in relatives:
        _require_owned_source_identity(target, relative, expected)
        path = target / Path(*relative.split("/"))
        try:
            with path.open("rb") as stream:
                opened = _identity_from_stat(os.fstat(stream.fileno()))
                if opened != expected[relative]:
                    raise OSError("owned file changed before snapshot")
                payload = stream.read(MAX_ARCHIVE_BYTES + 1)
                if len(payload) != opened.size or len(payload) > MAX_ARCHIVE_BYTES:
                    raise OSError("owned file changed during snapshot")
                if _identity_from_stat(os.fstat(stream.fileno())) != opened:
                    raise OSError("owned file changed during snapshot")
            if _file_identity(path) != opened:
                raise OSError("owned path changed during snapshot")
        except OSError as exc:
            raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY) from exc
        snapshot[relative] = (payload, stat.S_IMODE(opened.mode))
    return snapshot


def _restore_owned_snapshot(destination: Path, snapshot: tuple[bytes, int]) -> None:
    payload, mode = snapshot
    with destination.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    destination.chmod(mode)


def _prepare_recovery_destination(
    recovery: Path,
    relative: str,
    expected: dict[str, _FileIdentity],
) -> Path:
    destination = _prepare_owned_destination(recovery, relative)
    for index in range(1, len(relative.split("/"))):
        parent_relative = "/".join(relative.split("/")[:index])
        observed = _file_identity(recovery / Path(*parent_relative.split("/")))
        prior = expected.setdefault(parent_relative, observed)
        if not _same_directory_object(observed, prior):
            raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL)
    _require_recovery_directory_objects(recovery, expected)
    return destination


def _record_owned_destination(
    target: Path,
    relative: str,
    expected: dict[str, _FileIdentity],
) -> None:
    parts = relative.split("/")
    for index in range(1, len(parts)):
        parent_relative = "/".join(parts[:index])
        observed = _file_identity(target / Path(*parts[:index]))
        prior = expected.setdefault(parent_relative, observed)
        if not _same_directory_object(observed, prior):
            raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
    expected[relative] = _file_identity(target / Path(*parts))
    _require_owned_source_identity(target, relative, expected)


def _require_recovery_directory_objects(
    recovery: Path,
    expected: dict[str, _FileIdentity],
) -> None:
    try:
        for relative, prior in expected.items():
            if not stat.S_ISDIR(prior.mode):
                continue
            path = recovery if relative == "." else recovery / Path(*relative.split("/"))
            if not _same_directory_object(_file_identity(path), prior):
                raise OSError("recovery directory identity changed")
    except OSError as exc:
        raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL) from exc


def _recovery_source_matches(
    recovery: Path,
    relative: str,
    expected: dict[str, _FileIdentity],
) -> bool:
    try:
        root = expected["."]
        if not _same_directory_object(_file_identity(recovery), root):
            return False
        parts = relative.split("/")
        for index in range(1, len(parts)):
            parent_relative = "/".join(parts[:index])
            prior = expected[parent_relative]
            path = recovery / Path(*parts[:index])
            if not _same_directory_object(_file_identity(path), prior):
                return False
        return _file_identity(recovery / Path(*parts)) == expected[relative]
    except (KeyError, OSError):
        return False


def _require_exact_recovery_inventory(
    recovery: Path,
    expected: dict[str, _FileIdentity],
) -> None:
    _require_recovery_directory_objects(recovery, expected)
    expected_children: dict[str, set[str]] = {
        relative: set() for relative, identity in expected.items() if stat.S_ISDIR(identity.mode)
    }
    for relative in expected:
        if relative == ".":
            continue
        parts = relative.split("/")
        parent = "." if len(parts) == 1 else "/".join(parts[:-1])
        if parent not in expected_children:
            raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL)
        expected_children[parent].add(parts[-1])
    try:
        for relative, names in expected_children.items():
            path = recovery if relative == "." else recovery / Path(*relative.split("/"))
            if {child.name for child in path.iterdir()} != names:
                raise OSError("recovery inventory changed")
        for relative, prior in expected.items():
            if relative == "." or stat.S_ISDIR(prior.mode):
                continue
            path = recovery / Path(*relative.split("/"))
            if _file_identity(path) != prior:
                raise OSError("recovery file identity changed")
    except OSError as exc:
        raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL) from exc


def _require_published_identity(expected: dict[str, _FileIdentity]) -> None:
    if not isinstance(expected, _RecoveryIdentity) or not expected.publication_guard_active:
        return
    observed = dict(
        _capture_owned_install_identity_raw(
            expected.published_target,
            expected.published_relatives,
        )
    )
    if not _identity_maps_match(expected.published_identity, observed):
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)


def _published_objects_owned(expected: _RecoveryIdentity) -> bool:
    try:
        observed = dict(
            _capture_owned_install_identity_raw(
                expected.published_target,
                expected.published_relatives,
            )
        )
    except (InstallError, OSError):
        return False
    return _identity_maps_same_objects(expected.published_identity, observed)


def _identity_maps_same_objects(
    expected: dict[str, _FileIdentity],
    observed: dict[str, _FileIdentity],
    *,
    subset: bool = False,
) -> bool:
    if (not subset and expected.keys() != observed.keys()) or (
        subset and not expected.keys() <= observed.keys()
    ):
        return False
    for relative, prior in expected.items():
        current = observed[relative]
        if stat.S_ISDIR(prior.mode):
            if not _same_directory_object(current, prior):
                return False
        elif not _same_file_object(current, prior):
            return False
    return True


def _retire_owned_recovery(
    recovery: Path,
    expected: dict[str, _FileIdentity],
) -> None:
    for relative in sorted(
        (
            relative
            for relative, identity in expected.items()
            if relative != "." and not stat.S_ISDIR(identity.mode)
        ),
        key=lambda value: len(value.split("/")),
        reverse=True,
    ):
        _require_published_identity(expected)
        _require_exact_recovery_inventory(recovery, expected)
        path = recovery / Path(*relative.split("/"))
        if _file_identity(path) != expected[relative]:
            raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL)
        path.unlink()
        expected.pop(relative)
    for relative in sorted(
        (
            relative
            for relative, identity in expected.items()
            if relative != "." and stat.S_ISDIR(identity.mode)
        ),
        key=lambda value: len(value.split("/")),
        reverse=True,
    ):
        _require_published_identity(expected)
        _require_exact_recovery_inventory(recovery, expected)
        path = recovery / Path(*relative.split("/"))
        if not _same_directory_object(_file_identity(path), expected[relative]):
            raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL)
        path.rmdir()
        expected.pop(relative)
    _require_published_identity(expected)
    _require_exact_recovery_inventory(recovery, expected)
    if not _same_directory_object(_file_identity(recovery), expected["."]):
        raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL)
    recovery.rmdir()
    expected.pop(".")


def _file_identity(path: Path) -> _FileIdentity:
    return _identity_from_stat(path.lstat())


def _identity_from_stat(info: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=int(info.st_dev),
        inode=int(info.st_ino),
        mode=int(info.st_mode),
        links=int(info.st_nlink),
        size=int(info.st_size),
        modified_ns=int(info.st_mtime_ns),
        attributes=int(getattr(info, "st_file_attributes", 0)),
    )


def _receipt_relatives(receipt: dict[str, Any]) -> list[str]:
    files = receipt.get("files")
    if not isinstance(files, list):
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    relatives: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
        try:
            relatives.append(_portable_relative(entry.get("path")))
        except ValueError as exc:
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY) from exc
    return relatives


def _owned_path(target: Path, relative: str) -> Path:
    current = target
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            current /= part
            info = current.lstat()
            attributes = int(getattr(info, "st_file_attributes", 0))
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or attributes & FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise OSError("unsafe owned directory")
    except OSError as exc:
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY) from exc
    return current / parts[-1]


def _prepare_owned_destination(target: Path, relative: str) -> Path:
    current = target
    try:
        for part in relative.split("/")[:-1]:
            current /= part
            if os.path.lexists(current):
                info = current.lstat()
                attributes = int(getattr(info, "st_file_attributes", 0))
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or stat.S_ISLNK(info.st_mode)
                    or attributes & FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise OSError("unsafe owned directory")
            else:
                current.mkdir()
    except OSError as exc:
        raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY) from exc
    return current / relative.split("/")[-1]


def _prune_empty_owned_directories(target: Path, relatives: Sequence[str]) -> None:
    directories = {
        target / Path(*relative.split("/")[:index])
        for relative in relatives
        for index in range(1, len(relative.split("/")))
    }
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        with suppress(OSError):
            directory.rmdir()


def _uninstall(target: Path) -> None:
    receipt = _verify(target)
    relatives = _receipt_relatives(receipt)
    expected = dict(receipt.ownership_identity)
    snapshot = _snapshot_owned_files(target, [*relatives, RECEIPT_NAME], expected)
    quarantine = target.with_name(f".{target.name}.remove")
    if os.path.lexists(quarantine):
        raise InstallError("OBS_RECOVERY_REQUIRED", "preflight", EXIT_PREFLIGHT)
    quarantine.mkdir()
    moved: list[str] = []
    recovery_expected: dict[str, _FileIdentity] = {".": _file_identity(quarantine)}
    try:
        for relative in [*relatives, RECEIPT_NAME]:
            source = _owned_path(target, relative)
            destination = _prepare_recovery_destination(quarantine, relative, recovery_expected)
            _require_owned_source_identity(target, relative, expected)
            source.replace(destination)
            if _file_identity(destination) != expected[relative]:
                raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
            recovery_expected[relative] = expected[relative]
            moved.append(relative)
        _prune_empty_owned_directories(target, relatives)
        if not _same_directory_object(_file_identity(target), expected["."]):
            raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
        with suppress(OSError):
            target.rmdir()
        _retire_owned_recovery(quarantine, recovery_expected)
    except BaseException as exc:
        rollback_failed = False
        if not target.exists():
            try:
                target.mkdir()
            except OSError:
                rollback_failed = True
        for relative in reversed(moved):
            try:
                source = _owned_path(quarantine, relative)
                destination = _prepare_owned_destination(target, relative)
                if os.path.lexists(destination):
                    rollback_failed = True
                    continue
                if relative not in recovery_expected or not os.path.lexists(source):
                    _restore_owned_snapshot(destination, snapshot[relative])
                    recovery_expected.pop(relative, None)
                elif _recovery_source_matches(quarantine, relative, recovery_expected):
                    source.replace(destination)
                    recovery_expected.pop(relative)
                else:
                    _restore_owned_snapshot(destination, snapshot[relative])
                    rollback_failed = True
            except (InstallError, OSError):
                rollback_failed = True
        try:
            _retire_owned_recovery(quarantine, recovery_expected)
        except (InstallError, OSError):
            rollback_failed = True
        if rollback_failed:
            raise InstallError("OBS_RECOVERY_REQUIRED", "install", EXIT_INSTALL) from exc
        if isinstance(exc, InstallError):
            raise exc
        raise InstallError("OBS_UNINSTALL_FAILED", "install", EXIT_INSTALL) from exc


def _parse_receipt(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY) from exc
    if not isinstance(value, dict):
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    return value


def _read_archive_member(archive: bytes, name: str) -> bytes:
    if zipfile.is_zipfile(io.BytesIO(archive)):
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            members = package.infolist()
            if len(members) > MAX_MEMBERS + 1:
                raise ValueError("too many members")
            matches = [member for member in members if member.filename == name]
            if len(matches) != 1 or matches[0].is_dir() or matches[0].file_size > MAX_ARCHIVE_BYTES:
                raise KeyError(name)
            mode = matches[0].external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ValueError("unsafe member")
            return package.read(matches[0])
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as package:
        members = package.getmembers()
        if len(members) > MAX_MEMBERS + 1:
            raise ValueError("too many members")
        matches = [member for member in members if member.name == name]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > MAX_ARCHIVE_BYTES:
            raise KeyError(name)
        stream = package.extractfile(matches[0])
        if stream is None:
            raise KeyError(name)
        return stream.read(MAX_ARCHIVE_BYTES + 1)


def _validate_archive_inventory(archive: bytes, expected_sources: set[str]) -> None:
    expected = {"dcc-mcp-obs-plugin.json", *expected_sources}
    names: set[str] = set()
    total_size = 0
    try:
        if zipfile.is_zipfile(io.BytesIO(archive)):
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                members = package.infolist()
                for member in members:
                    name = _safe_relative(member.filename).casefold()
                    mode = member.external_attr >> 16
                    if member.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                        raise ValueError("unsafe archive member")
                    if member.file_size > MAX_ARCHIVE_BYTES:
                        raise ValueError("oversized archive member")
                    if member.file_size and (
                        member.compress_size <= 0
                        or member.file_size > member.compress_size * MAX_COMPRESSION_RATIO
                    ):
                        raise ValueError("unsafe archive compression ratio")
                    if name in names:
                        raise ValueError("duplicate archive member")
                    names.add(name)
                    total_size += member.file_size
        else:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as package:
                members = package.getmembers()
                for member in members:
                    name = _safe_relative(member.name).casefold()
                    if not member.isfile() or member.size > MAX_ARCHIVE_BYTES or name in names:
                        raise ValueError("unsafe archive member")
                    names.add(name)
                    total_size += member.size
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE) from exc
    if names != expected or total_size > MAX_ARCHIVE_BYTES:
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)


def _safe_relative(value: object) -> str:
    try:
        normalized = _portable_relative(value)
    except ValueError as exc:
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE) from exc
    if normalized.casefold() == RECEIPT_NAME.casefold():
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
    return normalized


def _portable_relative(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value != unicodedata.normalize("NFKC", value)
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    ):
        raise ValueError("invalid path")
    try:
        if len(value.encode("utf-8")) > MAX_PORTABLE_PATH_BYTES:
            raise ValueError("path too long")
    except UnicodeError as exc:
        raise ValueError("invalid path") from exc
    parts = value.split("/")
    if len(parts) > 16 or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("invalid path")
    reserved = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"}
    reserved.update(f"com{index}" for index in range(1, 10))
    reserved.update(f"lpt{index}" for index in range(1, 10))
    for part in parts:
        if len(part.encode("utf-8")) > MAX_PORTABLE_COMPONENT_BYTES:
            raise ValueError("path component too long")
        if part.endswith((".", " ")) or ":" in part:
            raise ValueError("non-portable path")
        if part.split(".", 1)[0].casefold() in reserved:
            raise ValueError("reserved path")
    return "/".join(parts)


def _require_safe_target_path(target: Path) -> None:
    for component in reversed((target, *target.parents)):
        if not os.path.lexists(component):
            continue
        try:
            info = component.lstat()
        except OSError as exc:
            raise InstallError("OBS_TARGET_UNSAFE", "preflight", EXIT_PREFLIGHT) from exc
        attributes = int(getattr(info, "st_file_attributes", 0))
        if stat.S_ISLNK(info.st_mode) or attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise InstallError("OBS_TARGET_UNSAFE", "preflight", EXIT_PREFLIGHT)


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def main(argv: Sequence[str] | None = None) -> None:
    code, report = run(list(argv) if argv is not None else sys.argv[1:])
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    raise SystemExit(code)


__all__ = ["InstallError", "default_plugin_dir", "main", "run"]
