from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dcc_mcp_obs import __version__
from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge
from dcc_mcp_obs.protocol import MUTATING_VENDOR_REQUESTS, VENDOR_REQUESTS

ROOT = Path(__file__).parents[1]
IDENTITY = {
    "instanceId": "issue-5",
    "pluginVersion": __version__,
    "obsVersion": "32.2.1",
    "hostPid": 1234,
    "eventSequence": 1,
    "ok": True,
}


class FakeTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, object]]] = []

    def vendor_request(
        self,
        request_type: str,
        data: dict[str, object],
        *,
        deadline: float | None = None,
    ) -> dict[str, object]:
        self.requests.append((request_type, dict(data)))
        return self.responses.pop(0)


def _bridge(*responses: dict[str, object]) -> tuple[ObsControlBridge, FakeTransport]:
    transport = FakeTransport([{**IDENTITY, "ready": True}, *responses])
    return ObsControlBridge(transport, expected_pid=1234), transport


def test_issue5_reviewed_input_settings_reconcile_exact_readback() -> None:
    accepted = {**IDENTITY, "accepted": True, "eventSequence": 2}
    readback = {
        **IDENTITY,
        "sourceName": "DCC Safe Color",
        "sourceKind": "color_source_v3",
        "schemaVersion": "1.0",
        "settings": {"width": 1280, "height": 720, "color": 4_278_190_080},
        "eventSequence": 3,
    }
    bridge, transport = _bridge(accepted, readback)

    result = bridge.set_input_settings(
        source_name="DCC Safe Color",
        source_kind="color_source_v3",
        schema_version="1.0",
        settings={"width": 1280, "height": 720, "color": 4_278_190_080},
    )

    assert result == {**readback, "verified": True}
    assert transport.requests[1] == (
        "SetInputSettings",
        {
            "sourceName": "DCC Safe Color",
            "sourceKind": "color_source_v3",
            "schemaVersion": "1.0",
            "settings": {"width": 1280, "height": 720, "color": 4_278_190_080},
            "capability": "inputs",
        },
    )
    assert transport.requests[2][0] == "GetInputSettings"


@pytest.mark.parametrize(
    ("source_kind", "schema_version", "settings", "error"),
    [
        (
            "browser_source",
            "1.0",
            {"url": "https://example.invalid"},
            "OBS_SOURCE_KIND_UNSUPPORTED",
        ),
        ("color_source_v3", "2.0", {"width": 1280}, "OBS_SCHEMA_UNSUPPORTED"),
        ("color_source_v3", "1.0", {"width": 0}, "OBS_ARGUMENT_INVALID"),
        ("color_source_v3", "1.0", {"unknown": 1}, "OBS_ARGUMENT_INVALID"),
    ],
)
def test_issue5_input_settings_fail_closed_before_mutation(
    source_kind: str,
    schema_version: str,
    settings: dict[str, object],
    error: str,
) -> None:
    bridge, transport = _bridge()

    with pytest.raises(BridgeError, match=error):
        bridge.set_input_settings(
            source_name="DCC Safe Color",
            source_kind=source_kind,
            schema_version=schema_version,
            settings=settings,
        )

    assert [name for name, _data in transport.requests] == ["GetPluginStatus"]


def test_issue5_filter_audio_and_media_mutations_use_exact_readback() -> None:
    bridge, transport = _bridge(
        {**IDENTITY, "accepted": True, "eventSequence": 2},
        {
            **IDENTITY,
            "sourceName": "Game Audio",
            "filterName": "Agent Gain",
            "filterKind": "gain_filter",
            "enabled": True,
            "schemaVersion": "1.0",
            "settings": {"db": -3.0},
            "eventSequence": 3,
        },
        {**IDENTITY, "accepted": True, "eventSequence": 4},
        {
            **IDENTITY,
            "sourceName": "Game Audio",
            "volume": 0.5,
            "eventSequence": 5,
        },
        {**IDENTITY, "accepted": True, "eventSequence": 6},
        {
            **IDENTITY,
            "sourceName": "Training Replay",
            "mediaState": "playing",
            "mediaDurationMs": 120_000,
            "mediaCursorMs": 2_000,
            "eventSequence": 7,
        },
    )

    created = bridge.create_filter(
        source_name="Game Audio",
        filter_name="Agent Gain",
        filter_kind="gain_filter",
        schema_version="1.0",
        settings={"db": -3.0},
        enabled=True,
    )
    volume = bridge.set_source_volume(source_name="Game Audio", volume=0.5)
    media = bridge.play_media(source_name="Training Replay")

    assert created["verified"] is True
    assert volume["volume"] == 0.5 and volume["verified"] is True
    assert media["mediaState"] == "playing" and media["verified"] is True
    assert [name for name, _data in transport.requests] == [
        "GetPluginStatus",
        "CreateFilter",
        "GetFilter",
        "SetSourceVolume",
        "GetSourceVolume",
        "PlayMedia",
        "GetMediaStatus",
    ]


