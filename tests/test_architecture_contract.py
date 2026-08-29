from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).parents[1]


def test_native_plugin_registers_bounded_vendor_requests() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")

    assert "obs_websocket_register_vendor" in source
    for request in (
        "GetPluginStatus",
        "ListScenes",
        "ListSources",
        "GetRecordingStatus",
        "StartRecording",
        "StopRecording",
        "PauseRecording",
        "ResumeRecording",
    ):
        assert f'"{request}"' in source

    assert "RawRequest" not in source
    assert "ExecuteScript" not in source


def test_native_plugin_uses_libobs_frontend_lifecycle() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")

    assert "obs_frontend_add_event_callback" in source
    assert "obs_frontend_remove_event_callback" in source
    assert "obs_frontend_recording_start" in source
    assert "obs_frontend_recording_stop" in source
    assert "obs_frontend_recording_pause" in source
    assert "obs_frontend_recording_pause(false)" in source
    assert "obs_queue_task(OBS_TASK_UI" in source


def test_native_plugin_advances_sequence_for_every_completed_request() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    completion = source.split("void execute_ui_operation", maxsplit=1)[1].split(
        "bool run_ui_operation", maxsplit=1
    )[0]

    increment = completion.index("g_event_sequence.fetch_add(1) + 1;")
    identity = completion.index("set_identity(result, response_sequence);")
    publish = completion.index("state->result = result;")
    assert increment < identity < publish


def test_native_error_state_cannot_be_overwritten_by_identity_readback() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")

    identity = source.split("void set_identity", maxsplit=1)[1].split("void set_error", maxsplit=1)[
        0
    ]
    assert 'obs_data_set_bool(data, "ok", true)' not in identity
    assert 'if (!obs_data_has_user_value(result, "ok"))' in source


def test_capability_matrix_freezes_full_product_scope() -> None:
    native_source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    matrix = json.loads(
        (ROOT / "contracts" / "obs-capabilities-v1.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / "contracts" / "obs-capabilities-v1.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(matrix)

    assert matrix["schema_version"] == "1.0"
    domains = {entry["domain"]: entry for entry in matrix["domains"]}
    assert set(domains) == {
        "status",
        "scenes",
        "scene_items",
        "sources",
        "inputs",
        "properties",
        "filters",
        "audio",
        "transitions",
        "studio_mode",
        "profiles",
        "scene_collections",
        "recording",
        "replay_buffer",
        "streaming",
        "virtual_camera",
        "media",
        "outputs",
        "hotkeys",
        "screenshots",
    }
    assert "get_plugin_status" in domains["status"]["delivered_operations"]
    assert "get_operator_status" in domains["status"]["delivered_operations"]
    assert '"GetOperatorStatus"' in native_source
    assert 'if (request == "GetOperatorStatus")' in native_source
    assert "list_scenes" in domains["scenes"]["delivered_operations"]
    assert "list_sources" in domains["sources"]["delivered_operations"]
    assert domains["recording"]["status"] == "delivered"
    assert all(
        entry["tracking_issue"].startswith("https://github.com/dcc-mcp/")
        for entry in domains.values()
    )
    assert all(
        set(entry["dangerous_operations"])
        <= set(entry["delivered_operations"] + entry["remaining_operations"])
        for entry in domains.values()
    )
