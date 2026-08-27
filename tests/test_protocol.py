from __future__ import annotations

import base64
import hashlib
import json

import pytest

from dcc_mcp_obs.config import ObsEndpointConfig
from dcc_mcp_obs.protocol import ObsWebSocketTransport, ProtocolError


class ScriptedSocket:
    def __init__(self, incoming: list[dict[str, object] | str | bytes]) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict[str, object]] = []
        self.closed = False

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
    socket = ScriptedSocket([_hello(), {"op": 2, "d": {}}, response])

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


def test_oversized_or_malformed_frame_is_stably_rejected() -> None:
    socket = ScriptedSocket([b"x" * 1_048_577])
    transport = ObsWebSocketTransport(
        ObsEndpointConfig(password="secret"), connector=lambda _url, _timeout: socket
    )

    with pytest.raises(ProtocolError, match="OBS_FRAME_TOO_LARGE"):
        transport.vendor_request("GetPluginStatus", {})
