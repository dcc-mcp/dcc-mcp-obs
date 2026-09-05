from __future__ import annotations

from pathlib import Path

import pytest

from dcc_mcp_obs import __version__
from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge


class FakeHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.scene = "Main"
        self.preview = "Main"
        self.transition = "Fade"
        self.transition_duration = 300
        self.items: dict[int, dict[str, object]] = {}

    def vendor_request(
        self, request_type: str, data: dict[str, object], *, deadline: float | None = None
    ):
        self.calls.append((request_type, dict(data)))
        identity = {
            "instanceId": "instance-1",
            "pluginVersion": __version__,
            "obsVersion": "30.0.0",
            "hostPid": 42,
            "eventSequence": len(self.calls),
            "ok": True,
        }
        if request_type == "GetPluginStatus":
            return {**identity, "ready": True}
        if request_type == "ListScenes":
            return {
                **identity,
                "scenes": [{"sceneName": "Main"}, {"sceneName": "Alt"}],
                "currentSceneName": self.scene,
                "truncated": False,
            }
        if request_type == "SetCurrentScene":
            self.scene = str(data["sceneName"])
            return {**identity, "accepted": True, "sceneName": self.scene}
        if request_type == "GetCurrentScene":
            return {**identity, "sceneName": self.scene}
        if request_type == "ListTransitions":
            return {
                **identity,
                "transitions": [
                    {"transitionName": "Cut", "transitionKind": "cut_transition"},
                    {"transitionName": "Fade", "transitionKind": "fade_transition"},
                ],
                "currentTransitionName": self.transition,
                "truncated": False,
            }
        if request_type == "SetCurrentTransition":
            self.transition = str(data["transitionName"])
            if "durationMs" in data:
                self.transition_duration = int(data["durationMs"])
            return {
                **identity,
                "transitionName": self.transition,
                "durationMs": self.transition_duration,
                "accepted": True,
            }
        if request_type == "GetCurrentTransition":
            return {
                **identity,
                "transitionName": self.transition,
                "durationMs": self.transition_duration,
            }
        if request_type == "CreateSceneItem":
            item_id = int(data.get("sceneItemId", 7))
            self.items[item_id] = {
                "sceneItemId": item_id,
                "sceneName": data["sceneName"],
                "sourceName": data["sourceName"],
                "sourceKind": data.get("sourceKind", "mock"),
                "enabled": True,
            }
            return {**identity, "accepted": True, **self.items[item_id]}
        if request_type == "GetSceneItem":
            item = self.items.get(int(data["sceneItemId"]))
            return {
                **identity,
                **(
                    item
                    or {
                        "sceneItemId": int(data["sceneItemId"]),
                        "sceneName": data["sceneName"],
                        "exists": False,
                        "removed": True,
                    }
                ),
            }
        if request_type == "RemoveSceneItem":
            self.items.pop(int(data["sceneItemId"]), None)
            return {
                **identity,
                "accepted": True,
                "sceneItemId": int(data["sceneItemId"]),
                "sceneName": data["sceneName"],
            }
        if request_type == "SetCurrentPreviewScene":
            self.preview = str(data["sceneName"])
            return {**identity, "accepted": True, "sceneName": self.preview}
        if request_type == "GetCurrentPreviewScene":
            return {**identity, "sceneName": self.preview}
        if request_type == "GetStudioModeStatus":
            return {
                **identity,
                "studioModeEnabled": True,
                "previewSceneName": self.preview,
                "programSceneName": self.scene,
            }
        if request_type == "TriggerStudioModeTransition":
            self.scene, self.preview = self.preview, self.scene
            return {**identity, "accepted": True}
        raise AssertionError(request_type)


def make_bridge(host: FakeHost) -> ObsControlBridge:
    return ObsControlBridge(host, expected_pid=42, postcondition_attempts=1)


def test_typed_scene_switch_is_exact_and_read_back() -> None:
    host = FakeHost()
    bridge = make_bridge(host)
    result = bridge.set_current_scene("Alt")
    assert result["sceneName"] == "Alt"
    assert result["verified"] is True
    assert host.calls[-2][0:1] == ("SetCurrentScene",)


def test_scene_item_crud_is_instance_bound_and_read_back() -> None:
    host = FakeHost()
    bridge = make_bridge(host)
    created = bridge.create_scene_item(scene_name="Main", source_name="Camera", source_kind="mock")
    assert created["verified"] is True
    removed = bridge.remove_scene_item(scene_name="Main", scene_item_id=7)
    assert removed["verified"] is True


