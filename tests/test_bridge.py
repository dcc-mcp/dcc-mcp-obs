from __future__ import annotations

from collections.abc import Mapping

import pytest

from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge


class FakeTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, object]]] = []

    def vendor_request(self, request_type: str, data: Mapping[str, object]) -> dict[str, object]:
        self.requests.append((request_type, dict(data)))
        return self.responses.pop(0)


IDENTITY = {
    "instanceId": "obs-instance-1",
    "pluginVersion": "0.1.0",
    "obsVersion": "31.1.1",
    "hostPid": 4242,
    "eventSequence": 7,
}


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
    assert [request for request, _ in transport.requests] == [
        "GetPluginStatus",
        "StartRecording",
        "GetRecordingStatus",
    ]


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
            {**IDENTITY, "outputActive": False, "outputPaused": False, "eventSequence": 8},
            {**IDENTITY, "outputActive": True, "outputPaused": False, "eventSequence": 9},
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
    assert [request for request, _ in transport.requests].count("GetRecordingStatus") == 2


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
            self, request_type: str, data: Mapping[str, object]
        ) -> dict[str, object]:
            del request_type, data
            raise RuntimeError("connect failed with PRIVATE_OBS_PASSWORD")

    with pytest.raises(BridgeError) as caught:
        ObsControlBridge(FailingTransport(), expected_pid=4242)

    assert caught.value.code == "OBS_CONNECTION_FAILED"
    assert "PRIVATE_OBS_PASSWORD" not in str(caught.value)
