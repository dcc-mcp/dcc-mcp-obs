from __future__ import annotations

from pathlib import Path

import jsonschema
import yaml
from dcc_mcp_core.skill import skill_success

import dcc_mcp_obs


def main() -> None:
    package_root = Path(dcc_mcp_obs.__file__).resolve().parent
    skill_root = package_root / "skills" / "obs-control"
    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    tools = yaml.safe_load((skill_root / "tools.yaml").read_text(encoding="utf-8"))["tools"]
    identity = {
        "instanceId": "obs-wheel-contract",
        "pluginVersion": dcc_mcp_obs.__version__,
        "obsVersion": "31.1.1",
        "hostPid": 4242,
        "eventSequence": 7,
        "ok": True,
    }
    results = {
        "get_status": {**identity, "ready": True},
        "list_scenes": {
            **identity,
            "currentSceneName": "Main",
            "scenes": [{"sceneName": "Main"}],
            "truncated": False,
        },
        "capture_program_frame": {
            **identity,
            "sourceName": "Main",
            "imageFormat": "png",
            "imageData": "data:image/png;base64," + ("A" * 64),
            "imageWidth": 320,
            "imageHeight": 180,
            "byteLength": 48,
            "sha256": "0" * 64,
        },
        "list_sources": {
            **identity,
            "sceneName": "Main",
            "sources": [
                {
                    "sceneItemId": 1,
                    "sourceName": "Camera",
                    "sourceKind": "video_capture_device",
                    "enabled": True,
                }
            ],
            "truncated": False,
        },
        "get_recording_status": {
            **identity,
            "outputActive": False,
            "outputPaused": False,
        },
        "start_recording": {
            **identity,
            "outputActive": True,
            "outputPaused": False,
            "verified": True,
        },
        "stop_recording": {
            **identity,
            "outputActive": False,
            "outputPaused": False,
            "verified": True,
        },
        "pause_recording": {
            **identity,
            "outputActive": True,
            "outputPaused": True,
            "verified": True,
        },
        "resume_recording": {
            **identity,
            "outputActive": True,
            "outputPaused": False,
            "verified": True,
        },
        "get_streaming_status": {**identity, "streamingActive": False},
        "start_streaming": {**identity, "streamingActive": True, "verified": True},
        "stop_streaming": {**identity, "streamingActive": False, "verified": True},
        "get_replay_buffer_status": {**identity, "replayBufferActive": False},
        "start_replay_buffer": {**identity, "replayBufferActive": True, "verified": True},
        "stop_replay_buffer": {**identity, "replayBufferActive": False, "verified": True},
        "save_replay_buffer": {
            **identity,
            "replayBufferActive": True,
            "accepted": True,
            "submitted": True,
        },
        "get_virtual_camera_status": {**identity, "virtualCameraActive": False},
        "start_virtual_camera": {**identity, "virtualCameraActive": True, "verified": True},
        "stop_virtual_camera": {**identity, "virtualCameraActive": False, "verified": True},
        "list_outputs": {
            **identity,
            "outputs": [
                {"outputName": "streaming", "outputKind": "streaming", "outputActive": False}
            ],
            "truncated": False,
        },
        "get_output_status": {
            **identity,
            "outputName": "streaming",
            "outputKind": "streaming",
            "outputActive": False,
        },
        "start_output": {
            **identity,
            "outputName": "streaming",
            "outputKind": "streaming",
            "outputActive": True,
            "verified": True,
        },
        "stop_output": {
            **identity,
            "outputName": "streaming",
            "outputKind": "streaming",
            "outputActive": False,
            "verified": True,
        },
        "list_profiles": {**identity, "profiles": [{"profileName": "Main"}], "truncated": False},
        "get_current_profile": {**identity, "profileName": "Main"},
        "set_current_profile": {**identity, "profileName": "Main", "verified": True},
        "list_scene_collections": {
            **identity,
            "sceneCollections": [{"sceneCollectionName": "Main"}],
            "truncated": False,
        },
        "get_current_scene_collection": {**identity, "sceneCollectionName": "Main"},
        "set_current_scene_collection": {
            **identity,
            "sceneCollectionName": "Main",
            "verified": True,
        },
        "list_allowlisted_hotkeys": {
            **identity,
            "hotkeys": [{"hotkeyName": "start_streaming"}],
            "truncated": False,
        },
        "trigger_allowlisted_hotkey": {
            **identity,
            "hotkeyName": "start_streaming",
            "accepted": True,
        },
        "get_operator_status": {
            **identity,
            "ready": True,
            "uiThreadReady": True,
            "configPathRedacted": True,
            "profileName": "Main",
            "sceneCollectionName": "Main",
        },
        "get_current_scene": {**identity, "sceneName": "Main"},
        "set_current_scene": {**identity, "sceneName": "Main", "verified": True},
        "list_scene_items": {**identity, "sceneName": "Main", "sceneItems": [], "truncated": False},
        "create_scene_item": {
            **identity,
            "sceneName": "Main",
            "sceneItemId": 1,
            "sourceName": "Camera",
            "sourceKind": "mock",
            "enabled": True,
            "verified": True,
        },
        "get_window_capture_source": {
            **identity,
            "sceneName": "RL - Bazaar",
            "sceneItemId": 7,
            "sourceName": "RL - Bazaar Window",
            "sourceKind": "window_capture",
            "enabled": True,
            "processId": 30520,
            "windowHandle": 147140366,
            "windowTitle": "The Bazaar",
            "windowClass": "UnrealWindow",
            "executable": "Bazaar.exe",
            "captureCursor": False,
            "clientArea": True,
            "captureMethod": "automatic",
            "bindingVerified": True,
            "verified": True,
        },
        "list_window_capture_candidates": {
            **identity,
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
                    "minimized": False,
                    "clientWidth": 1066,
                    "clientHeight": 600,
                    "captureReady": True,
                }
            ],
            "truncated": False,
        },
        "restore_window_capture_candidate": {
            **identity,
            "processId": 120000,
            "windowHandle": 220000000,
            "windowTitle": "b1  ",
            "windowClass": "UnrealWindow",
            "executable": "b1-Win64-Shipping.exe",
            "visible": True,
            "minimized": False,
            "clientWidth": 1066,
            "clientHeight": 600,
            "captureReady": True,
            "verified": True,
        },
        "create_window_capture_source": {
            **identity,
            "sceneName": "RL - Bazaar",
            "sceneItemId": 7,
            "sourceName": "RL - Bazaar Window",
            "sourceKind": "window_capture",
            "enabled": True,
            "processId": 30520,
            "windowHandle": 147140366,
            "windowTitle": "The Bazaar",
            "windowClass": "UnrealWindow",
            "executable": "Bazaar.exe",
            "captureCursor": False,
            "clientArea": True,
            "captureMethod": "automatic",
            "bindingVerified": True,
            "verified": True,
        },
        "rebind_window_capture_source": {
            **identity,
            "sceneName": "RL - Wukong",
            "sceneItemId": 7,
            "sourceName": "RL - Wukong Window",
            "sourceKind": "window_capture",
            "enabled": True,
            "processId": 120000,
            "windowHandle": 220000000,
            "windowTitle": "b1  ",
            "windowClass": "UnrealWindow",
            "executable": "b1-Win64-Shipping.exe",
            "captureCursor": False,
            "clientArea": True,
            "captureMethod": "windows_graphics_capture",
            "bindingVerified": True,
            "verified": True,
        },
        "set_window_capture_method": {
            **identity,
            "sceneName": "RL - Bazaar",
            "sceneItemId": 7,
            "sourceName": "RL - Bazaar Window",
            "sourceKind": "window_capture",
            "enabled": True,
            "processId": 30520,
            "windowHandle": 147140366,
            "windowTitle": "The Bazaar",
            "windowClass": "UnrealWindow",
            "executable": "Bazaar.exe",
            "captureCursor": False,
            "clientArea": True,
            "captureMethod": "windows_graphics_capture",
            "bindingVerified": True,
            "verified": True,
        },
        "set_scene_item_enabled": {
            **identity,
            "sceneName": "Main",
            "sceneItemId": 1,
            "sourceName": "Camera",
            "sourceKind": "mock",
            "enabled": False,
            "verified": True,
        },
        "remove_scene_item": {
            **identity,
            "sceneName": "Main",
            "sceneItemId": 1,
            "removed": True,
            "verified": True,
        },
        "list_transitions": {**identity, "transitions": [], "truncated": False},
        "set_current_transition": {**identity, "transitionName": "Fade", "verified": True},
        "trigger_transition": {
            **identity,
            "transitionName": "Fade",
            "sceneName": "Main",
            "verified": True,
        },
        "get_studio_mode": {
            **identity,
            "studioModeEnabled": False,
            "previewSceneName": "Main",
            "programSceneName": "Main",
        },
        "set_studio_mode": {**identity, "studioModeEnabled": True, "verified": True},
        "set_preview_scene": {**identity, "sceneName": "Main", "verified": True},
        "transition_to_program": {
            **identity,
            "studioModeEnabled": True,
            "previewSceneName": "Main",
            "programSceneName": "Main",
            "verified": True,
        },
        "create_scene": {**identity, "sceneName": "New", "verified": True},
        "rename_scene": {**identity, "sceneName": "New2", "verified": True},
        "remove_scene": {**identity, "sceneName": "Old", "removed": True, "verified": True},
        "set_scene_item_transform": {
            **identity,
            "sceneName": "Main",
            "sceneItemId": 1,
            "sourceName": "Camera",
            "sourceKind": "mock",
            "enabled": True,
            "verified": True,
        },
    }

    assert [tool["name"] for tool in tools] == list(results)
    assert "`postcondition.verified=true`" in skill_text
    for tool in tools:
        envelope = skill_success("OBS action completed.", **results[tool["name"]])
        if tool["name"] == "trigger_allowlisted_hotkey":
            assert "postcondition" not in envelope
        jsonschema.Draft202012Validator(tool["output_schema"]).validate(envelope)


if __name__ == "__main__":
    main()
