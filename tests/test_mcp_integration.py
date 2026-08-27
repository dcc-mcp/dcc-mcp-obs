from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

from dcc_mcp_obs import server
from dcc_mcp_obs.skills.obs_control.scripts import _client

IDENTITY = {
    "instanceId": "obs-integration",
    "pluginVersion": "0.1.0",
    "obsVersion": "31.1.1",
    "hostPid": os.getpid(),
    "eventSequence": 1,
    "ready": True,
}


class FakeTransport:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.closed = False
        self.event_sequence = 0

    def vendor_request(
        self,
        request_type: str,
        _data: dict[str, object],
        *,
        deadline: float | None = None,
    ) -> dict[str, object]:
        assert request_type == "GetPluginStatus"
        assert deadline is not None
        self.event_sequence += 1
        return {**IDENTITY, "eventSequence": self.event_sequence}

    def close(self) -> None:
        self.closed = True


def test_real_mcp_search_load_describe_and_call_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DCC_MCP_GATEWAY_PORT", "0")
    monkeypatch.setenv("DCC_MCP_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS", "1")
    monkeypatch.setattr(server, "resolve_obs_pid", lambda _pid=None: os.getpid())
    monkeypatch.setattr(server, "ObsWebSocketTransport", FakeTransport)
    monkeypatch.setattr(_client, "resolve_obs_pid", lambda _pid=None: os.getpid())
    monkeypatch.setattr(_client, "ObsWebSocketTransport", FakeTransport)

    instance = server.ObsMcpServer(port=0, host_pid=os.getpid())
    instance.register_builtin_actions()
    handle = instance.start()
    url = handle.mcp_url()
    session: str | None = None

    def post(payload: dict[str, object]) -> dict[str, Any]:
        nonlocal session
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session:
            headers["Mcp-Session-Id"] = session
        request = urllib.request.Request(url, json.dumps(payload).encode(), headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            session = response.headers.get("Mcp-Session-Id") or session
            body = response.read().decode().strip()
        if body.startswith("event:") or "\ndata: " in body:
            body = next(line[6:] for line in body.splitlines() if line.startswith("data: "))
        return json.loads(body) if body else {}

    try:
        post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "obs-smoke", "version": "0"},
                },
            }
        )
        post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        discovered = post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_skills",
                    "arguments": {"query": "OBS 录制视频 recording"},
                },
            }
        )
        assert "obs-control" in json.dumps(discovered)
        loaded = post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "load_skill", "arguments": {"skill_name": "obs-control"}},
            }
        )
        assert "obs_control__get_status" in json.dumps(loaded)
        described_tools: list[dict[str, Any]] = []
        cursor: str | None = None
        page_id = 4
        while True:
            params = {"cursor": cursor} if cursor else {}
            described = post(
                {"jsonrpc": "2.0", "id": page_id, "method": "tools/list", "params": params}
            )
            described_tools.extend(described["result"]["tools"])
            cursor = described["result"].get("nextCursor")
            if not cursor:
                break
            page_id += 1
        assert any(tool["name"] == "start_recording" for tool in described_tools)
        called = post(
            {
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tools/call",
                "params": {"name": "obs_control__get_status", "arguments": {}},
            }
        )
        envelope = called["result"]["structuredContent"]
        result = envelope
        if "job_id" in envelope:
            job_id = envelope["job_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                called = post(
                    {
                        "jsonrpc": "2.0",
                        "id": 101,
                        "method": "tools/call",
                        "params": {
                            "name": "jobs_get_status",
                            "arguments": {
                                "job_id": job_id,
                                "include_result": True,
                            },
                        },
                    }
                )
                envelope = called["result"]["structuredContent"]
                status = envelope.get("status")
                if status in {"completed", "failed", "cancelled", "interrupted"}:
                    break
                time.sleep(0.05)
            assert envelope["status"] == "completed"
            result = envelope["result"]
        else:
            assert envelope["success"] is True
        assert "obs-integration" in json.dumps(result)
    finally:
        instance.stop()
