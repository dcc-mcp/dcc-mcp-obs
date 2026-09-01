from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from typing import Any

import pytest
from dcc_mcp_core import HostExecutionBridge

from dcc_mcp_obs import __version__
from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge
from dcc_mcp_obs.dispatcher import ObsBridgeDispatcher


class FakeTransport:
    def __init__(
        self,
        responses: list[dict[str, object]],
        *,
        clock: ManualClock | None = None,
        advances: list[float] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, object], float | None]] = []
        self.clock = clock
        self.advances = list(advances or [])

    def vendor_request(
        self,
        request_type: str,
        data: Mapping[str, object],
        *,
        deadline: float | None = None,
    ) -> dict[str, object]:
        self.requests.append((request_type, dict(data), deadline))
        if self.clock is not None and self.advances:
            self.clock.advance(self.advances.pop(0))
        return self.responses.pop(0)


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)


IDENTITY = {
    "instanceId": "obs-instance-1",
    "pluginVersion": __version__,
    "obsVersion": "31.1.1",
    "hostPid": 4242,
    "eventSequence": 7,
    "ok": True,
}


def test_recording_status_accepts_bounded_output_diagnostics() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "outputActive": True,
                "outputPaused": False,
                "outputName": "simple_file_output",
                "outputKind": "mp4_output",
                "outputPath": "C:/Videos/session.mp4",
                "totalBytes": 4_224_797_993,
                "totalFrames": 161_602,
                "lastError": "",
                "eventSequence": 9,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    result = bridge.recording_status()

    assert result["outputPath"] == "C:/Videos/session.mp4"
    assert result["totalBytes"] == 4_224_797_993
    assert result["totalFrames"] == 161_602
    assert result["lastError"] == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outputPath", "x" * 4097),
        ("lastError", "x" * 4097),
        ("totalBytes", -1),
        ("totalFrames", True),
    ],
)
def test_recording_status_rejects_invalid_output_diagnostics(field: str, value: object) -> None:
    response = {
        **IDENTITY,
        "outputActive": True,
        "outputPaused": False,
        "outputName": "simple_file_output",
        "outputKind": "mp4_output",
        "outputPath": "C:/Videos/session.mp4",
        "totalBytes": 1024,
        "totalFrames": 30,
        "lastError": "",
        "eventSequence": 9,
    }
    response[field] = value
    transport = FakeTransport([{**IDENTITY, "ready": True}, response])
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        bridge.recording_status()


def test_recording_start_requires_separate_verified_readback() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 8},
            {**IDENTITY, "outputActive": True, "outputPaused": False, "eventSequence": 9},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    result = bridge.start_recording()

    assert result["verified"] is True
    assert result["outputActive"] is True
    assert [request for request, _data, _deadline in transport.requests] == [
        "GetPluginStatus",
        "StartRecording",
        "GetRecordingStatus",
    ]


def test_graceful_shutdown_is_a_typed_terminal_submission() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "accepted": True,
                "shutdownScheduled": True,
                "eventSequence": 8,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    result = bridge.request_graceful_shutdown()

    assert result["accepted"] is True
    assert result["shutdownScheduled"] is True
    assert transport.requests[1][0] == "RequestGracefulShutdown"
    assert transport.requests[1][1] == {"capability": "application_lifecycle"}


