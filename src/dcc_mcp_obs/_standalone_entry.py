"""Entrypoint for the self-contained OBS sidecar distribution."""

from __future__ import annotations

import json
import os
import runpy
import sys
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path

from . import install_cli, server
from .__version__ import __version__

_INSTALL_COMMANDS = frozenset({"install", "upgrade", "status", "verify", "uninstall"})
_PYTHON_SCRIPT_SUFFIXES = frozenset({".py", ".pyw"})
_STANDALONE_MANIFEST = "dcc-mcp-obs-standalone.json"
_BUNDLED_PLUGIN = "dcc-mcp-obs-plugin.zip"


def _is_skill_script_invocation(argv: Sequence[str]) -> bool:
    if len(argv) < 2:
        return False
    script = Path(argv[1])
    return script.suffix.lower() in _PYTHON_SCRIPT_SUFFIXES and script.is_file()


def _run_skill_script(argv: Sequence[str]) -> None:
    script = str(Path(argv[1]).resolve())
    original_argv = sys.argv
    sys.argv = [script, *argv[2:]]
    try:
        runpy.run_path(script, run_name="__main__")
    finally:
        sys.argv = original_argv


def _standalone_root() -> Path:
    return Path(sys.executable).resolve().parent


def _bundled_plugin_install_args(command: str, trailing: Sequence[str]) -> list[str]:
    root = _standalone_root()
    manifest_path = root / _STANDALONE_MANIFEST
    plugin_path = root / _BUNDLED_PLUGIN
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        plugin_entry = next(entry for entry in files if entry.get("path") == _BUNDLED_PLUGIN)
        digest = plugin_entry["sha256"]
        size = plugin_entry["size"]
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise SystemExit(f"Bundled native plugin metadata is invalid: {exc}") from exc
    if (
        manifest.get("schema_version") != 1
        or manifest.get("product") != "dcc-mcp-obs-standalone"
        or manifest.get("version") != __version__
        or manifest.get("platform") != install_cli._platform_name()
        or manifest.get("core_version") != metadata.version("dcc-mcp-core")
        or not isinstance(digest, str)
        or len(digest) != 64
        or not isinstance(size, int)
        or size <= 0
        or not plugin_path.is_file()
        or plugin_path.stat().st_size != size
    ):
        raise SystemExit("Bundled native plugin metadata does not match this distribution")
    return [
        command,
        "--plugin-archive",
        str(plugin_path),
        "--sha256",
        digest,
        *trailing,
    ]


def main(argv: Sequence[str] | None = None) -> None:
    """Run a sidecar, installer command, bundled install, or managed skill script."""
    resolved = list(sys.argv if argv is None else argv)
    os.environ["DCC_MCP_PYTHON_EXECUTABLE"] = sys.executable
    if _is_skill_script_invocation(resolved):
        _run_skill_script(resolved)
        return
    arguments = resolved[1:]
    if arguments and arguments[0] in {"install-bundled", "upgrade-bundled"}:
        command = arguments[0].removesuffix("-bundled")
        install_cli.main(_bundled_plugin_install_args(command, arguments[1:]))
        return
    if arguments and arguments[0] in _INSTALL_COMMANDS:
        install_cli.main(arguments)
        return
    server.main(arguments)


if __name__ == "__main__":
    main()