def test_issue5_native_numeric_readback_uses_declared_small_tolerances() -> None:
    bridge, _transport = _bridge(
        {**IDENTITY, "accepted": True, "eventSequence": 2},
        {
            **IDENTITY,
            "sourceName": "Game Audio",
            "volume": 0.30000001192092896,
            "eventSequence": 3,
        },
        {**IDENTITY, "accepted": True, "eventSequence": 4},
        {
            **IDENTITY,
            "sourceName": "Training Replay",
            "mediaState": "playing",
            "mediaDurationMs": 120_000,
            "mediaCursorMs": 2_120,
            "eventSequence": 5,
        },
    )

    volume = bridge.set_source_volume(source_name="Game Audio", volume=0.3)
    media = bridge.seek_media(source_name="Training Replay", media_cursor_ms=2_000)

    assert volume["volume"] == 0.30000001192092896 and volume["verified"] is True
    assert media["mediaCursorMs"] == 2_120 and media["verified"] is True


def test_issue5_native_and_protocol_register_every_typed_domain() -> None:
    requests = {
        "GetSourceIdentity",
        "CreateSource",
        "RenameSource",
        "RemoveSource",
        "ListInputKinds",
        "GetInputSettings",
        "SetInputSettings",
        "DescribeProperties",
        "ValidatePropertyValue",
        "SetPropertyValue",
        "ListFilters",
        "GetFilter",
        "CreateFilter",
        "SetFilterEnabled",
        "SetFilterSettings",
        "RemoveFilter",
        "GetSourceVolume",
        "SetSourceVolume",
        "GetSourceMute",
        "SetSourceMute",
        "GetSourceMonitorType",
        "SetSourceMonitorType",
        "GetMediaStatus",
        "PlayMedia",
        "PauseMedia",
        "RestartMedia",
        "StopMedia",
        "SeekMedia",
    }
    mutations = {
        request
        for request in requests
        if request.startswith(
            ("Create", "Rename", "Remove", "Set", "Play", "Pause", "Restart", "Stop", "Seek")
        )
    } - {"GetSourceIdentity"}
    native = (ROOT / "native/src/plugin-main.cpp").read_text(encoding="utf-8")

    assert requests <= VENDOR_REQUESTS
    assert mutations <= MUTATING_VENDOR_REQUESTS
    assert all(f'"{request}"' in native for request in requests)
    assert "RawInputSettings" not in native
    assert "CallVendorRequest" not in native


def test_issue5_skill_and_capability_contracts_are_complete() -> None:
    tools = yaml.safe_load(
        (ROOT / "src/dcc_mcp_obs/skills/obs-control/tools.yaml").read_text(encoding="utf-8")
    )["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    expected = {
        "get_source_identity",
        "create_source",
        "rename_source",
        "remove_source",
        "list_input_kinds",
        "get_input_settings",
        "set_input_settings",
        "describe_properties",
        "validate_property_value",
        "set_property_value",
        "list_filters",
        "get_filter",
        "create_filter",
        "set_filter_enabled",
        "set_filter_settings",
        "remove_filter",
        "get_source_volume",
        "set_source_volume",
        "get_source_mute",
        "set_source_mute",
        "get_source_monitor_type",
        "set_source_monitor_type",
        "get_media_status",
        "play_media",
        "pause_media",
        "restart_media",
        "stop_media",
        "seek_media",
    }
    assert expected <= set(by_name)
    for name in expected:
        schema = by_name[name]["input_schema"]
        assert schema["additionalProperties"] is False
    for name in expected & {
        "create_source",
        "rename_source",
        "remove_source",
        "set_input_settings",
        "set_property_value",
        "create_filter",
        "set_filter_enabled",
        "set_filter_settings",
        "remove_filter",
        "set_source_volume",
        "set_source_mute",
        "set_source_monitor_type",
        "play_media",
        "pause_media",
        "restart_media",
        "stop_media",
        "seek_media",
    }:
        assert by_name[name]["annotations"]["destructive_hint"] is True
        assert "postcondition" in by_name[name]["output_schema"]["required"]

    matrix = json.loads((ROOT / "contracts/obs-capabilities-v1.json").read_text("utf-8"))
    domains = {entry["domain"]: entry for entry in matrix["domains"]}
    for domain in ("sources", "inputs", "properties", "filters", "audio", "media"):
        assert domains[domain]["status"] == "delivered"
        assert domains[domain]["remaining_operations"] == []
