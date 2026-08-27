from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from dcc_mcp_obs.bridge import ObsControlBridge
from dcc_mcp_obs.config import ObsEndpointConfig
from dcc_mcp_obs.process import resolve_obs_pid
from dcc_mcp_obs.protocol import ObsWebSocketTransport


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
