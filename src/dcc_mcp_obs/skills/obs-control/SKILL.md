---
name: obs-control
description: Control OBS Studio and Open Broadcaster Software for recording, streaming, scene switching, 录屏, 录制视频, 直播, and 场景切换 through the native DCC-MCP OBS plugin.
license: GPL-2.0-or-later
compatibility: "Python 3.10+, OBS Studio 28+ with obs-websocket 5.x"
metadata:
  dcc-mcp:
    dcc: obs
    layer: domain
    version: "0.1.0"  # x-release-please-version
    tags: [obs, recording, streaming, video, scenes]
    search-hint: "OBS Open Broadcaster Software recording streaming record video 录屏 录制视频 直播 场景切换 scene sources pause resume"
    tools: tools.yaml
---

# OBS Control

Use this skill whenever the user refers to OBS, Open Broadcaster Software,
recording, streaming, 录屏, 录制视频, 直播, or 场景切换.

## Route

1. Use native plugin status and read-only scene/source discovery first.
2. Ask for confirmation before start/stop/pause/resume recording when the user
   did not explicitly request that state change.
3. Treat a mutation response as provisional. Require its typed readback with
   `verified=true` and retain the exact OBS instance identity.
4. Never request, print, log, or return the OBS WebSocket password. The operator
   owns `DCC_MCP_OBS_WEBSOCKET_PASSWORD`.
5. Never construct a raw OBS WebSocket request or execute arbitrary script/code.

## UI fallback

OBS control is native-plugin/WebSocket first. Only an OBS action that has no
typed contract may use the DCC-MCP `ui-control` route with project-owned
DCC-CUA. Bind the exact OBS PID and HWND, capture a fresh snapshot before the
action, and require post-action readback. Never silently substitute generic Computer Use.
