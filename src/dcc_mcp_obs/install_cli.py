"""Install SOP v1 lifecycle for signed native OBS plugin bundles."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
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
RECEIPT_NAME = ".dcc-mcp-obs-install.json"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class InstallError(RuntimeError):
    def __init__(self, code: str, stage: str, exit_code: int) -> None:
        self.code = code
        self.stage = stage
        self.exit_code = exit_code
        super().__init__(code)


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


def _validate_bundle(archive: Path, expected_digest: str) -> dict[str, Any]:
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise InstallError("OBS_BUNDLE_DIGEST_INVALID", "acquire", EXIT_ACQUIRE)
    try:
        int(expected_digest, 16)
    except ValueError as exc:
        raise InstallError("OBS_BUNDLE_DIGEST_INVALID", "acquire", EXIT_ACQUIRE) from exc
    archive = archive.expanduser().resolve()
    try:
        info = archive.stat()
    except OSError as exc:
        raise InstallError("OBS_BUNDLE_UNAVAILABLE", "acquire", EXIT_ACQUIRE) from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_ARCHIVE_BYTES:
        raise InstallError("OBS_BUNDLE_UNSAFE", "acquire", EXIT_ACQUIRE)
    if _sha256_file(archive) != expected_digest.casefold():
        raise InstallError("OBS_BUNDLE_DIGEST_MISMATCH", "acquire", EXIT_ACQUIRE)
    try:
        raw_manifest = _read_archive_member(archive, "dcc-mcp-obs-plugin.json")
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
    _validate_archive_inventory(archive, seen_sources)
    for source, digest in payload_checks:
        payload = _read_archive_member(archive, source)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise InstallError("OBS_BUNDLE_MEMBER_MISMATCH", "acquire", EXIT_ACQUIRE)
    manifest["_archive"] = str(archive)
    return manifest


def _install(plan: dict[str, Any], target: Path, *, allow_existing: bool) -> None:
    if target.exists():
        if not allow_existing:
            raise InstallError("OBS_PLUGIN_ALREADY_INSTALLED", "preflight", EXIT_PREFLIGHT)
        _verify(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _require_safe_target_path(target)
    stage = Path(tempfile.mkdtemp(prefix=".dcc-mcp-obs-stage-", dir=target.parent))
    backup = target.with_name(f".{target.name}.backup")
    archive = Path(plan["_archive"])
    try:
        receipt_files: list[dict[str, object]] = []
        for entry in plan["files"]:
            relative = _safe_relative(entry["target"])
            destination = stage / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = _read_archive_member(archive, _safe_relative(entry["source"]))
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
        if os.path.lexists(backup):
            raise InstallError("OBS_RECOVERY_REQUIRED", "preflight", EXIT_PREFLIGHT)
        if target.exists():
            target.replace(backup)
        try:
            stage.replace(target)
        except BaseException:
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    except InstallError:
        raise
    except BaseException as exc:
        raise InstallError("OBS_INSTALL_FAILED", "install", EXIT_INSTALL) from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _verify(target: Path) -> dict[str, Any]:
    _require_safe_target_path(target)
    if not target.is_dir():
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    receipt = _read_receipt(target / RECEIPT_NAME)
    if receipt.get("product") != "dcc-mcp-obs" or receipt.get("version") != __version__:
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    for entry in files:
        if not isinstance(entry, dict):
            raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
        relative = _safe_relative(entry.get("path"))
        path = target / Path(relative)
        try:
            info = path.lstat()
        except OSError as exc:
            raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY) from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
        if info.st_size != entry.get("size") or _sha256_file(path) != entry.get("sha256"):
            raise InstallError("OBS_PLUGIN_DRIFT", "verify", EXIT_VERIFY)
    return receipt


def _uninstall(target: Path) -> None:
    _verify(target)
    quarantine = target.with_name(f".{target.name}.remove")
    if os.path.lexists(quarantine):
        raise InstallError("OBS_RECOVERY_REQUIRED", "preflight", EXIT_PREFLIGHT)
    try:
        target.replace(quarantine)
        shutil.rmtree(quarantine)
    except BaseException as exc:
        if quarantine.exists() and not target.exists():
            quarantine.replace(target)
        raise InstallError("OBS_UNINSTALL_FAILED", "install", EXIT_INSTALL) from exc


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65_536:
            raise OSError("unsafe receipt")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY) from exc
    if not isinstance(value, dict):
        raise InstallError("OBS_RECEIPT_INVALID", "verify", EXIT_VERIFY)
    return value


def _read_archive_member(archive: Path, name: str) -> bytes:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as package:
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
    with tarfile.open(archive, mode="r:*") as package:
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


def _validate_archive_inventory(archive: Path, expected_sources: set[str]) -> None:
    expected = {"dcc-mcp-obs-plugin.json", *expected_sources}
    names: set[str] = set()
    total_size = 0
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as package:
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
            with tarfile.open(archive, mode="r:*") as package:
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
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(ch) < 32 for ch in value)
    ):
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or len(path.parts) > 16:
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
    normalized = path.as_posix()
    if normalized.casefold() == RECEIPT_NAME.casefold():
        raise InstallError("OBS_BUNDLE_INVALID", "acquire", EXIT_ACQUIRE)
    return normalized


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
