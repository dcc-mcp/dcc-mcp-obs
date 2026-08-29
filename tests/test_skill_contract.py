from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml
from dcc_mcp_core.skill import skill_success

from dcc_mcp_obs import __version__

ROOT = Path(__file__).parents[1]


def test_obs_skill_has_bilingual_discovery_aliases_and_no_raw_escape_hatch() -> None:
    skill = (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    tools = yaml.safe_load(
        (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "tools.yaml").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(tools, ensure_ascii=False)

    frontmatter = yaml.safe_load(skill.split("---", 2)[1])
    discovery = json.dumps(frontmatter, ensure_ascii=False)

    for alias in (
        "OBS",
        "Open Broadcaster Software",
        "录屏",
        "录制视频",
        "recording",
        "scenes",
        "sources",
        "pause",
        "resume",
    ):
        assert alias.casefold() in discovery.casefold()

    for unsupported in ("scene switching", "场景切换"):
        assert unsupported.casefold() not in discovery.casefold()

    assert [tool["name"] for tool in tools["tools"]] == [
        "get_status",
        "list_scenes",
        "list_sources",
        "get_recording_status",
        "start_recording",
        "stop_recording",
        "pause_recording",
        "resume_recording",
        "get_streaming_status",
        "start_streaming",
        "stop_streaming",
        "get_replay_buffer_status",
        "start_replay_buffer",
        "stop_replay_buffer",
        "save_replay_buffer",
        "get_virtual_camera_status",
        "start_virtual_camera",
        "stop_virtual_camera",
        "list_outputs",
        "get_output_status",
        "start_output",
        "stop_output",
        "list_profiles",
        "get_current_profile",
        "set_current_profile",
        "list_scene_collections",
        "get_current_scene_collection",
        "set_current_scene_collection",
        "list_allowlisted_hotkeys",
        "trigger_allowlisted_hotkey",
        "capture_source_screenshot",
        "get_operator_status",
    ]

    assert "raw_request" not in serialized
    assert "execute_script" not in serialized
    assert all(
        tool["annotations"]["read_only_hint"]
        for tool in tools["tools"]
        if tool["name"].startswith("get_") or tool["name"].startswith("list_")
    )
    assert all("call_examples" in tool for tool in tools["tools"])
    assert all("next-tools" in tool for tool in tools["tools"][:8])


def test_ui_fallback_is_explicitly_scoped_to_dcc_cua() -> None:
    skill = (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "DCC-CUA" in skill
    assert "PID" in skill and "HWND" in skill
    assert "fresh snapshot" in skill
    assert "post-action readback" in skill
    assert "generic Computer Use" in skill


def test_skill_outputs_publish_strict_typed_envelopes_with_context_parity() -> None:
    tools = yaml.safe_load(
        (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "tools.yaml").read_text(
            encoding="utf-8"
        )
    )["tools"]

    expected_context_keys = {
        "get_status": {
            "instanceId",
            "pluginVersion",
            "obsVersion",
            "hostPid",
            "eventSequence",
            "ok",
            "ready",
        },
        "list_scenes": {
            "instanceId",
            "pluginVersion",
            "obsVersion",
            "hostPid",
            "eventSequence",
            "ok",
            "currentSceneName",
            "scenes",
            "truncated",
        },
        "list_sources": {
            "instanceId",
            "pluginVersion",
            "obsVersion",
            "hostPid",
            "eventSequence",
            "ok",
            "sceneName",
            "sources",
            "truncated",
        },
        "get_recording_status": {
            "instanceId",
            "pluginVersion",
            "obsVersion",
            "hostPid",
            "eventSequence",
            "ok",
            "outputActive",
            "outputPaused",
        },
    }
    mutation_context_keys = {
        "instanceId",
        "pluginVersion",
        "obsVersion",
        "hostPid",
        "eventSequence",
        "ok",
        "outputActive",
        "outputPaused",
    }
    mutation_names = {
        "start_recording",
        "stop_recording",
        "pause_recording",
        "resume_recording",
    }
    expected_context_keys.update(
        {
            "get_streaming_status": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"streamingActive"},
            "start_streaming": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"streamingActive"},
            "stop_streaming": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"streamingActive"},
            "get_replay_buffer_status": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"replayBufferActive"},
            "start_replay_buffer": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"replayBufferActive"},
            "stop_replay_buffer": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"replayBufferActive"},
            "save_replay_buffer": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"replayBufferActive", "accepted", "submitted"},
            "get_virtual_camera_status": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"virtualCameraActive"},
            "start_virtual_camera": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"virtualCameraActive"},
            "stop_virtual_camera": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"virtualCameraActive"},
            "list_outputs": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"outputs", "truncated"},
            "get_output_status": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"outputName", "outputKind", "outputActive"},
            "start_output": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"outputName", "outputKind", "outputActive"},
            "stop_output": mutation_context_keys - {"outputActive", "outputPaused"}
            | {"outputName", "outputKind", "outputActive"},
        }
    )
    mutation_names.update(
        {
            "start_streaming",
            "stop_streaming",
            "start_replay_buffer",
            "stop_replay_buffer",
            "start_virtual_camera",
            "stop_virtual_camera",
            "start_output",
            "stop_output",
            "set_current_profile",
            "set_current_scene_collection",
        }
    )
    for name in {"start_recording", "stop_recording", "pause_recording", "resume_recording"}:
        expected_context_keys[name] = mutation_context_keys

    expected_context_keys.update(
        {
            "list_profiles": set(
                identity
                for identity in (
                    "instanceId",
                    "pluginVersion",
                    "obsVersion",
                    "hostPid",
                    "eventSequence",
                    "ok",
                    "profiles",
                    "currentProfileName",
                    "truncated",
                )
            ),
            "get_current_profile": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "profileName",
            },
            "set_current_profile": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "profileName",
            },
            "list_scene_collections": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "sceneCollections",
                "currentSceneCollectionName",
                "truncated",
            },
            "get_current_scene_collection": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "sceneCollectionName",
            },
            "set_current_scene_collection": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "sceneCollectionName",
            },
            "list_allowlisted_hotkeys": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "hotkeys",
                "truncated",
            },
            "trigger_allowlisted_hotkey": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "hotkeyName",
                "accepted",
            },
            "capture_source_screenshot": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "accepted",
                "screenshotId",
                "imageFormat",
                "pathRedacted",
                "width",
                "height",
            },
            "get_operator_status": {
                "instanceId",
                "pluginVersion",
                "obsVersion",
                "hostPid",
                "eventSequence",
                "ok",
                "ready",
                "uiThreadReady",
                "configPathRedacted",
                "profileName",
                "sceneCollectionName",
                "configVersion",
            },
        }
    )

    for tool in tools:
        schema = tool["output_schema"]
        assert schema["additionalProperties"] is False
        required = {"success", "message", "error", "prompt", "context"}
        if tool["name"] in mutation_names:
            required.add("postcondition")
            assert schema["properties"]["postcondition"] == {
                "type": "object",
                "additionalProperties": False,
                "required": ["verified"],
                "properties": {"verified": {"const": True}},
            }
        assert set(schema["required"]) == required
        assert set(schema["properties"]) == required
        context = schema["properties"]["context"]
        assert context["additionalProperties"] is False
        assert set(context["properties"]) == expected_context_keys[tool["name"]]