def test_create_agent_input_overlay_requires_separate_scene_bound_readback() -> None:
    inactive = {
        **IDENTITY,
        "sceneName": "RL - Game 1",
        "sceneItemId": 42,
        "sourceName": "DCC-MCP Agent Input",
        "sourceKind": "dcc_mcp_agent_input_overlay",
        "theme": "dcc_mcp_dark",
        "anchor": "bottom_right",
        "opacity": 78,
        "margin": 48,
        "agentId": "agent",
        "active": False,
        "activitySequence": 0,
        "eventKind": "none",
        "keysCsv": "",
        "mouseButton": "none",
        "wheelDirection": "none",
        "characterCount": 0,
        "cueLabel": "",
        "durationMs": 0,
        "remainingMs": 0,
    }
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "currentSceneName": "RL - Game 1",
                "scenes": [{"sceneName": "RL - Game 1"}],
                "truncated": False,
                "eventSequence": 8,
            },
            {**IDENTITY, "accepted": True, "eventSequence": 9},
            {**inactive, "eventSequence": 10},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    result = bridge.create_agent_input_overlay(scene_name="RL - Game 1")

    assert result["verified"] is True
    assert result["anchor"] == "bottom_right"
    assert [request for request, _data, _deadline in transport.requests] == [
        "GetPluginStatus",
        "ListScenes",
        "CreateAgentInputOverlay",
        "GetAgentInputOverlay",
    ]
    assert transport.requests[2][1] == {
        "sceneName": "RL - Game 1",
        "sourceName": "DCC-MCP Agent Input",
        "anchor": "bottom_right",
        "capability": "agent_input_overlay",
    }


