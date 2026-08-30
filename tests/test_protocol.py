from __future__ import annotations

import base64
import hashlib
import json

import pytest

from dcc_mcp_obs.config import ObsEndpointConfig
from dcc_mcp_obs.protocol import (
    MAX_FRAME_BYTES,
    VENDOR_REQUESTS,
    ObsWebSocketTransport,
    ProtocolError,
)


class ScriptedSocket:
    def __init__(self, incoming: list[dict[str, object] | str | bytes]) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def recv(self) -> str | bytes:
        raw = self.incoming.pop(0)
        return raw if isinstance(raw, (str, bytes)) else json.dumps(raw)

    def send(self, payload: str) -> None:
        value = json.loads(payload)
        if value.get("op") == 6:
            response = self.incoming[-1]
            assert isinstance(response, dict)
            response["d"]["requestId"] = value["d"]["requestId"]
        self.sent.append(value)

    def close(self) -> None:
        self.closed = True


def _hello() -> dict[str, object]:
    return {
        "op": 0,
        "d": {
            "obsWebSocketVersion": "5.6.2",
            "rpcVersion": 1,
            "authentication": {"challenge": "challenge", "salt": "salt"},
        },
    }


def _response() -> dict[str, object]:
    return {
        "op": 7,
        "d": {
            "requestType": "CallVendorRequest",
            "requestId": "filled-by-socket",
            "requestStatus": {"result": True, "code": 100},
            "responseData": {"responseData": {"instanceId": "one", "ready": True}},
        },
    }


def test_authenticates_and_uses_only_call_vendor_request() -> None:
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()])
    config = ObsEndpointConfig(password="PRIVATE_OBS_PASSWORD")
    transport = ObsWebSocketTransport(config, connector=lambda _url, _timeout: socket)

    result = transport.vendor_request("GetPluginStatus", {})

    secret = base64.b64encode(hashlib.sha256(b"PRIVATE_OBS_PASSWORDsalt").digest()).decode("ascii")
    expected_auth = base64.b64encode(
        hashlib.sha256((secret + "challenge").encode()).digest()
    ).decode("ascii")
    assert socket.sent[0] == {
        "op": 1,
        "d": {"rpcVersion": 1, "eventSubscriptions": 0, "authentication": expected_auth},
    }
    assert socket.sent[1]["d"]["requestType"] == "CallVendorRequest"
    assert socket.sent[1]["d"]["requestData"]["vendorName"] == "dcc-mcp-obs"
    assert result == {"instanceId": "one", "ready": True}
    assert "PRIVATE_OBS_PASSWORD" not in json.dumps(socket.sent)


def test_vendor_request_carries_bounded_deadline_metadata_to_native() -> None:
    clock = ManualClock()
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket, clock=clock
    )
    transport.vendor_request("StartStreaming", {}, deadline=5)
    request_data = socket.sent[1]["d"]["requestData"]["requestData"]
    assert isinstance(request_data["__dccDeadlineAtMs"], int)


def test_absolute_deadline_epoch_uses_ceil_and_stale_deadline_fails_closed(monkeypatch) -> None:
    clock = ManualClock()
    monkeypatch.setattr("dcc_mcp_obs.protocol.time.time", lambda: 1000.25)
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket, clock=clock
    )
    transport.vendor_request("GetPluginStatus", {}, deadline=1.001)
    request_data = socket.sent[1]["d"]["requestData"]["requestData"]
    assert request_data["__dccDeadlineAtMs"] == 1001251

    with pytest.raises(ProtocolError, match="OBS_TIMEOUT"):
        transport.vendor_request("StartRecording", {}, deadline=clock.now)


def test_post_send_mutation_timeout_is_indeterminate() -> None:
    clock = ManualClock()

    class DelayedSendSocket(ScriptedSocket):
        def send(self, payload: str) -> None:
            value = json.loads(payload)
            if value.get("op") == 6:
                clock.now += 0.01
            super().send(payload)

    socket = DelayedSendSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket, clock=clock
    )

    with pytest.raises(ProtocolError, match="OBS_UI_INDETERMINATE"):
        transport.vendor_request("StartRecording", {}, deadline=0.005)


def test_mutation_frame_validation_failure_before_send_preserves_frame_error() -> None:
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_FRAME_TOO_LARGE"):
        transport.vendor_request("StartRecording", {"padding": "x" * MAX_FRAME_BYTES})

    assert len(socket.sent) == 1  # identify only; the mutation frame was never attempted