def test_every_skill_output_schema_accepts_the_real_core_success_envelope() -> None:
    tools = yaml.safe_load(
        (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "tools.yaml").read_text(
            encoding="utf-8"
        )
    )["tools"]
    identity = {
        "instanceId": "obs-contract",
        "pluginVersion": __version__,
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
    }
    results.update(
        {
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
            "list_profiles": {
                **identity,
                "profiles": [{"profileName": "Main"}],
                "truncated": False,
            },
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
            "capture_source_screenshot": {
                **identity,
                "accepted": True,
                "screenshotId": "shot-1",
                "imageFormat": "png",
                "pathRedacted": True,
            },
            "get_operator_status": {
                **identity,
                "ready": True,
                "uiThreadReady": True,
                "configPathRedacted": True,
                "profileName": "Main",
                "sceneCollectionName": "Main",
            },
        }
    )

    failures: dict[str, list[str]] = {}
    for tool in tools:
        envelope = skill_success("OBS action completed.", **results[tool["name"]])
        errors = sorted(
            error.message
            for error in jsonschema.Draft202012Validator(tool["output_schema"]).iter_errors(
                envelope
            )
        )
        if errors:
            failures[tool["name"]] = errors

    assert failures == {}


def test_public_discovery_docs_distinguish_delivered_tools_from_roadmap() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "docs" / "zh" / "README.md").read_text(encoding="utf-8")
    llms = (ROOT / "llms.txt").read_text(encoding="utf-8")

    assert "typed tools" in readme
    assert "Scene switching remains" in readme
    assert "类型化工具" in chinese
    assert "场景切换仍属于路线图" in chinese
    assert "Scene switching remains" in llms
