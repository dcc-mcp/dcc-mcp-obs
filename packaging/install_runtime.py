"""Verify and install one extracted DCC-MCP OBS shared-runtime bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


class InstallFailure(RuntimeError):
    """A stable, user-facing bundle installation failure."""


def _member(root: Path, value: object) -> Path:
    """Resolve one manifest member without permitting traversal or aliases."""
    if not isinstance(value, str):
        raise InstallFailure("BUNDLE_MANIFEST_INVALID: member path is not a string")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or "\\" in value
        or ":" in value
        or ".." in relative.parts
        or "." in relative.parts
    ):
        raise InstallFailure(f"BUNDLE_MANIFEST_INVALID: unsafe member path {value!r}")
    target = root.joinpath(*relative.parts).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise InstallFailure(f"BUNDLE_MANIFEST_INVALID: escaped member path {value!r}") from exc
    return target


def _platform() -> str:
    """Map Python's platform name to the release matrix contract."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _verify(root: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Verify bundle identity, inventory, sizes, and digests before execution."""
    manifest_path = root / "dcc-mcp-obs-runtime.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallFailure("BUNDLE_MANIFEST_INVALID: cannot read release manifest") from exc
    if not isinstance(manifest, dict):
        raise InstallFailure("BUNDLE_MANIFEST_INVALID: release manifest is not an object")
    if not all(
        isinstance(manifest.get(field), str) and bool(manifest[field])
        for field in ("version", "runtime_version")
    ):
        raise InstallFailure("BUNDLE_MANIFEST_INVALID: version fields are missing")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("product") != "dcc-mcp-obs-runtime"
        or manifest.get("platform") != _platform()
    ):
        raise InstallFailure("BUNDLE_IDENTITY_MISMATCH: product or platform differs")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise InstallFailure("BUNDLE_MANIFEST_INVALID: empty file inventory")
    files: dict[str, bytes] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise InstallFailure("BUNDLE_MANIFEST_INVALID: file entry is not an object")
        name = entry.get("path")
        path = _member(root, name)
        if name in files:
            raise InstallFailure(f"BUNDLE_MANIFEST_INVALID: duplicate member {name!r}")
        if not path.is_file():
            raise InstallFailure(f"BUNDLE_FILE_MISSING: {name}")
        payload = path.read_bytes()
        if len(payload) != entry.get("size") or hashlib.sha256(payload).hexdigest() != entry.get(
            "sha256"
        ):
            raise InstallFailure(f"BUNDLE_DIGEST_MISMATCH: {name}")
        files[name] = payload
    return manifest, files


def _one(files: dict[str, bytes], *, prefix: str, suffix: str) -> tuple[str, bytes]:
    """Select exactly one verified file by its manifest-bound name."""
    matches = [
        item for item in files.items() if item[0].startswith(prefix) and item[0].endswith(suffix)
    ]
    if len(matches) != 1:
        raise InstallFailure(f"BUNDLE_PAYLOAD_INVALID: expected one {prefix}*{suffix}")
    return matches[0]


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one allow-listed installer command and retain output for one JSON report."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallFailure("INSTALL_COMMAND_TIMEOUT: command exceeded 600 seconds") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise InstallFailure(f"INSTALL_COMMAND_FAILED: {detail[:2000]}")
    return result


def install(argv: list[str] | None = None) -> dict[str, Any]:
    """Verify the extracted bundle and optionally perform its exact install plan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="approve the verified install plan")
    parser.add_argument("--dry-run", action="store_true", help="verify and print the plan only")
    parser.add_argument(
        "--plugin-dir", type=Path, help="explicit operator-owned OBS plugin directory"
    )
    args = parser.parse_args(argv)
    if not args.dry_run and not args.yes:
        raise InstallFailure("CONSENT_REQUIRED: pass --yes after reviewing --dry-run")

    root = Path(__file__).resolve().parent
    manifest, files = _verify(root)
    runtime_wheel = _one(files, prefix="wheels/dcc_mcp_runtime-", suffix=".whl")
    adapter_wheel = _one(files, prefix="wheels/dcc_mcp_obs-", suffix=".whl")
    plugin_payload = files.get("native/dcc-mcp-obs-plugin.zip")
    if plugin_payload is None:
        raise InstallFailure("BUNDLE_PAYLOAD_INVALID: native plugin is missing")
    plan: dict[str, Any] = {
        "schema_version": 1,
        "product": "dcc-mcp-obs-runtime-installer",
        "version": manifest["version"],
        "platform": manifest["platform"],
        "runtime_version": manifest["runtime_version"],
        "runtime_root": str(root),
        "python": sys.executable,
        "dry_run": args.dry_run,
        "files_verified": len(files),
        "steps": ["install-runtime-and-adapter-wheels", "install-native-plugin"],
    }
    if args.dry_run:
        plan["status"] = "planned"
        return plan

    with tempfile.TemporaryDirectory(prefix="dcc-mcp-obs-install-") as temporary:
        safe_cwd = Path(temporary)
        staged_runtime = safe_cwd / Path(runtime_wheel[0]).name
        staged_adapter = safe_cwd / Path(adapter_wheel[0]).name
        staged_plugin = safe_cwd / "dcc-mcp-obs-plugin.zip"
        staged_runtime.write_bytes(runtime_wheel[1])
        staged_adapter.write_bytes(adapter_wheel[1])
        staged_plugin.write_bytes(plugin_payload)
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                str(staged_runtime),
                str(staged_adapter),
            ],
            cwd=safe_cwd,
        )
        plugin_command = [
            sys.executable,
            "-c",
            "from dcc_mcp_obs.install_cli import main; main()",
            "install",
            "--plugin-archive",
            str(staged_plugin),
            "--sha256",
            hashlib.sha256(plugin_payload).hexdigest(),
        ]
        if args.plugin_dir is not None:
            plugin_command.extend(("--plugin-dir", str(args.plugin_dir.resolve())))
        plugin_report = _run(plugin_command, cwd=safe_cwd)
    try:
        plan["native_plugin"] = json.loads(plugin_report.stdout)
    except ValueError as exc:
        raise InstallFailure(
            "INSTALL_REPORT_INVALID: native installer did not return JSON"
        ) from exc
    plan["status"] = "installed"
    plan["environment"] = {"DCC_MCP_RUNTIME_ROOT": str(root)}
    plan["next_steps"] = [
        {
            "name": "start-runtime",
            "command": ["dcc-mcp-obs-runtime", "--host-pid", "<OBS_PID>"],
        },
        {
            "name": "wait-ready",
            "command": ["dcc-mcp-cli", "wait-ready", "--dcc-type", "obs"],
        },
    ]
    return plan


def main() -> int:
    """Emit one machine-readable report with stable success/failure status."""
    try:
        report = install()
        code = 0
    except InstallFailure as exc:
        report = {
            "schema_version": 1,
            "product": "dcc-mcp-obs-runtime-installer",
            "status": "failed",
            "error": str(exc),
        }
        code = 2
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
