from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from dcc_mcp_obs import __version__
from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge

IDENTITY = {
    "instanceId": "issue-2",
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


@pytest.mark.parametrize(
    ("method", "status_request", "field"),
    [
        ("start_streaming", "GetStreamingStatus", "streamingActive"),
        ("start_replay_buffer", "GetReplayBufferStatus", "replayBufferActive"),
        ("start_virtual_camera", "GetVirtualCameraStatus", "virtualCameraActive"),
    ],
)
def test_issue2_mutations_reconcile_delayed_typed_readback(method, status_request, field):
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 2},
            {**IDENTITY, field: False, "eventSequence": 3},
            {**IDENTITY, field: True, "eventSequence": 4},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234, postcondition_attempts=2)

    result = getattr(bridge, method)()

    assert result[field] is True
    assert result["verified"] is True
    expected_mutation = {
        "start_streaming": "StartStreaming",
        "start_replay_buffer": "StartReplayBuffer",
        "start_virtual_camera": "StartVirtualCamera",
    }[method]
    assert [name for name, _data in transport.requests] == [
        "GetPluginStatus",
        expected_mutation,
        status_request,
        status_request,
    ]


def test_issue2_output_mutation_binds_exact_name_and_reconciles():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 2},
            {
                **IDENTITY,
                "outputName": "streaming",
                "outputKind": "streaming",
                "outputActive": True,
                "eventSequence": 3,
            },
        ]
    )
    result = ObsControlBridge(transport, expected_pid=1234).start_output(output_name="streaming")
    assert result["outputName"] == "streaming"
    assert result["verified"] is True
    assert transport.requests[1][1] == {"outputName": "streaming"}


def test_issue2_parallel_scene_recordings_bind_exact_scenes_and_reconcile():
    started_at = "2026-09-01T07:15:30+08:00"
    output_root = Path.cwd() / "recordings"
    recordings = [
        {
            "sceneName": "RL - Vampire Survivors",
            "fileNamePrefix": "Vampire Survivors",
            "fileName": "Vampire Survivors 2026-09-01 07-15-30.mp4",
        },
        {
            "sceneName": "RL - The Bazaar",
            "fileNamePrefix": "The Bazaar",
            "fileName": "The Bazaar 2026-09-01 07-15-30.mp4",
        },
        {
            "sceneName": "RL - Black Myth Wukong",
            "fileNamePrefix": "Black Myth Wukong",
            "fileName": "Black Myth Wukong 2026-09-01 07-15-30.mp4",
        },
    ]
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "sessionId": "session-1", "eventSequence": 2},
            {
                **IDENTITY,
                "sessionId": "session-1",
                "sessionActive": True,
                "startedAt": started_at,
                "recordings": [
                    {
                        "sceneName": item["sceneName"],
                        "fileName": item["fileName"],
                        "outputPath": str(output_root / item["fileName"]),
                        "outputActive": True,
                        "videoOnly": True,
                        "videoWidth": 1280,
                        "videoHeight": 720,
                        "totalBytes": 4096,
                        "totalFrames": 120,
                        "lastError": "",
                    }
                    for item in recordings
                ],
                "eventSequence": 3,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=1234)

    result = bridge.start_scene_recordings(
        recordings=[
            {
                "scene_name": item["sceneName"],
                "file_name_prefix": item["fileNamePrefix"],
            }
            for item in recordings
        ]
    )

    assert result["sessionActive"] is True
    assert result["verified"] is True
    assert {(item["videoWidth"], item["videoHeight"]) for item in result["recordings"]} == {
        (1280, 720)
    }
    assert {item["fileName"] for item in result["recordings"]} == {
        item["fileName"] for item in recordings
    }
    assert [name for name, _data in transport.requests] == [
        "GetPluginStatus",
        "StartSceneRecordings",
        "GetSceneRecordingSession",
    ]
    assert transport.requests[1][1] == {
        "recordings": [
            {
                "sceneName": item["sceneName"],
                "fileNamePrefix": item["fileNamePrefix"],
            }
            for item in recordings
        ]
    }
    assert transport.requests[2][1] == {"sessionId": "session-1"}


def test_issue2_parallel_scene_recordings_stop_all_outputs_and_reconcile():
    file_name = "The Bazaar 2026-09-01 07-15-30.mp4"
    output_path = str(Path.cwd() / "recordings" / file_name)
    stopped = {
        **IDENTITY,
        "sessionId": "session-1",
        "sessionActive": False,
        "startedAt": "2026-09-01T07:15:30+08:00",
        "recordings": [
            {
                "sceneName": "RL - The Bazaar",
                "fileName": file_name,
                "outputPath": output_path,
                "outputActive": False,
                "videoOnly": True,
                "videoWidth": 1280,
                "videoHeight": 720,
                "totalBytes": 8192,
                "totalFrames": 240,
                "lastError": "",
            }
        ],
        "eventSequence": 3,
    }
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "sessionId": "session-1", "eventSequence": 2},
            stopped,
        ]
    )

    result = ObsControlBridge(transport, expected_pid=1234).stop_scene_recordings(
        session_id="session-1"
    )

    assert result["sessionActive"] is False
    assert result["verified"] is True
    assert result["recordings"][0]["outputActive"] is False
    assert [name for name, _data in transport.requests] == [
        "GetPluginStatus",
        "StopSceneRecordings",
        "GetSceneRecordingSession",
    ]


