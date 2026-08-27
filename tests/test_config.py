from __future__ import annotations

import pytest

from dcc_mcp_obs.config import ConfigError, ObsEndpointConfig


def test_password_is_operator_owned_and_never_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCC_MCP_OBS_WEBSOCKET_PASSWORD", "PRIVATE_OBS_PASSWORD")
    config = ObsEndpointConfig.from_environment()

    assert config.password == "PRIVATE_OBS_PASSWORD"
    assert "PRIVATE_OBS_PASSWORD" not in repr(config)
    assert "PRIVATE_OBS_PASSWORD" not in str(config.public_summary())
    assert config.public_summary() == {"host": "127.0.0.1", "port": 4455, "secure": False}


@pytest.mark.parametrize(
    "url",
    [
        "ws://example.com:4455",
        "ws://127.0.0.1:4455/path",
        "ws://user:password@127.0.0.1:4455",
        "http://127.0.0.1:4455",
        "ws://127.0.0.1:4455?secret=value",
    ],
)
def test_first_slice_rejects_unbound_or_secret_bearing_endpoints(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("DCC_MCP_OBS_WEBSOCKET_URL", url)

    with pytest.raises(ConfigError, match="OBS_ENDPOINT_INVALID"):
        ObsEndpointConfig.from_environment()