def test_set_agent_input_overlay_layout_verifies_safe_anchor_opacity_and_margin() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 8},
            {
                **IDENTITY,
                "sceneName": "RL - Game 1",
                "sceneItemId": 42,
                "sourceName": "DCC-MCP Agent Input - trainer-1",
                "sourceKind": "dcc_mcp_agent_input_overlay",
                "theme": "dcc_mcp_dark",
                "anchor": "top_left",
                "opacity": 55,
                "margin": 32,
                "agentId": "trainer-1",
                "active": False,
                "activitySequence": 0,
                "eventKind": "none",
                "keysCsv": "",
                "mouseButton": "none",
                "wheelDirection": "none",
                "characterCount": 0,
                "cueLabel": "",
                "durationMs": 0,
                "remainingMs": 0,
                "eventSequence": 9,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    result = bridge.set_agent_input_overlay_layout(
        scene_name="RL - Game 1",
        source_name="DCC-MCP Agent Input - trainer-1",
        anchor="top_left",
        opacity=55,
        margin=32,
    )

    assert result["verified"] is True
    assert result["anchor"] == "top_left"
    assert result["opacity"] == 55
    assert result["margin"] == 32
    assert transport.requests[1][0] == "SetAgentInputOverlayLayout"
    assert transport.requests[1][1] == {
        "sceneName": "RL - Game 1",
        "sourceName": "DCC-MCP Agent Input - trainer-1",
        "anchor": "top_left",
        "opacity": 55,
        "margin": 32,
        "capability": "agent_input_overlay",
    }


@pytest.mark.parametrize(
    "layout",
    [
        {"anchor": "center", "opacity": 55, "margin": 32},
        {"anchor": "top_left", "opacity": 19, "margin": 32},
        {"anchor": "top_left", "opacity": 101, "margin": 32},
        {"anchor": "top_left", "opacity": 55, "margin": 7},
        {"anchor": "top_left", "opacity": 55, "margin": 161},
    ],
)
def test_agent_input_overlay_layout_rejects_unsafe_values(layout: dict[str, object]) -> None:
    bridge = ObsControlBridge(FakeTransport([{**IDENTITY, "ready": True}]), expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_ARGUMENT_INVALID"):
        bridge.set_agent_input_overlay_layout(
            scene_name="RL - Game 1",
            source_name="DCC-MCP Agent Input - trainer-1",
            **layout,
        )


def test_emit_agent_shortcut_uses_semantic_keys_and_exact_activity_readback() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "accepted": True,
                "activitySequence": 7,
                "eventSequence": 8,
            },
            {
                **IDENTITY,
                "sceneName": "RL - Game 1",
                "sceneItemId": 42,
                "sourceName": "DCC-MCP Agent Input",
                "sourceKind": "dcc_mcp_agent_input_overlay",
                "theme": "dcc_mcp_dark",
                "anchor": "bottom_right",
                "opacity": 78,
                "margin": 48,
                "agentId": "trainer-1",
                "active": True,
                "activitySequence": 7,
                "eventKind": "shortcut",
                "keysCsv": "ctrl,shift,r",
                "mouseButton": "none",
                "wheelDirection": "none",
                "characterCount": 0,
                "cueLabel": "CTRL + SHIFT + R",
                "durationMs": 1600,
                "remainingMs": 1598,
                "eventSequence": 9,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    result = bridge.emit_agent_input_activity(
        scene_name="RL - Game 1",
        event_kind="shortcut",
        keys=["ctrl", "shift", "r"],
        agent_id="trainer-1",
    )

    assert result["verified"] is True
    assert result["cueLabel"] == "CTRL + SHIFT + R"
    assert transport.requests[1][1] == {
        "sceneName": "RL - Game 1",
        "sourceName": "DCC-MCP Agent Input",
        "eventKind": "shortcut",
        "keysCsv": "ctrl,shift,r",
        "mouseButton": "none",
        "wheelDirection": "none",
        "characterCount": 0,
        "durationMs": 1600,
        "agentId": "trainer-1",
        "capability": "agent_input_overlay",
    }


@pytest.mark.parametrize(
    ("arguments", "field", "wrong_value"),
    [
        ({"event_kind": "mouse_button", "mouse_button": "left"}, "mouseButton", "right"),
        ({"event_kind": "mouse_wheel", "wheel_direction": "up"}, "wheelDirection", "down"),
        ({"event_kind": "typing", "character_count": 12}, "characterCount", 11),
    ],
)
def test_agent_input_activity_requires_exact_semantic_readback(
    arguments: dict[str, object], field: str, wrong_value: object
) -> None:
    event_kind = str(arguments["event_kind"])
    response = {
        **IDENTITY,
        "sceneName": "RL - Game 1",
        "sceneItemId": 42,
        "sourceName": "DCC-MCP Agent Input",
        "sourceKind": "dcc_mcp_agent_input_overlay",
        "theme": "dcc_mcp_dark",
        "anchor": "bottom_right",
        "opacity": 78,
        "margin": 48,
        "agentId": "agent",
        "active": True,
        "activitySequence": 7,
        "eventKind": event_kind,
        "keysCsv": "",
        "mouseButton": arguments.get("mouse_button", "none"),
        "wheelDirection": arguments.get("wheel_direction", "none"),
        "characterCount": arguments.get("character_count", 0),
        "cueLabel": "bounded semantic cue",
        "durationMs": 1600,
        "remainingMs": 1598,
        "eventSequence": 9,
    }
    response[field] = wrong_value
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "activitySequence": 7, "eventSequence": 8},
            response,
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_POSTCONDITION_FAILED"):
        bridge.emit_agent_input_activity(scene_name="RL - Game 1", **arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        {"event_kind": "shortcut", "keys": ["ctrl", "secret"]},
        {"event_kind": "shortcut", "keys": []},
        {"event_kind": "mouse_button", "mouse_button": "none"},
        {"event_kind": "mouse_wheel", "wheel_direction": "none"},
        {"event_kind": "typing", "character_count": 0},
    ],
)
def test_agent_input_activity_rejects_nonsemantic_or_empty_events(
    arguments: dict[str, object],
) -> None:
    transport = FakeTransport([{**IDENTITY, "ready": True}])
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_ARGUMENT_INVALID"):
        bridge.emit_agent_input_activity(scene_name="RL - Game 1", **arguments)

    assert [request for request, _data, _deadline in transport.requests] == ["GetPluginStatus"]


def test_mutation_fails_closed_when_readback_does_not_prove_postcondition() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 8},
            {**IDENTITY, "outputActive": False, "outputPaused": False, "eventSequence": 9},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242, postcondition_attempts=1)

    with pytest.raises(BridgeError, match="OBS_POSTCONDITION_FAILED"):
        bridge.start_recording()


def test_recording_mutation_reconciles_bounded_delayed_postcondition() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 8},
            {**IDENTITY, "outputActive": False, "outputPaused": False, "eventSequence": 9},
            {**IDENTITY, "outputActive": True, "outputPaused": False, "eventSequence": 10},
        ]
    )
    bridge = ObsControlBridge(
        transport,
        expected_pid=4242,
        postcondition_attempts=2,
        postcondition_poll_seconds=0,
    )

    result = bridge.start_recording()

    assert result["verified"] is True
    assert [request for request, _data, _deadline in transport.requests].count(
        "GetRecordingStatus"
    ) == 2