def test_mutation_settimeout_failure_before_send_preserves_connection_error() -> None:
    class FailingRequestTimeoutSocket(ScriptedSocket):
        def settimeout(self, timeout: float) -> None:
            if len(self.sent) == 1:
                raise OSError("settimeout failed")
            super().settimeout(timeout)

    socket = FailingRequestTimeoutSocket(
        [_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()]
    )
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_CONNECTION_FAILED"):
        transport.vendor_request("StartRecording", {})


def test_late_mutation_response_is_indeterminate() -> None:
    clock = ManualClock()
    socket = TimedSocket(
        [_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()],
        clock,
        [0, 0, 0.01],
    )
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket, clock=clock
    )

    with pytest.raises(ProtocolError, match="OBS_UI_INDETERMINATE"):
        transport.vendor_request("SetCurrentProfile", {"profileName": "Main"}, deadline=0.005)


@pytest.mark.parametrize("transport_error", [BrokenPipeError, ConnectionResetError])
def test_post_send_connection_errors_are_indeterminate_for_mutations(transport_error) -> None:
    class FailingSendSocket(ScriptedSocket):
        def send(self, payload: str) -> None:
            value = json.loads(payload)
            if value.get("op") == 6:
                raise transport_error("send failed")
            super().send(payload)

    socket = FailingSendSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_UI_INDETERMINATE"):
        transport.vendor_request("StartStreaming", {})


def test_post_send_malformed_response_is_indeterminate_for_mutation() -> None:
    malformed = {"op": 7, "d": {"requestType": "CallVendorRequest"}}
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, malformed])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_UI_INDETERMINATE"):
        transport.vendor_request("SetCurrentSceneCollection", {"sceneCollectionName": "Main"})


def test_rejects_hello_without_authentication_before_identify() -> None:
    hello = _hello()
    del hello["d"]["authentication"]
    socket = ScriptedSocket([hello])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_AUTHENTICATION_REQUIRED"):
        transport.vendor_request("GetPluginStatus", {})

    assert socket.sent == []
    assert socket.closed is True


def test_unknown_vendor_request_is_rejected_locally() -> None:
    connected = False

    def connect(_url: str, _timeout: float) -> ScriptedSocket:
        nonlocal connected
        connected = True
        return ScriptedSocket([])

    transport = ObsWebSocketTransport(ObsEndpointConfig(password="secret"), connector=connect)

    with pytest.raises(ProtocolError, match="OBS_REQUEST_INVALID"):
        transport.vendor_request("DeleteEverything", {})

    assert connected is False


def test_transport_allowlist_is_exactly_the_typed_public_requests() -> None:
    assert {
        "GetPluginStatus",
        "GetOperatorStatus",
        "ListScenes",
        "GetCurrentScene",
        "SetCurrentScene",
        "CreateScene",
        "RenameScene",
        "RemoveScene",
        "ListSources",
        "ListSceneItems",
        "GetSceneItem",
        "CreateSceneItem",
        "CreateWindowCaptureSource",
        "GetWindowCaptureSource",
        "UpdateSceneItem",
        "RemoveSceneItem",
        "SetSceneItemEnabled",
        "SetSceneItemTransform",
        "ListTransitions",
        "GetCurrentTransition",
        "SetCurrentTransition",
        "TriggerTransition",
        "GetStudioModeStatus",
        "SetStudioMode",
        "GetCurrentPreviewScene",
        "SetCurrentPreviewScene",
        "TriggerStudioModeTransition",
        "GetRecordingStatus",
        "StartRecording",
        "StopRecording",
        "PauseRecording",
        "ResumeRecording",
        "GetStreamingStatus",
        "StartStreaming",
        "StopStreaming",
        "GetReplayBufferStatus",
        "StartReplayBuffer",
        "StopReplayBuffer",
        "SaveReplayBuffer",
        "GetVirtualCameraStatus",
        "StartVirtualCamera",
        "StopVirtualCamera",
        "ListOutputs",
        "GetOutputStatus",
        "StartOutput",
        "StopOutput",
        "ListProfiles",
        "GetCurrentProfile",
        "SetCurrentProfile",
        "ListSceneCollections",
        "GetCurrentSceneCollection",
        "SetCurrentSceneCollection",
        "ListAllowlistedHotkeys",
        "TriggerAllowlistedHotkey",
        "CaptureScreenshot",
        "CaptureSourceScreenshot",
    } == VENDOR_REQUESTS


def test_bounded_events_are_reconciled_before_response() -> None:
    socket = ScriptedSocket(
        [
            _hello(),
            {"op": 2, "d": {"negotiatedRpcVersion": 1}},
            {"op": 5, "d": {"eventType": "VendorEvent"}},
            _response(),
        ]
    )
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    assert transport.vendor_request("GetPluginStatus", {})["ready"] is True


