"""Entry point for the shared dcc-mcp-runtime deployment."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from .__version__ import __version__

_RUNTIME_METADATA_ENV = {
    "DCC_MCP_RUNTIME_ID": "runtime_id",
    "DCC_MCP_RUNTIME_VERSION": "runtime_version",
    "DCC_MCP_RUNTIME_CAPABILITIES_FINGERPRINT": "capabilities_fingerprint",
}


def require_runtime() -> object:
    """Validate the runtime/adapter manifest handshake before starting OBS."""
    try:
        from dcc_mcp_runtime.bootstrap import require_adapter
    except ImportError as exc:  # pragma: no cover - deployment-only branch
        raise RuntimeError(
            "DCC_MCP_RUNTIME_MISSING: install dcc-mcp-runtime and the OBS adapter wheel"
        ) from exc
    result = require_adapter("obs")
    handshake = getattr(result, "handshake", None)
    if handshake is None or getattr(handshake, "adapter_id", None) != "obs":
        raise RuntimeError("DCC_MCP_RUNTIME_HANDSHAKE_INVALID: missing OBS handshake")
    manifest_version = getattr(handshake, "adapter_version", None)
    if manifest_version != __version__:
        raise RuntimeError(
            "DCC_MCP_RUNTIME_ADAPTER_VERSION_MISMATCH: "
            f"manifest={manifest_version!r} installed={__version__!r}"
        )
    os.environ["DCC_MCP_PYTHON_EXECUTABLE"] = sys.executable
    for environment_name, attribute_name in _RUNTIME_METADATA_ENV.items():
        value = getattr(handshake, attribute_name, None)
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"DCC_MCP_RUNTIME_HANDSHAKE_INVALID: missing {attribute_name}")
        os.environ[environment_name] = value
    return result


def main(argv: Sequence[str] | None = None) -> None:
    """Start OBS through the shared runtime after a capability handshake."""
    require_runtime()
    from .server import main as server_main

    server_main(argv)


__all__ = ["main", "require_runtime"]
