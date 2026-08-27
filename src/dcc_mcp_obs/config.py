"""Operator-owned OBS endpoint configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """A stable, public-safe configuration failure."""


@dataclass(frozen=True)
class ObsEndpointConfig:
    """A loopback OBS WebSocket endpoint with a write-only secret field."""

    host: str = "127.0.0.1"
    port: int = 4455
    secure: bool = False
    password: str = field(default="", repr=False)
    timeout_seconds: float = 5.0

    @classmethod
    def from_environment(cls) -> ObsEndpointConfig:
        raw = os.environ.get("DCC_MCP_OBS_WEBSOCKET_URL", "ws://127.0.0.1:4455")
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
        return cls(
            host="127.0.0.1",
            port=port,
            password=os.environ.get("DCC_MCP_OBS_WEBSOCKET_PASSWORD", ""),
            timeout_seconds=timeout,
        )

    def public_summary(self) -> dict[str, object]:
        return {"host": self.host, "port": self.port, "secure": self.secure}


__all__ = ["ConfigError", "ObsEndpointConfig"]
