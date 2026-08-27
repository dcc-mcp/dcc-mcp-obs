"""Out-of-process DCC-MCP server bound to one native OBS plugin instance."""

from __future__ import annotations

import argparse
import os
import signal
import threading
from collections.abc import Sequence
from pathlib import Path

from dcc_mcp_core import DccServerOptions, HostExecutionBridge
from dcc_mcp_core.readiness import AdapterReadinessBinder
from dcc_mcp_core.server_base import DccServerBase

from .__version__ import __version__
from .bridge import ObsControlBridge
from .config import ObsEndpointConfig
from .dispatcher import ObsBridgeDispatcher
from .process import process_is_alive, resolve_obs_pid
from .protocol import ObsWebSocketTransport

_server: ObsMcpServer | None = None


class ObsMcpServer(DccServerBase):
    """DCC-MCP service backed by the native OBS vendor bridge."""

    def __init__(self, *, port: int | None = None, host_pid: int | None = None) -> None:
        resolved_pid = resolve_obs_pid(host_pid)
        config = ObsEndpointConfig.from_environment()
        self._transport = ObsWebSocketTransport(config)
        self._bridge = ObsControlBridge(self._transport, expected_pid=resolved_pid)
        status = self._bridge.status()
        os.environ["DCC_MCP_OBS_HOST_PID"] = str(resolved_pid)
        os.environ["DCC_MCP_OBS_INSTANCE_ID"] = str(status["instanceId"])
        execution_bridge = HostExecutionBridge(
            dispatcher=ObsBridgeDispatcher(),
            default_thread_affinity="any",
            default_execution="sync",
            default_timeout_hint_secs=30,
        )
        options = DccServerOptions.from_env(
            "obs",
            Path(__file__).resolve().parent / "skills",
            port=port,
            server_name="dcc-mcp-obs",
            server_version=__version__,
            adapter_version=__version__,
            dcc_version=str(status["obsVersion"]),
            dcc_pid=resolved_pid,
            instance_type="gui",
            host_rpc=f"ws://{config.host}:{config.port}",
            execution_bridge=execution_bridge,
        )
        super().__init__(options=options)
        self._readiness = AdapterReadinessBinder(self)
        self._readiness.mark_dispatcher_ready(
            True,
            host_execution_bridge_ready=True,
            main_thread_executor_ready=True,
            dcc_ready=True,
        )

    def stop(self) -> None:
        try:
            self._transport.close()
        finally:
            super().stop()


def start_server(*, port: int | None = None, host_pid: int | None = None) -> ObsMcpServer:
    global _server
    if _server is None or not _server.is_running:
        _server = ObsMcpServer(port=port, host_pid=host_pid)
        _server.register_builtin_actions()
        _server.start()
    return _server


def stop_server() -> None:
    global _server
    if _server is not None:
        _server.stop()
        _server = None


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the native DCC-MCP OBS adapter.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--host-pid", type=int)
    parser.add_argument("--mcp-port", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    import sys

    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    host_pid = resolve_obs_pid(args.host_pid)
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    start_server(port=args.mcp_port, host_pid=host_pid)
    try:
        while not stopped.wait(1.0) and process_is_alive(host_pid):
            pass
    finally:
        stop_server()


__all__ = ["ObsMcpServer", "main", "start_server", "stop_server"]
