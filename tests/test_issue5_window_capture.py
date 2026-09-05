from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dcc_mcp_obs import __version__
from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge
from dcc_mcp_obs.protocol import MUTATING_VENDOR_REQUESTS, VENDOR_REQUESTS


class FakeWindowCaptureHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.window_title = "The Bazaar"
        self.window_minimized = True

    def candidate(self) -> dict[str, object]:
        return {
            "processId": 120000,
            "windowHandle": 220000000,
            "windowTitle": "b1  ",
            "windowClass": "UnrealWindow",
            "executable": "b1-Win64-Shipping.exe",
            "visible": True,
            "minimized": self.window_minimized,
            "clientWidth": 0 if self.window_minimized else 1066,
            "clientHeight": 0 if self.window_minimized else 600,
            "captureReady": not self.window_minimized,
        }

    def vendor_request(
        self, request_type: str, data: dict[str, object], *, deadline: float | None = None
    ) -> dict[str, object]:
        self.calls.append((request_type, dict(data)))
        identity: dict[str, object] = {
            "instanceId": "instance-1",
            "pluginVersion": __version__,
            "obsVersion": "32.2.1",
            "hostPid": 42,
            "eventSequence": len(self.calls),
            "ok": True,
        }
        if request_type == "GetPluginStatus":
            return {**identity, "ready": True}
        if request_type == "ListWindowCaptureCandidates":
            return {
                **identity,
                "executable": data["executable"],
                "windowTitle": data.get("windowTitle"),
                "candidates": [self.candidate()],
                "truncated": False,
            }
        if request_type == "RestoreWindowCaptureCandidate":
            self.window_minimized = False
            return {
                **identity,
                **self.candidate(),
                "accepted": True,
                "capability": "window_capture",
            }
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
            "captureMethod": data.get("captureMethod", "automatic"),
            "bindingVerified": True,
        }
        if request_type in {
            "CreateWindowCaptureSource",
            "RebindWindowCaptureSource",
            "SetWindowCaptureMethod",
        }:
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
        "captureMethod": "automatic",
        "enabled": True,
        "capability": "window_capture",
    }
    assert host.calls[-1][0] == "GetWindowCaptureSource"


def test_window_capture_method_is_typed_and_can_be_updated_in_place() -> None:
    host = FakeWindowCaptureHost()
    result = make_bridge(host).set_window_capture_method(
        scene_name="RL - Bazaar",
        source_name="RL - Bazaar Window",
        process_id=30520,
        window_handle=147140366,
        window_title="The Bazaar",
        capture_cursor=False,
        client_area=True,
        capture_method="windows_graphics_capture",
    )

    assert result["verified"] is True
    assert result["captureMethod"] == "windows_graphics_capture"
    request_type, payload = host.calls[-2]
    assert request_type == "SetWindowCaptureMethod"
    assert payload["captureMethod"] == "windows_graphics_capture"
    assert host.calls[-1][0] == "GetWindowCaptureSource"


def test_window_capture_source_can_be_atomically_rebound_after_process_restart() -> None:
    host = FakeWindowCaptureHost()
    result = make_bridge(host).rebind_window_capture_source(
        scene_name="RL - Wukong",
        source_name="RL - Wukong Window",
        expected_process_id=117716,
        expected_window_handle=195967510,
        expected_window_title="b1  ",
        process_id=120000,
        window_handle=220000000,
        window_title="The Bazaar",
        capture_cursor=False,
        client_area=True,
        capture_method="windows_graphics_capture",
    )

    assert result["verified"] is True
    assert result["processId"] == 120000
    request_type, payload = host.calls[-2]
    assert request_type == "RebindWindowCaptureSource"
    assert payload == {
        "sceneName": "RL - Wukong",
        "sourceName": "RL - Wukong Window",
        "expectedProcessId": 117716,
        "expectedWindowHandle": 195967510,
        "expectedWindowTitle": "b1  ",
        "processId": 120000,
        "windowHandle": 220000000,
        "windowTitle": "The Bazaar",
        "captureCursor": False,
        "clientArea": True,
        "captureMethod": "windows_graphics_capture",
        "enabled": True,
        "capability": "window_capture",
    }
    assert host.calls[-1][0] == "GetWindowCaptureSource"


