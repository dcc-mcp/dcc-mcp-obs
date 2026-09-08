from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from functools import wraps
from typing import Any, TypeVar, cast

from dcc_mcp_core.skill import skill_entry, skill_error

from dcc_mcp_obs.bridge import PUBLIC_DOWNSTREAM_ERRORS, BridgeError, ObsControlBridge
from dcc_mcp_obs.config import ObsEndpointConfig
from dcc_mcp_obs.process import resolve_obs_pid
from dcc_mcp_obs.protocol import ObsWebSocketTransport

_F = TypeVar("_F", bound=Callable[..., Mapping[str, object]])


def obs_skill_entry(func: _F) -> _F:
    """Expose stable OBS bridge codes through the public skill envelope."""

    @skill_entry
    @wraps(func)
    def wrapper(**kwargs: Any) -> Mapping[str, object]:
        try:
            return func(**kwargs)
        except BridgeError as exc:
            code = exc.code if exc.code in PUBLIC_DOWNSTREAM_ERRORS else "OBS_REQUEST_FAILED"
            return skill_error(
                f"OBS request failed: {code}",
                code,
                _meta={"dcc.error": {"type": "BridgeError", "message": code}},
                error_type="BridgeError",
            )

    return cast(_F, wrapper)


@contextmanager
def obs_bridge() -> Iterator[ObsControlBridge]:
    transport = ObsWebSocketTransport(ObsEndpointConfig.from_environment())
    try:
        yield ObsControlBridge(
            transport,
            expected_pid=resolve_obs_pid(),
            expected_instance_id=os.environ.get("DCC_MCP_OBS_INSTANCE_ID") or None,
        )
    finally:
        transport.close()
