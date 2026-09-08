from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_native_recording_contract_classifies_terminal_artifacts() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")

    assert "config_set_string(config, section, key, directory.toUtf8().constData())" in source
    assert 'output_path + ".stalled"' in source
    assert 'obs_data_set_string(result, "outputState", "stalled")' in source
    assert 'obs_data_set_string(result, "outputState", "empty")' in source
    assert "g_recording_stop_requested" in source


def test_skill_contract_exposes_path_and_bounded_finalization() -> None:
    tools = yaml.safe_load(
        (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "tools.yaml").read_text(
            encoding="utf-8"
        )
    )["tools"]
    by_name = {tool["name"]: tool for tool in tools}

    assert by_name["start_recording"]["input_schema"]["properties"]["output_directory"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 4096,
    }
    assert by_name["stop_recording"]["timeout_hint_secs"] == 120
    states = by_name["get_recording_status"]["output_schema"]["properties"]["context"][
        "properties"
    ]["outputState"]["enum"]
    assert {"complete", "stalled", "empty", "failed", "missing"} <= set(states)