def test_window_capture_candidates_are_bounded_by_exact_executable_and_title() -> None:
    host = FakeWindowCaptureHost()
    result = make_bridge(host).list_window_capture_candidates(
        executable="b1-Win64-Shipping.exe",
        window_title="b1  ",
    )

    assert result == {
        "instanceId": "instance-1",
        "pluginVersion": __version__,
        "obsVersion": "32.2.1",
        "hostPid": 42,
        "eventSequence": 2,
        "ok": True,
        "executable": "b1-Win64-Shipping.exe",
        "windowTitle": "b1  ",
        "candidates": [
            {
                "processId": 120000,
                "windowHandle": 220000000,
                "windowTitle": "b1  ",
                "windowClass": "UnrealWindow",
                "executable": "b1-Win64-Shipping.exe",
                "visible": True,
                "minimized": True,
                "clientWidth": 0,
                "clientHeight": 0,
                "captureReady": False,
            }
        ],
        "truncated": False,
    }
    assert host.calls == [
        ("GetPluginStatus", {}),
        (
            "ListWindowCaptureCandidates",
            {"executable": "b1-Win64-Shipping.exe", "windowTitle": "b1  "},
        ),
    ]


def test_minimized_window_capture_candidate_can_be_restored_by_exact_identity() -> None:
    host = FakeWindowCaptureHost()
    result = make_bridge(host).restore_window_capture_candidate(
        executable="b1-Win64-Shipping.exe",
        process_id=120000,
        window_handle=220000000,
        window_title="b1  ",
    )

    assert result["verified"] is True
    assert result["captureReady"] is True
    assert result["minimized"] is False
    assert result["clientWidth"] == 1066
    assert result["clientHeight"] == 600
    assert host.calls == [
        ("GetPluginStatus", {}),
        (
            "RestoreWindowCaptureCandidate",
            {
                "executable": "b1-Win64-Shipping.exe",
                "processId": 120000,
                "windowHandle": 220000000,
                "windowTitle": "b1  ",
                "capability": "window_capture",
            },
        ),
        (
            "ListWindowCaptureCandidates",
            {"executable": "b1-Win64-Shipping.exe", "windowTitle": "b1  "},
        ),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable", "C:/private/game.exe"),
        ("executable", ""),
        ("process_id", True),
        ("process_id", 0),
        ("window_handle", True),
        ("window_handle", 0),
        ("window_title", ""),
    ],
)
def test_window_capture_candidate_restore_rejects_inexact_identity(
    field: str, value: object
) -> None:
    kwargs: dict[str, object] = {
        "executable": "b1-Win64-Shipping.exe",
        "process_id": 120000,
        "window_handle": 220000000,
        "window_title": "b1  ",
    }
    kwargs[field] = value

    with pytest.raises(BridgeError, match="OBS_ARGUMENT_INVALID"):
        make_bridge(FakeWindowCaptureHost()).restore_window_capture_candidate(**kwargs)  # type: ignore[arg-type]


def test_window_capture_candidate_state_fails_closed_on_inconsistent_readback() -> None:
    host = FakeWindowCaptureHost()
    original = host.vendor_request

    def inconsistent(request_type, data, *, deadline=None):
        response = original(request_type, data, deadline=deadline)
        if request_type == "ListWindowCaptureCandidates":
            response["candidates"][0]["captureReady"] = True
        return response

    host.vendor_request = inconsistent  # type: ignore[method-assign]
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        make_bridge(host).list_window_capture_candidates(
            executable="b1-Win64-Shipping.exe",
            window_title="b1  ",
        )


