"""DCC-MCP control plane for the native OBS plugin."""

from .__version__ import __version__
from .bridge import BridgeError, ObsControlBridge
from .config import ConfigError, ObsEndpointConfig
from .server import ObsMcpServer, start_server, stop_server

__all__ = [
    "BridgeError",
    "ConfigError",
    "ObsControlBridge",
    "ObsEndpointConfig",
    "ObsMcpServer",
    "__version__",
    "start_server",
    "stop_server",
]
