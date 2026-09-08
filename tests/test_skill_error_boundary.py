from __future__ import annotations

from dcc_mcp_obs.bridge import BridgeError
from dcc_mcp_obs.skills.obs_control.scripts._client import obs_skill_entry


def test_obs_skill_entry_preserves_stable_bridge_error_code() -> None:
    @obs_skill_entry
    def failing_skill(**_kwargs):
        raise BridgeError("OBS_INSTANCE_NOT_READY")

    result = failing_skill()

    assert result["success"] is False
    assert result["error"] == "OBS_INSTANCE_NOT_READY"
    assert result["context"]["error_type"] == "BridgeError"
    assert result["_meta"]["dcc.error"] == {
        "type": "BridgeError",
        "message": "OBS_INSTANCE_NOT_READY",
    }


def test_obs_skill_entry_redacts_private_bridge_error_code() -> None:
    @obs_skill_entry
    def failing_skill(**_kwargs):
        raise BridgeError("OBS_PRIVATE_PASSWORD_LEAK")

    result = failing_skill()

    assert result["success"] is False
    assert result["error"] == "OBS_REQUEST_FAILED"
    assert "OBS_PRIVATE_PASSWORD_LEAK" not in str(result)