def test_recording_stop_allows_bounded_obs_finalization_delay() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 8},
            *[
                {
                    **IDENTITY,
                    "outputActive": True,
                    "outputPaused": False,
                    "eventSequence": 9 + offset,
                }
                for offset in range(24)
            ],
            {**IDENTITY, "outputActive": False, "outputPaused": False, "eventSequence": 33},
        ]
    )
    bridge = ObsControlBridge(
        transport,
        expected_pid=4242,
        postcondition_poll_seconds=0,
    )

    result = bridge.stop_recording()

    assert result["verified"] is True
    assert result["outputActive"] is False
    assert [request for request, _data, _deadline in transport.requests].count(
        "GetRecordingStatus"
    ) == 25


def test_cross_instance_drift_fails_before_following_call() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "instanceId": "obs-instance-2", "scenes": []},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_INSTANCE_DRIFT"):
        bridge.list_scenes()


def test_server_scoped_instance_mismatch_fails_during_initial_binding() -> None:
    transport = FakeTransport([{**IDENTITY, "ready": True}])

    with pytest.raises(BridgeError, match="OBS_INSTANCE_NOT_READY"):
        ObsControlBridge(
            transport,
            expected_pid=4242,
            expected_instance_id="different-server-instance",
        )


def test_transport_failure_is_stable_and_redacts_password() -> None:
    class FailingTransport:
        def vendor_request(
            self,
            request_type: str,
            data: Mapping[str, object],
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            del request_type, data, deadline
            raise RuntimeError("connect failed with PRIVATE_OBS_PASSWORD")

    with pytest.raises(BridgeError) as caught:
        ObsControlBridge(FailingTransport(), expected_pid=4242)

    assert caught.value.code == "OBS_CONNECTION_FAILED"
    assert "PRIVATE_OBS_PASSWORD" not in str(caught.value)


@pytest.mark.parametrize(
    ("downstream", "public"),
    [
        ("OBS_UI_TIMEOUT", "OBS_UI_TIMEOUT"),
        ("OBS_RECORDING_NOT_ACTIVE", "OBS_RECORDING_NOT_ACTIVE"),
        ("OBS_PRIVATE_PASSWORD_leak", "OBS_CONNECTION_FAILED"),
    ],
)
def test_downstream_exception_codes_are_allowlisted(downstream: str, public: str) -> None:
    class FailingTransport:
        def vendor_request(
            self,
            request_type: str,
            data: Mapping[str, object],
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            del request_type, data, deadline
            error = RuntimeError("private downstream detail")
            error.code = downstream  # type: ignore[attr-defined]
            raise error

    with pytest.raises(BridgeError) as caught:
        ObsControlBridge(FailingTransport(), expected_pid=4242)

    assert caught.value.code == public
    assert downstream not in str(caught.value) or downstream == public


def test_transport_raised_private_bridge_error_is_redacted() -> None:
    private_code = "OBS_PRIVATE_PASSWORD_LEAK"

    class FailingTransport:
        def vendor_request(
            self,
            request_type: str,
            data: Mapping[str, object],
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            del request_type, data, deadline
            raise BridgeError(private_code)

    with pytest.raises(BridgeError) as caught:
        ObsControlBridge(FailingTransport(), expected_pid=4242)

    assert caught.value.code == "OBS_CONNECTION_FAILED"
    assert private_code not in str(caught.value)


@pytest.mark.parametrize(
    "downstream",
    [
        pytest.param(RuntimeError("PRIVATE_OBS_PASSWORD"), id="private-message"),
        pytest.param(BridgeError("OBS_PRIVATE_PASSWORD_LEAK"), id="private-code"),
    ],
)
def test_private_transport_details_do_not_escape_real_core_public_boundary(
    caplog, downstream: BaseException
) -> None:
    private_message = "PRIVATE_OBS_PASSWORD"
    private_code = "OBS_PRIVATE_PASSWORD_LEAK"

    class FailingTransport:
        def vendor_request(
            self,
            request_type: str,
            data: Mapping[str, object],
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            del request_type, data, deadline
            raise downstream

    execution_bridge = HostExecutionBridge(
        dispatcher=ObsBridgeDispatcher(),
        default_thread_affinity="any",
        default_execution="sync",
        default_timeout_hint_secs=30,
    )

    with caplog.at_level(logging.ERROR, logger="dcc_mcp_core._server.inprocess_executor"):
        envelope = execution_bridge.dispatch_callable(
            lambda: ObsControlBridge(FailingTransport(), expected_pid=4242),
            action_name="obs_control__get_status",
            skill_name="obs-control",
        )

    public_payload = json.dumps(envelope, sort_keys=True)
    assert "OBS_CONNECTION_FAILED" in public_payload
    for private_value in (private_message, private_code):
        assert private_value not in public_payload
        assert private_value not in caplog.text


@pytest.mark.parametrize("raw_code", [["OBS_UI_TIMEOUT"], {"code": "OBS_UI_TIMEOUT"}])
def test_unhashable_transport_error_code_is_redacted(raw_code: object) -> None:
    class FailingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def vendor_request(
            self,
            request_type: str,
            data: Mapping[str, object],
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            del request_type, data, deadline
            self.calls += 1
            if self.calls == 1:
                return {**IDENTITY, "ready": True}
            error = RuntimeError("private downstream detail")
            error.code = raw_code  # type: ignore[attr-defined]
            raise error

    bridge = ObsControlBridge(FailingTransport(), expected_pid=4242)

    with pytest.raises(BridgeError) as caught:
        bridge.list_scenes()

    assert caught.value.code == "OBS_CONNECTION_FAILED"


def test_unknown_vendor_error_code_is_redacted() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "ok": False, "errorCode": "OBS_PRIVATE_PATH_C_USERS", "eventSequence": 8},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError) as caught:
        bridge.list_scenes()

    assert caught.value.code == "OBS_REQUEST_FAILED"
    assert "PRIVATE_PATH" not in str(caught.value)


@pytest.mark.parametrize("raw_code", [["OBS_UI_TIMEOUT"], {"code": "OBS_UI_TIMEOUT"}])
def test_unhashable_vendor_error_code_is_redacted(raw_code: object) -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "ok": False, "errorCode": raw_code, "eventSequence": 8},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError) as caught:
        bridge.list_scenes()

    assert caught.value.code == "OBS_REQUEST_FAILED"


def test_incompatible_native_plugin_version_is_not_ready() -> None:
    status = {**IDENTITY, "pluginVersion": "999.0.0", "ready": True}

    with pytest.raises(BridgeError, match="OBS_PLUGIN_VERSION_UNSUPPORTED"):
        ObsControlBridge(FakeTransport([status]), expected_pid=4242)


@pytest.mark.parametrize(
    "response",
    [
        {**IDENTITY, "scenes": "not-an-array", "truncated": False},
        {**IDENTITY, "scenes": [], "truncated": "false"},
        {**IDENTITY, "scenes": [{"sceneName": 7}], "truncated": False},
        {**IDENTITY, "scenes": [], "truncated": False, "privateExtra": "leak"},
    ],
)
def test_list_scenes_response_has_strict_typed_schema(response: dict[str, object]) -> None:
    transport = FakeTransport([{**IDENTITY, "ready": True}, {**response, "eventSequence": 8}])
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        bridge.list_scenes()


@pytest.mark.parametrize(
    ("request_type", "response"),
    [
        ("GetPluginStatus", {**IDENTITY, "ready": True, "eventSequence": 8}),
        ("ListScenes", {**IDENTITY, "scenes": [], "truncated": False, "eventSequence": 8}),
        (
            "ListSources",
            {
                **IDENTITY,
                "sceneName": "Main",
                "sources": [],
                "truncated": False,
                "eventSequence": 8,
            },
        ),
        (
            "GetRecordingStatus",
            {
                **IDENTITY,
                "outputActive": False,
                "outputPaused": False,
                "eventSequence": 8,
            },
        ),
    ],
)
def test_read_only_success_requires_explicit_ok_true(
    request_type: str, response: dict[str, object]
) -> None:
    response.pop("ok")
    transport = FakeTransport([{**IDENTITY, "ready": True}, response])
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        bridge._checked(request_type, deadline=bridge._operation_deadline())


def test_event_sequence_regression_fails_closed_before_verified_readback() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True, "eventSequence": 100},
            {**IDENTITY, "accepted": True, "eventSequence": 1},
            {
                **IDENTITY,
                "outputActive": True,
                "outputPaused": False,
                "eventSequence": 0,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_EVENT_SEQUENCE_INVALID"):
        bridge.start_recording()

    assert len(transport.requests) == 2


def test_read_only_response_must_strictly_advance_event_sequence() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "scenes": []},
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242)

    with pytest.raises(BridgeError, match="OBS_EVENT_SEQUENCE_INVALID"):
        bridge.list_scenes()