def test_studio_preview_program_and_transition_are_typed() -> None:
    host = FakeHost()
    bridge = make_bridge(host)
    assert bridge.set_current_preview_scene("Alt")["verified"] is True
    status = bridge.get_studio_mode_status()
    assert status["previewSceneName"] == "Alt"
    assert bridge.trigger_studio_mode_transition()["verified"] is True


def test_transition_duration_requires_exact_readback() -> None:
    host = FakeHost()
    original = host.vendor_request

    def ignore_duration(request_type, data, *, deadline=None):
        response = original(request_type, data, deadline=deadline)
        if request_type == "SetCurrentTransition":
            host.transition_duration = 300
        return response

    host.vendor_request = ignore_duration  # type: ignore[method-assign]

    with pytest.raises(BridgeError, match="OBS_POSTCONDITION_FAILED"):
        make_bridge(host).set_current_transition("Cut", duration_ms=100)


def test_studio_transition_rejects_accepted_noop() -> None:
    host = FakeHost()
    original = host.vendor_request

    def noop(request_type, data, *, deadline=None):
        before_scene, before_preview = host.scene, host.preview
        response = original(request_type, data, deadline=deadline)
        if request_type == "TriggerStudioModeTransition":
            host.scene, host.preview = before_scene, before_preview
        return response

    host.vendor_request = noop  # type: ignore[method-assign]
    bridge = make_bridge(host)
    bridge.set_current_preview_scene("Alt")
    with pytest.raises(BridgeError, match="OBS_POSTCONDITION_FAILED"):
        bridge.trigger_studio_mode_transition()


def test_studio_transition_accepts_obs_non_swap_preview_behavior() -> None:
    host = FakeHost()
    original = host.vendor_request

    def no_swap(request_type, data, *, deadline=None):
        if request_type == "TriggerStudioModeTransition":
            host.scene = host.preview
            host.calls.append((request_type, dict(data)))
            return {
                "instanceId": "instance-1",
                "pluginVersion": __version__,
                "obsVersion": "30.0.0",
                "hostPid": 42,
                "eventSequence": len(host.calls),
                "ok": True,
                "accepted": True,
            }
        return original(request_type, data, deadline=deadline)

    host.vendor_request = no_swap  # type: ignore[method-assign]
    bridge = make_bridge(host)
    bridge.set_current_preview_scene("Alt")

    result = bridge.trigger_studio_mode_transition()

    assert result["programSceneName"] == "Alt"
    assert result["previewSceneName"] == "Alt"
    assert result["verified"] is True


def test_scene_item_mutations_carry_capability_scope() -> None:
    host = FakeHost()
    bridge = make_bridge(host)
    bridge.create_scene_item(scene_name="Main", source_name="Camera", source_kind="mock")
    request_type, payload = host.calls[-2]
    assert request_type == "CreateSceneItem"
    assert payload["capability"] == "scene_graph"


def test_scene_mutation_rejects_unverified_readback() -> None:
    host = FakeHost()
    original = host.vendor_request

    def bad(request_type, data, *, deadline=None):
        response = original(request_type, data, deadline=deadline)
        if request_type == "GetCurrentScene":
            response["sceneName"] = "Other"
        return response

    host.vendor_request = bad  # type: ignore[method-assign]
    with pytest.raises(BridgeError, match="OBS_POSTCONDITION_FAILED"):
        make_bridge(host).set_current_scene("Alt")


def test_native_transition_selection_uses_frontend_transition_inventory() -> None:
    source = (Path(__file__).parents[1] / "native" / "src" / "plugin-main.cpp").read_text(
        encoding="utf-8"
    )
    selection = source.split("case UiOperation::SetCurrentTransition:", 1)[1].split(
        "case UiOperation::TriggerTransition:", 1
    )[0]
    readback = source.split("case UiOperation::GetCurrentTransition:", 1)[1].split(
        "case UiOperation::SetCurrentTransition:", 1
    )[0]

    assert "lookup_frontend_transition" in selection
    assert "obs_get_source_by_name" not in selection
    assert "obs_frontend_get_current_transition" not in selection
    assert 'obs_data_set_int(result, "durationMs"' in readback
