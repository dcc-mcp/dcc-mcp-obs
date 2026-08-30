from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge
from dcc_mcp_obs.protocol import MUTATING_VENDOR_REQUESTS, VENDOR_REQUESTS


class FakeWindowCaptureHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.window_title = "The Bazaar"

    def vendor_request(
        self, request_type: str, data: dict[str, object], *, deadline: float | None = None
    ) -> dict[str, object]:
        self.calls.append((request_type, dict(data)))
        identity: dict[str, object] = {
            "instanceId": "instance-1",
            "pluginVersion": "1.1.0",
            "obsVersion": "32.2.1",
            "hostPid": 42,
            "eventSequence": len(self.calls),
            "ok": True,
        }
        if request_type == "GetPluginStatus":
            return {**identity, "ready": True}
        capture = {
            "sceneName": data["sceneName"],
            "sceneItemId": 7,
            "sourceName": data["sourceName"],
            "sourceKind": "window_capture",
            "enabled": data.get("enabled", True),
            "processId": data["processId"],
            "windowHandle": data["windowHandle"],
            "windowTitle": self.window_title,
            "windowClass": "UnrealWindow",
            "executable": "Bazaar.exe",
            "captureCursor": data.get("captureCursor", True),
            "clientArea": data.get("clientArea", True),
            "bindingVerified": True,
        }
        if request_type == "CreateWindowCaptureSource":
            return {**identity, **capture, "accepted": True, "capability": "window_capture"}
        if request_type == "GetWindowCaptureSource":
            return {**identity, **capture}
        raise AssertionError(request_type)


def make_bridge(host: FakeWindowCaptureHost) -> ObsControlBridge:
    return ObsControlBridge(host, expected_pid=42, postcondition_attempts=1)


def test_window_capture_source_is_exactly_bound_and_read_back() -> None:
    host = FakeWindowCaptureHost()
    result = make_bridge(host).create_window_capture_source(
        scene_name="RL - Bazaar",
        source_name="RL - Bazaar Window",
        process_id=30520,
        window_handle=147140366,
        window_title="The Bazaar",
        capture_cursor=False,
        client_area=True,
    )

    assert result["verified"] is True
    assert result["bindingVerified"] is True
    assert result["sourceKind"] == "window_capture"
    request_type, payload = host.calls[-2]
    assert request_type == "CreateWindowCaptureSource"
    assert payload == {
        "sceneName": "RL - Bazaar",
        "sourceName": "RL - Bazaar Window",
        "processId": 30520,
        "windowHandle": 147140366,
        "windowTitle": "The Bazaar",
        "captureCursor": False,
        "clientArea": True,
        "enabled": True,
        "capability": "window_capture",
    }
    assert host.calls[-1][0] == "GetWindowCaptureSource"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("process_id", True),
        ("process_id", 0),
        ("process_id", 2**32),
        ("window_handle", True),
        ("window_handle", 0),
        ("window_handle", 2**63),
        ("window_title", ""),
    ],
)
def test_window_capture_source_rejects_invalid_exact_identity(field: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "scene_name": "RL - Bazaar",
        "source_name": "RL - Bazaar Window",
        "process_id": 30520,
        "window_handle": 147140366,
        "window_title": "The Bazaar",
    }
    kwargs[field] = value
    with pytest.raises(BridgeError, match="OBS_ARGUMENT_INVALID"):
        make_bridge(FakeWindowCaptureHost()).create_window_capture_source(**kwargs)  # type: ignore[arg-type]


def test_window_capture_source_fails_closed_on_window_title_drift() -> None:
    host = FakeWindowCaptureHost()
    original = host.vendor_request

    def drifted(request_type, data, *, deadline=None):
        if request_type == "GetWindowCaptureSource":
            host.window_title = "Different Window"
        return original(request_type, data, deadline=deadline)

    host.vendor_request = drifted  # type: ignore[method-assign]
    with pytest.raises(BridgeError, match="OBS_POSTCONDITION_FAILED"):
        make_bridge(host).create_window_capture_source(
            scene_name="RL - Bazaar",
            source_name="RL - Bazaar Window",
            process_id=30520,
            window_handle=147140366,
            window_title="The Bazaar",
        )


def test_window_capture_readback_rejects_private_or_untyped_fields() -> None:
    host = FakeWindowCaptureHost()
    original = host.vendor_request

    def leaked(request_type, data, *, deadline=None):
        response = original(request_type, data, deadline=deadline)
        if request_type == "GetWindowCaptureSource":
            response["executablePath"] = "C:/private/game.exe"
        return response

    host.vendor_request = leaked  # type: ignore[method-assign]
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        make_bridge(host).get_window_capture_source(
            scene_name="RL - Bazaar",
            source_name="RL - Bazaar Window",
            process_id=30520,
            window_handle=147140366,
            window_title="The Bazaar",
        )


def test_window_capture_vendor_surface_and_skill_are_bounded() -> None:
    assert "CreateWindowCaptureSource" in VENDOR_REQUESTS
    assert "GetWindowCaptureSource" in VENDOR_REQUESTS
    assert "CreateWindowCaptureSource" in MUTATING_VENDOR_REQUESTS
    assert "GetWindowCaptureSource" not in MUTATING_VENDOR_REQUESTS

    root = Path(__file__).parents[1]
    tools = yaml.safe_load(
        (root / "src/dcc_mcp_obs/skills/obs-control/tools.yaml").read_text(encoding="utf-8")
    )["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    create = by_name["create_window_capture_source"]
    assert create["source_file"] == "scripts/create_window_capture_source.py"
    assert create["input_schema"]["additionalProperties"] is False
    assert set(create["input_schema"]["required"]) == {
        "scene_name",
        "source_name",
        "process_id",
        "window_handle",
        "window_title",
    }
    assert create["annotations"]["open_world_hint"] is False
    assert "raw" not in str(create).lower()


def test_native_window_capture_contract_is_windows_only_and_revalidates_identity() -> None:
    source = (Path(__file__).parents[1] / "native/src/plugin-main.cpp").read_text(encoding="utf-8")
    assert '"CreateWindowCaptureSource"' in source
    assert '"GetWindowCaptureSource"' in source
    assert '"window_capture"' in source
    assert "GetWindowThreadProcessId" in source
    assert "GetProcessTimes" in source
    assert "QueryFullProcessImageNameW" in source
    assert "OBS_WINDOW_IDENTITY_DRIFT" in source
    assert "OBS_UNSUPPORTED_PLATFORM" in source
    assert "RawInputSettings" not in source
