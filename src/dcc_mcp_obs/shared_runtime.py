"""Entry point for the shared dcc-mcp-runtime deployment."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence


def require_runtime() -> object:
    """Validate the runtime/adapter manifest handshake before starting OBS."""
    try:
        from dcc_mcp_runtime.bootstrap import require_adapter
    except ImportError as exc:  # pragma: no cover - deployment-only branch
        raise RuntimeError(
            "DCC_MCP_RUNTIME_MISSING: install dcc-mcp-runtime and the OBS adapter wheel"
        ) from exc
    result = require_adapter("obs")
    os.environ.setdefault("DCC_MCP_PYTHON_EXECUTABLE", sys.executable)
    return result


def main(argv: Sequence[str] | None = None) -> None:
    """Start OBS through the shared runtime after a capability handshake."""
    require_runtime()
    from .server import main as server_main

    server_main(argv)


__all__ = ["main", "require_runtime"]
