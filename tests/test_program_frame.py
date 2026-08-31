from __future__ import annotations

import base64
import binascii
import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest
import yaml

from dcc_mcp_obs import __version__
from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge

IDENTITY = {
    "instanceId": "program-frame",
    "pluginVersion": __version__,
    "obsVersion": "32.2.1",
    "hostPid": 1234,
    "eventSequence": 1,
    "ok": True,
}
ROOT = Path(__file__).parents[1]


class FakeTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, object]]] = []

    def vendor_request(
        self, request_type: str, data: dict[str, object], *, deadline: float | None = None
    ) -> dict[str, object]:
        self.requests.append((request_type, dict(data)))
        return self.responses.pop(0)


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    rows = b"".join(b"\x00" + (b"\x00\x00\x00\xff" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def test_capture_program_frame_returns_verified_bounded_png() -> None:
    image = _png(320, 180)
    image_data = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "eventSequence": 2,
                "sourceName": "RL - Vampire Survivors",
                "imageFormat": "png",
                "imageData": image_data,
            },
        ]
    )

    result = ObsControlBridge(transport, expected_pid=1234).capture_program_frame()

    assert result["sourceName"] == "RL - Vampire Survivors"
    assert result["imageFormat"] == "png"
    assert result["imageWidth"] == 320
    assert result["imageHeight"] == 180
    assert result["byteLength"] == len(image)
    assert result["sha256"] == hashlib.sha256(image).hexdigest()
    assert result["imageData"] == image_data
    assert transport.requests[1] == (
        "CaptureProgramFrame",
        {"imageFormat": "png", "imageWidth": 320, "imageHeight": 180},
    )


def test_capture_program_frame_rejects_corrupt_png_crc() -> None:
    image = bytearray(_png(320, 180))
    image[-17] ^= 0xFF
    transport = FakeTransport(
        [
            {**IDENTITY, "ready": True},
            {
                **IDENTITY,
                "eventSequence": 2,
                "sourceName": "Program",
                "imageFormat": "png",
                "imageData": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
            },
        ]
    )

    with pytest.raises(BridgeError, match="OBS_RESPONSE_INVALID"):
        ObsControlBridge(transport, expected_pid=1234).capture_program_frame()


def test_native_program_frame_uses_bounded_in_memory_obs_screenshot() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")

    assert '"CaptureProgramFrame"' in source
    assert 'obs_websocket_call_request("GetSourceScreenshot", screenshot_request)' in source
    assert 'obs_data_set_int(screenshot_request, "imageWidth", 320)' in source
    assert 'obs_data_set_int(screenshot_request, "imageHeight", 180)' in source
    assert 'obs_data_set_string(result, "imageData", image_data)' in source
    assert '"imageFilePath"' not in source


def test_program_frame_tool_is_exposed_as_bounded_read_only_preview() -> None:
    manifest = yaml.safe_load(
        (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "tools.yaml").read_text(
            encoding="utf-8"
        )
    )
    tool = next(item for item in manifest["tools"] if item["name"] == "capture_program_frame")

    assert tool["source_file"] == "scripts/capture_program_frame.py"
    assert tool["input_schema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert tool["annotations"] == {
        "read_only_hint": True,
        "destructive_hint": False,
        "idempotent_hint": True,
        "open_world_hint": False,
        "deferred_hint": False,
    }
    context = tool["output_schema"]["properties"]["context"]
    assert context["required"][-7:] == [
        "sourceName",
        "imageFormat",
        "imageData",
        "imageWidth",
        "imageHeight",
        "byteLength",
        "sha256",
    ]


def test_program_frame_is_delivered_and_required_before_long_recording() -> None:
    capabilities = json.loads(
        (ROOT / "contracts" / "obs-capabilities-v1.json").read_text(encoding="utf-8")
    )
    screenshots = next(item for item in capabilities["domains"] if item["domain"] == "screenshots")
    skill = (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert screenshots["status"] == "partial"
    assert screenshots["delivered_operations"] == ["capture_program_frame"]
    assert screenshots["remaining_operations"] == ["capture_source_screenshot"]
    assert "Call `capture_program_frame` before every long recording" in skill