@pytest.mark.parametrize(
    "recordings",
    [
        [],
        [
            {"scene_name": "RL - Game", "file_name_prefix": "Game"},
            {"scene_name": "RL - Game", "file_name_prefix": "Game 2"},
        ],
        [{"scene_name": "RL - Game", "file_name_prefix": "../escape"}],
    ],
)
def test_issue2_parallel_scene_recordings_reject_invalid_or_ambiguous_plans(recordings):
    bridge = ObsControlBridge(FakeTransport([{**IDENTITY, "ready": True}]), expected_pid=1234)

    with pytest.raises(BridgeError, match="OBS_ARGUMENT_INVALID"):
        bridge.start_scene_recordings(recordings=recordings)


def test_issue2_parallel_scene_recordings_reject_path_filename_mismatch():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "sessionId": "session-1",
                "sessionActive": True,
                "startedAt": "2026-09-01T07:15:30+08:00",
                "recordings": [
                    {
                        "sceneName": "RL - The Bazaar",
                        "fileName": "The Bazaar 2026-09-01 07-15-30.mp4",
                        "outputPath": str(Path.cwd() / "recordings" / "different.mp4"),
                        "outputActive": True,
                        "videoOnly": True,
                        "videoWidth": 1280,
                        "videoHeight": 720,
                        "totalBytes": 0,
                        "totalFrames": 0,
                        "lastError": "",
                    }
                ],
                "eventSequence": 2,
            },
        ]
    )

    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        ObsControlBridge(transport, expected_pid=1234).scene_recording_status(
            session_id="session-1"
        )


@pytest.mark.parametrize(
    ("readback_name", "readback_kind"),
    [("recording", "recording"), ("streaming", "recording")],
)
def test_issue2_output_mutation_rejects_identity_mismatch(readback_name, readback_kind):
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 2},
            {
                **IDENTITY,
                "outputName": readback_name,
                "outputKind": readback_kind,
                "outputActive": True,
                "eventSequence": 3,
            },
        ]
    )

    with pytest.raises(BridgeError, match="OBS_POSTCONDITION_FAILED"):
        ObsControlBridge(transport, expected_pid=1234, postcondition_attempts=1).start_output(
            output_name="streaming"
        )


def test_issue2_replay_save_is_submitted_without_false_verified_readback():
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 2},
            {**IDENTITY, "replayBufferActive": True, "eventSequence": 3},
        ]
    )

    result = ObsControlBridge(transport, expected_pid=1234).save_replay_buffer()

    assert result["accepted"] is True
    assert result["submitted"] is True
    assert "verified" not in result


def test_issue2_rejects_unsupported_obs_version_before_use():
    with pytest.raises(BridgeError, match="OBS_VERSION_UNSUPPORTED"):
        ObsControlBridge(
            FakeTransport([{**IDENTITY, "ready": True, "obsVersion": "27.2.4"}]),
            expected_pid=1234,
        )


def test_issue2_skill_contract_marks_dangerous_operations_and_strict_schemas():
    root = Path(__file__).parents[1]
    tools = yaml.safe_load(
        (root / "src/dcc_mcp_obs/skills/obs-control/tools.yaml").read_text(encoding="utf-8")
    )["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    for name in (
        "start_scene_recordings",
        "stop_scene_recordings",
        "start_streaming",
        "stop_streaming",
        "start_replay_buffer",
        "stop_replay_buffer",
        "start_virtual_camera",
        "stop_virtual_camera",
        "start_output",
        "stop_output",
    ):
        assert by_name[name]["annotations"]["destructive_hint"] is True
        assert by_name[name]["output_schema"]["additionalProperties"] is False
        assert "postcondition" in by_name[name]["output_schema"]["required"]
    assert by_name["get_scene_recording_session"]["annotations"]["read_only_hint"] is True
    recording_item = by_name["get_scene_recording_session"]["output_schema"]["properties"][
        "context"
    ]["properties"]["recordings"]["items"]
    assert {"videoWidth", "videoHeight", "outputPath", "videoOnly"} <= set(
        recording_item["required"]
    )
    save_context = by_name["save_replay_buffer"]["output_schema"]["properties"]["context"]
    assert {"accepted", "submitted"}.issubset(save_context["required"])
    json.dumps(by_name)
    jsonschema.Draft202012Validator.check_schema(by_name["get_streaming_status"]["output_schema"])
