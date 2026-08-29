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
        "save_replay_buffer": {**identity, "replayBufferActive": True, "verified": True},
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
    }

    assert [tool["name"] for tool in tools] == list(results)
    assert "`postcondition.verified=true`" in skill_text
    for tool in tools:
        envelope = skill_success("OBS action completed.", **results[tool["name"]])
        jsonschema.Draft202012Validator(tool["output_schema"]).validate(envelope)


if __name__ == "__main__":
    main()