def test_request_id_mismatch_fails_closed_and_disconnects() -> None:
    response = _response()
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, response])

    def break_request_id(payload: str) -> None:
        value = json.loads(payload)
        socket.sent.append(value)
        if value.get("op") == 6:
            response["d"]["requestId"] = "wrong"

    socket.send = break_request_id  # type: ignore[method-assign]
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_RESPONSE_MISMATCH"):
        transport.vendor_request("GetPluginStatus", {})
    assert socket.closed is True


def test_mutating_request_id_mismatch_is_indeterminate_after_send() -> None:
    response = _response()
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, response])

    def break_request_id(payload: str) -> None:
        value = json.loads(payload)
        socket.sent.append(value)
        if value.get("op") == 6:
            response["d"]["requestId"] = "wrong"

    socket.send = break_request_id  # type: ignore[method-assign]
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_UI_INDETERMINATE"):
        transport.vendor_request("StartStreaming", {})


def test_mutating_event_overflow_is_indeterminate_after_send() -> None:
    events = [{"op": 5, "d": {"eventType": "VendorEvent"}} for _ in range(65)]
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, *events])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_UI_INDETERMINATE"):
        transport.vendor_request("StartRecording", {})


def test_mutation_connection_failure_before_send_remains_connection_failed() -> None:
    class FailingConnectSocket(ScriptedSocket):
        def recv(self) -> str | bytes:
            raise ConnectionResetError("handshake failed")

    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"),
        connector=lambda _url, _timeout: FailingConnectSocket([]),
    )

    with pytest.raises(ProtocolError, match="OBS_CONNECTION_FAILED"):
        transport.vendor_request("StartRecording", {})


@pytest.mark.parametrize(
    "identify",
    [
        {"op": 2},
        {"op": 2, "d": {}},
        {"op": 2, "d": {"negotiatedRpcVersion": True}},
        {"op": 2, "d": {"negotiatedRpcVersion": 0}},
        {"op": 2, "d": {"negotiatedRpcVersion": 2}},
    ],
)
def test_identify_requires_negotiated_rpc_version_one(identify) -> None:
    socket = ScriptedSocket([_hello(), identify])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_VERSION_UNSUPPORTED"):
        transport.vendor_request("GetPluginStatus", {})

    assert socket.closed is True


def test_oversized_or_malformed_frame_is_stably_rejected() -> None:
    socket = ScriptedSocket([b"x" * 1_048_577])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_FRAME_TOO_LARGE"):
        transport.vendor_request("GetPluginStatus", {})


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TimedSocket(ScriptedSocket):
    def __init__(
        self,
        incoming: list[dict[str, object] | str | bytes],
        clock: ManualClock,
        advances: list[float],
    ) -> None:
        super().__init__(incoming)
        self.clock = clock
        self.advances = list(advances)

    def recv(self) -> str | bytes:
        self.clock.now += self.advances.pop(0)
        return super().recv()


@pytest.mark.parametrize("response_advance, succeeds", [(5.0, True), (5.001, False)])
def test_protocol_uses_one_deadline_across_handshake_and_response(
    response_advance: float, succeeds: bool
) -> None:
    clock = ManualClock()
    socket = TimedSocket(
        [_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, _response()],
        clock,
        [0, 0, response_advance],
    )
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"),
        connector=lambda _url, _timeout: socket,
        clock=clock,
    )

    if succeeds:
        assert transport.vendor_request("GetPluginStatus", {}, deadline=5)["ready"] is True
    else:
        with pytest.raises(ProtocolError, match="OBS_TIMEOUT"):
            transport.vendor_request("GetPluginStatus", {}, deadline=5)

    assert socket.timeouts
    assert socket.timeouts[-1] <= socket.timeouts[0]


def test_interleaved_events_cannot_refresh_the_receive_deadline() -> None:
    clock = ManualClock()
    events = [{"op": 5, "d": {"eventType": "VendorEvent"}} for _ in range(64)]
    socket = TimedSocket(
        [_hello(), {"op": 2, "d": {"negotiatedRpcVersion": 1}}, *events, _response()],
        clock,
        [0, 0, *([0.11] * 64), 0],
    )
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"),
        connector=lambda _url, _timeout: socket,
        clock=clock,
    )

    with pytest.raises(ProtocolError, match="OBS_TIMEOUT"):
        transport.vendor_request("GetPluginStatus", {}, deadline=1)

    assert len(socket.incoming) > 50
    assert all(
        later <= earlier
        for earlier, later in zip(socket.timeouts, socket.timeouts[1:], strict=False)
    )
