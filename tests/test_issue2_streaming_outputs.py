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
        "start_streaming",
        "stop_streaming",
        "start_replay_buffer",
        "stop_replay_buffer",
        "save_replay_buffer",
        "start_virtual_camera",
        "stop_virtual_camera",
        "start_output",
        "stop_output",
    ):
        assert by_name[name]["annotations"]["destructive_hint"] is True
        assert by_name[name]["output_schema"]["additionalProperties"] is False
        assert "postcondition" in by_name[name]["output_schema"]["required"]
    json.dumps(by_name)
    jsonschema.Draft202012Validator.check_schema(by_name["get_streaming_status"]["output_schema"])