def test_mutation_poll_must_strictly_advance_event_sequence() -> None:
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 8},
            {
                **IDENTITY,
                "outputActive": False,
                "outputPaused": False,
                "eventSequence": 8,
            },
        ]
    )
    bridge = ObsControlBridge(transport, expected_pid=4242, postcondition_attempts=2)

    with pytest.raises(BridgeError, match="OBS_EVENT_SEQUENCE_INVALID"):
        bridge.start_recording()

    assert len(transport.requests) == 3


def test_concurrent_responses_commit_event_sequence_in_request_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConcurrentTransport:
        def __init__(self) -> None:
            self.sequence = 7
            self.lock = threading.Lock()

        def vendor_request(
            self,
            request_type: str,
            data: Mapping[str, object],
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            del data, deadline
            with self.lock:
                if request_type == "GetPluginStatus" and self.sequence == 7:
                    return {**IDENTITY, "ready": True}
                self.sequence += 1
                return {
                    **IDENTITY,
                    "eventSequence": self.sequence,
                    "scenes": [],
                    "truncated": False,
                }

    transport = ConcurrentTransport()
    bridge = ObsControlBridge(transport, expected_pid=4242)
    first_in_validation = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    errors: list[BaseException] = []
    original_parse_identity = bridge._parse_identity

    def delayed_first_identity(response: Mapping[str, object]) -> object:
        if response.get("eventSequence") == 8:
            first_in_validation.set()
            assert release_first.wait(2)
        return original_parse_identity(response)

    monkeypatch.setattr(bridge, "_parse_identity", delayed_first_identity)

    def call(*, second: bool = False) -> None:
        try:
            bridge.list_scenes()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            if second:
                second_finished.set()

    first = threading.Thread(target=call)
    first.start()
    assert first_in_validation.wait(2)
    second = threading.Thread(target=call, kwargs={"second": True})
    second.start()
    second_finished.wait(0.1)
    release_first.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert transport.sequence == 9


@pytest.mark.parametrize("invalid", [None, True, -1, 1.5, "1"])
def test_event_sequence_shape_is_required(invalid: Any) -> None:
    response = {**IDENTITY, "ready": True, "eventSequence": invalid}

    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        ObsControlBridge(FakeTransport([response]), expected_pid=4242)


def test_one_mutation_deadline_stops_before_another_status_request() -> None:
    clock = ManualClock()
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {**IDENTITY, "accepted": True, "eventSequence": 8},
            {**IDENTITY, "outputActive": False, "outputPaused": False, "eventSequence": 9},
        ],
        clock=clock,
        advances=[0, 0, 4],
    )
    bridge = ObsControlBridge(
        transport,
        expected_pid=4242,
        deadline=5,
        clock=clock,
        sleeper=clock.sleep,
        postcondition_attempts=2,
        postcondition_poll_seconds=1,
    )

    with pytest.raises(BridgeError, match="OBS_TIMEOUT"):
        bridge.start_recording()

    assert len(transport.requests) == 3
    assert {deadline for _request, _data, deadline in transport.requests} == {5}
