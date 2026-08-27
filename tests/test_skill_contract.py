from __future__ import annotations

import json
from pathlib import Path

import yaml

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

    for alias in (
        "OBS",
        "Open Broadcaster Software",
        "录屏",
        "录制视频",
        "直播",
        "场景切换",
        "streaming",
        "recording",
    ):
        assert alias.casefold() in skill.casefold()

    assert "raw_request" not in serialized
    assert "execute_script" not in serialized
    assert all(
        tool["annotations"]["read_only_hint"]
        for tool in tools["tools"]
        if tool["name"].startswith("get_") or tool["name"].startswith("list_")
    )
    assert all("call_examples" in tool for tool in tools["tools"])
    assert all("next-tools" in tool for tool in tools["tools"])


def test_ui_fallback_is_explicitly_scoped_to_dcc_cua() -> None:
    skill = (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "DCC-CUA" in skill
    assert "PID" in skill and "HWND" in skill
    assert "fresh snapshot" in skill
    assert "post-action readback" in skill
    assert "generic Computer Use" in skill
