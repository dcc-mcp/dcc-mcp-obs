from __future__ import annotations

import pytest

from dcc_mcp_obs import __version__
from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge

IDENTITY = {
    "instanceId": "issue-3",
    "pluginVersion": __version__,
    "obsVersion": "31.1.1",
    "hostPid": 1234,
    "eventSequence": 1,
    "ok": True,
}


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def vendor_request(self, request_type, data, *, deadline=None):
        self.requests.append((request_type, dict(data)))
        return self.responses.pop(0)


def test_profile_selection_is_exact_and_verified():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "profiles": [{"profileName": "Main"}],
                "truncated": False,
                "eventSequence": 2,
            },
            {**IDENTITY, "accepted": True, "eventSequence": 3},
            {**IDENTITY, "profileName": "Main", "eventSequence": 4},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    result = bridge.set_current_profile("Main")
    assert result["profileName"] == "Main"
    assert result["verified"] is True
    assert transport.requests[2] == ("SetCurrentProfile", {"profileName": "Main"})


def test_scene_collection_selection_rejects_ambiguous_target():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "sceneCollections": [
                    {"sceneCollectionName": "Main"},
                    {"sceneCollectionName": "Main"},
                ],
                "truncated": False,
                "eventSequence": 2,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_TARGET_AMBIGUOUS"):
        bridge.set_current_scene_collection("Main")


def test_allowlisted_hotkey_and_screenshot_redact_path():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 2},
            {
                **IDENTITY,
                "accepted": True,
                "screenshotId": "shot-1",
                "imageFormat": "png",
                "path": r"C:\Users\operator\secret\shot.png",
                "pathRedacted": True,
                "eventSequence": 3,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    hotkey = bridge.trigger_allowlisted_hotkey("OBSBasic.StartStreaming")
    screenshot = bridge.capture_source_screenshot("Program")
    assert hotkey["triggered"] is True
    assert screenshot["screenshotId"] == "shot-1"
    assert "path" not in screenshot
    assert "secret" not in str(screenshot)


def test_unknown_hotkey_fails_closed_before_transport_call():
    transport = FakeTransport([{**IDENTITY, "ready": True}])
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_HOTKEY_NOT_ALLOWLISTED"):
        bridge.trigger_allowlisted_hotkey("arbitrary-key-sequence")
    assert [name for name, _data in transport.requests] == ["GetPluginStatus"]
