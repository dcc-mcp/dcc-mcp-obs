from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

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


def test_selection_rejects_truncated_discovery_before_mutation():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "profiles": [{"profileName": "Main"}],
                "truncated": True,
                "eventSequence": 2,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INCOMPLETE"):
        bridge.set_current_profile("Main")
    assert len(transport.requests) == 2


@pytest.mark.parametrize(
    "request_type, field",
    [("ListProfiles", "profiles"), ("ListSceneCollections", "sceneCollections")],
)
def test_profile_discovery_rejects_more_than_native_cap(request_type, field):
    entry_key = "profileName" if field == "profiles" else "sceneCollectionName"
    entries = [{entry_key: f"Entry-{index}"} for index in range(129)]
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, field: entries, "truncated": False, "eventSequence": 2},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        getattr(bridge, "list_profiles" if field == "profiles" else "list_scene_collections")()


def test_profile_yaml_and_capability_schemas_reject_129_entries():
    profiles = [{"profileName": f"Profile-{index}"} for index in range(129)]
    collections = [{"sceneCollectionName": f"Collection-{index}"} for index in range(129)]
    tools = yaml.safe_load(
        Path("src/dcc_mcp_obs/skills/obs-control/tools.yaml").read_text(encoding="utf-8")
    )["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    for tool_name, field, entries in (
        ("list_profiles", "profiles", profiles),
        ("list_scene_collections", "sceneCollections", collections),
    ):
        envelope = {
            "success": True,
            "message": "ok",
            "error": None,
            "prompt": None,
            "context": {**IDENTITY, field: entries, "truncated": False},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(by_name[tool_name]["output_schema"]).validate(envelope)

    capability_schema = json.loads(
        Path("contracts/obs-profile-status-v1.schema.json").read_text(encoding="utf-8")
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(capability_schema).validate(
            {**IDENTITY, "profiles": profiles}
        )


def test_exact_cap_profile_list_is_complete_and_selectable():
    profiles = [{"profileName": f"Profile-{index}"} for index in range(128)]
    target = profiles[-1]["profileName"]
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "profiles": profiles, "truncated": False, "eventSequence": 2},
            {**IDENTITY, "accepted": True, "eventSequence": 3},
            {**IDENTITY, "profileName": target, "eventSequence": 4},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    assert bridge.set_current_profile(target)["profileName"] == target


def test_exact_cap_scene_collection_list_is_complete_and_selectable():
    collections = [{"sceneCollectionName": f"Collection-{index}"} for index in range(128)]
    target = collections[-1]["sceneCollectionName"]
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "sceneCollections": collections,
                "truncated": False,
                "eventSequence": 2,
            },
            {**IDENTITY, "accepted": True, "eventSequence": 3},
            {**IDENTITY, "sceneCollectionName": target, "eventSequence": 4},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    assert bridge.set_current_scene_collection(target)["sceneCollectionName"] == target


def test_allowlisted_hotkeys_require_truncated_field():
    transport = FakeTransport(
        [{**IDENTITY, "ready": True}, {**IDENTITY, "hotkeys": [], "eventSequence": 2}]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        bridge.list_allowlisted_hotkeys()


def test_operator_status_requires_ui_thread_and_redaction_fields():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "ready": True, "eventSequence": 2},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        bridge.get_operator_status()


def test_allowlisted_hotkey_and_screenshot_redact_path():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "accepted": True,
                "hotkeyName": "OBSBasic.StartStreaming",
                "eventSequence": 2,
            },
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
    assert hotkey["hotkeyName"] == "OBSBasic.StartStreaming"
    assert hotkey["accepted"] is True
    with pytest.raises(BridgeError, match="OBS_SCREENSHOT_UNVERIFIED"):
        bridge.capture_source_screenshot("Program")


def test_hotkey_success_matches_strict_tool_envelope():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "accepted": True,
                "hotkeyName": "start_streaming",
                "eventSequence": 2,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    raw = bridge.trigger_allowlisted_hotkey("start_streaming")
    envelope = {
        "success": True,
        "message": "ok",
        "error": None,
        "prompt": None,
        "context": raw,
    }
    tool = next(
        item
        for item in yaml.safe_load(
            Path("src/dcc_mcp_obs/skills/obs-control/tools.yaml").read_text(encoding="utf-8")
        )["tools"]
        if item["name"] == "trigger_allowlisted_hotkey"
    )
    errors = list(jsonschema.Draft202012Validator(tool["output_schema"]).iter_errors(envelope))
    assert not errors


def test_empty_current_profile_is_rejected_by_bridge_contract():
    transport = FakeTransport(
        [{**IDENTITY, "ready": True}, {**IDENTITY, "profileName": "", "eventSequence": 2}]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        bridge.get_current_profile()


def test_empty_operator_config_version_is_rejected_by_bridge_contract():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "ready": True,
                "uiThreadReady": True,
                "configPathRedacted": True,
                "configVersion": "",
                "eventSequence": 2,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        bridge.get_operator_status()


def test_unknown_hotkey_fails_closed_before_transport_call():
    transport = FakeTransport([{**IDENTITY, "ready": True}])
    bridge = ObsControlBridge(transport, expected_pid=1234)
    with pytest.raises(BridgeError, match="OBS_HOTKEY_NOT_ALLOWLISTED"):
        bridge.trigger_allowlisted_hotkey("arbitrary-key-sequence")
    assert [name for name, _data in transport.requests] == ["GetPluginStatus"]
