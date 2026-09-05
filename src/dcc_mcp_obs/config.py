"""Operator-owned OBS endpoint configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """A stable, public-safe configuration failure."""


DEFAULT_OBS_WEBSOCKET_PORT = 4455
DEFAULT_CONTROL_PORT = 9766
TRANSPORT_MODES = frozenset({"websocket", "dual"})


@dataclass(frozen=True)
class ObsEndpointConfig:
    """A loopback OBS WebSocket endpoint with a write-only secret field."""

    host: str = "127.0.0.1"
    port: int = 4455
    secure: bool = False
    password: str = field(default="", repr=False)
    timeout_seconds: float = 5.0
    transport_mode: str = "dual"

    @classmethod
    def from_environment(cls) -> ObsEndpointConfig:
        raw = os.environ.get(
            "DCC_MCP_OBS_WEBSOCKET_URL", f"ws://127.0.0.1:{DEFAULT_OBS_WEBSOCKET_PORT}"
        )
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError as exc:
            raise ConfigError("OBS_ENDPOINT_INVALID") from exc
        if (
            parsed.scheme != "ws"
            or parsed.hostname != "127.0.0.1"
            or port is None
            or not (1 <= port <= 65535)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigError("OBS_ENDPOINT_INVALID")
        timeout_raw = os.environ.get("DCC_MCP_OBS_TIMEOUT_SECONDS", "5")
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise ConfigError("OBS_TIMEOUT_INVALID") from exc
        if not 0.1 <= timeout <= 30.0:
            raise ConfigError("OBS_TIMEOUT_INVALID")
        transport_mode = os.environ.get("DCC_MCP_OBS_TRANSPORT", "dual").strip().casefold()
        if transport_mode not in TRANSPORT_MODES:
            raise ConfigError("OBS_TRANSPORT_INVALID")
        return cls(
            host="127.0.0.1",
            port=port,
            password=os.environ.get("DCC_MCP_OBS_WEBSOCKET_PASSWORD", ""),
            timeout_seconds=timeout,
            transport_mode=transport_mode,
        )

    def public_summary(self, *, control_port: int = DEFAULT_CONTROL_PORT) -> dict[str, object]:
        if type(control_port) is not int or not 1 <= control_port <= 65535:
            raise ConfigError("OBS_CONTROL_PORT_INVALID")
        if control_port == self.port:
            raise ConfigError("OBS_PORT_CONFLICT")
        return {
            "host": self.host,
            "port": self.port,
            "secure": self.secure,
            "controlPort": control_port,
            "transportMode": self.transport_mode,
        }


__all__ = [
    "ConfigError",
    "DEFAULT_CONTROL_PORT",
    "DEFAULT_OBS_WEBSOCKET_PORT",
    "ObsEndpointConfig",
    "TRANSPORT_MODES",
]
