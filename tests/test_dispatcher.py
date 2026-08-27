from __future__ import annotations

from collections.abc import Mapping

import pytest

from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge
from dcc_mcp_obs.dispatcher import ObsBridgeDispatcher

IDENTITY = {
    "instanceId": "obs-instance-1",
    "pluginVersion": "0.1.0",
    "obsVersion": "31.1.1",
    "hostPid": 4242,
    "eventSequence": 7,
}


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TimedTransport:
    def __init__(self, clock: ManualClock, advances: list[float]) -> None:
        self.clock = clock
        self.advances = list(advances)
        self.requests = 0
        self.deadlines: list[float | None] = []

    def vendor_request(
        self,
        _request_type: str,
        _data: Mapping[str, object],
        *,
        deadline: float | None = None,
    ) -> dict[str, object]:
        self.requests += 1
        self.deadlines.append(deadline)
        self.clock.advance(self.advances.pop(0))
        return {**IDENTITY, "ready": True, "eventSequence": 6 + self.requests}


def test_dispatcher_carries_one_absolute_deadline_to_every_bridge_request() -> None:
    clock = ManualClock()
    transport = TimedTransport(clock, [0, 5])
    dispatcher = ObsBridgeDispatcher(clock=clock)

    result = dispatcher.dispatch_callable(
        lambda: ObsControlBridge(transport, expected_pid=4242, clock=clock).status(),
        timeout_hint_secs=5,
    )

    assert result["ready"] is True
    assert transport.deadlines == [5, 5]


def test_dispatcher_total_deadline_rejects_over_budget_result() -> None:
    clock = ManualClock()
    transport = TimedTransport(clock, [0, 5.001])
    dispatcher = ObsBridgeDispatcher(clock=clock)

    with pytest.raises(BridgeError, match="OBS_TIMEOUT"):
        dispatcher.dispatch_callable(
            lambda: ObsControlBridge(transport, expected_pid=4242, clock=clock).status(),
            timeout_hint_secs=5,
        )

    assert transport.requests == 2