def test_window_capture_rebind_rejects_untyped_native_response_fields() -> None:
    host = FakeWindowCaptureHost()
    original = host.vendor_request

    def leaked(request_type, data, *, deadline=None):
        response = original(request_type, data, deadline=deadline)
        if request_type == "RebindWindowCaptureSource":
            response["previousExecutablePath"] = "C:/private/game.exe"
        return response

    host.vendor_request = leaked  # type: ignore[method-assign]
    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        make_bridge(host).rebind_window_capture_source(
            scene_name="RL - Wukong",
            source_name="RL - Wukong Window",
            expected_process_id=117716,
            expected_window_handle=195967510,
            expected_window_title="b1  ",
            process_id=120000,
            window_handle=220000000,
            window_title="The Bazaar",
        )


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
        ("capture_method", "desktop_duplication"),
        ("capture_method", 2),
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
    assert "ListWindowCaptureCandidates" in VENDOR_REQUESTS
    assert "RestoreWindowCaptureCandidate" in VENDOR_REQUESTS
    assert "RebindWindowCaptureSource" in VENDOR_REQUESTS
    assert "SetWindowCaptureMethod" in VENDOR_REQUESTS
    assert "CreateWindowCaptureSource" in MUTATING_VENDOR_REQUESTS
    assert "RestoreWindowCaptureCandidate" in MUTATING_VENDOR_REQUESTS
    assert "RebindWindowCaptureSource" in MUTATING_VENDOR_REQUESTS
    assert "SetWindowCaptureMethod" in MUTATING_VENDOR_REQUESTS
    assert "GetWindowCaptureSource" not in MUTATING_VENDOR_REQUESTS
    assert "ListWindowCaptureCandidates" not in MUTATING_VENDOR_REQUESTS

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
    assert create["input_schema"]["properties"]["capture_method"]["enum"] == [
        "automatic",
        "bitblt",
        "windows_graphics_capture",
    ]
    assert create["annotations"]["open_world_hint"] is False
    assert "raw" not in str(create).lower()
    update = by_name["set_window_capture_method"]
    assert update["source_file"] == "scripts/set_window_capture_method.py"
    assert update["annotations"]["open_world_hint"] is False
    assert update["annotations"]["idempotent_hint"] is True
    rebind = by_name["rebind_window_capture_source"]
    assert rebind["source_file"] == "scripts/rebind_window_capture_source.py"
    assert set(rebind["input_schema"]["required"]) == {
        "scene_name",
        "source_name",
        "expected_process_id",
        "expected_window_handle",
        "expected_window_title",
        "process_id",
        "window_handle",
        "window_title",
    }
    assert rebind["annotations"] == {
        "read_only_hint": False,
        "destructive_hint": True,
        "idempotent_hint": True,
        "open_world_hint": False,
        "deferred_hint": False,
    }
    candidates = by_name["list_window_capture_candidates"]
    assert candidates["source_file"] == "scripts/list_window_capture_candidates.py"
    assert set(candidates["input_schema"]["required"]) == {"executable"}
    assert candidates["input_schema"]["additionalProperties"] is False
    assert candidates["annotations"] == {
        "read_only_hint": True,
        "destructive_hint": False,
        "idempotent_hint": True,
        "open_world_hint": False,
        "deferred_hint": False,
    }
    restore = by_name["restore_window_capture_candidate"]
    assert restore["source_file"] == "scripts/restore_window_capture_candidate.py"
    assert set(restore["input_schema"]["required"]) == {
        "executable",
        "process_id",
        "window_handle",
        "window_title",
    }
    assert restore["input_schema"]["additionalProperties"] is False
    assert restore["annotations"] == {
        "read_only_hint": False,
        "destructive_hint": True,
        "idempotent_hint": True,
        "open_world_hint": False,
        "deferred_hint": False,
    }


def test_native_window_capture_contract_is_windows_only_and_revalidates_identity() -> None:
    source = (Path(__file__).parents[1] / "native/src/plugin-main.cpp").read_text(encoding="utf-8")
    assert '"CreateWindowCaptureSource"' in source
    assert '"GetWindowCaptureSource"' in source
    assert '"SetWindowCaptureMethod"' in source
    assert '"window_capture"' in source
    assert '"windows_graphics_capture"' in source
    assert "GetWindowThreadProcessId" in source
    assert "GetProcessTimes" in source
    assert "QueryFullProcessImageNameW" in source
    assert "OBS_WINDOW_IDENTITY_DRIFT" in source
    assert "OBS_UNSUPPORTED_PLATFORM" in source
    assert "RawInputSettings" not in source


def test_native_window_rebind_is_transactional_and_owns_the_stored_identity() -> None:
    source = (Path(__file__).parents[1] / "native/src/plugin-main.cpp").read_text(encoding="utf-8")

    assert "case UiOperation::RebindWindowCaptureSource" in source
    assert "state->operation == UiOperation::RebindWindowCaptureSource" in source
    assert 'request == "RebindWindowCaptureSource"' in source
    assert '"expectedProcessId"' in source
    assert '"expectedWindowHandle"' in source
    assert '"expectedWindowTitle"' in source
    assert "stored_window_capture_identity_matches" in source
    assert "obs_data_apply(settings, previous_settings)" in source
    assert "obs_source_update(source, previous_settings)" in source


def test_native_window_candidate_discovery_is_visible_filtered_and_bounded() -> None:
    source = (Path(__file__).parents[1] / "native/src/plugin-main.cpp").read_text(encoding="utf-8")

    assert "case UiOperation::ListWindowCaptureCandidates" in source
    assert 'request == "ListWindowCaptureCandidates"' in source
    assert "EnumWindows" in source
    assert "IsWindowVisible" in source
    assert "kMaxWindowCaptureCandidates" in source
    assert '"candidates"' in source
    assert '"truncated"' in source
    assert "IsIconic" in source
    assert "GetClientRect" in source
    assert '"captureReady"' in source


def test_native_window_candidate_restore_is_exact_and_ui_gated() -> None:
    source = (Path(__file__).parents[1] / "native/src/plugin-main.cpp").read_text(encoding="utf-8")

    assert "case UiOperation::RestoreWindowCaptureCandidate" in source
    assert "state->operation == UiOperation::RestoreWindowCaptureCandidate" in source
    assert 'request == "RestoreWindowCaptureCandidate"' in source
    assert "validate_exact_window" in source
    assert "exact_window_is_still_live" in source
    assert "ShowWindowAsync" in source
    assert "SW_RESTORE" in source
    assert "_stricmp(actual.executable.c_str(), state->window_executable_filter.c_str())" in source
