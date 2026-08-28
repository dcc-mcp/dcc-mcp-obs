---
name: obs-control
description: Inspect OBS Studio scenes and sources and control recording, pause, and resume for OBS, Open Broadcaster Software, 录屏, and 录制视频 through the native DCC-MCP OBS plugin.
license: GPL-2.0-or-later
compatibility: "Python 3.10+, OBS Studio 28+ with obs-websocket 5.x"
metadata:
  dcc-mcp:
    dcc: obs
    layer: domain
    version: "1.0.0"  # x-release-please-version
    tags: [obs, recording, video, scenes, sources]
    search-hint: "OBS Open Broadcaster Software recording record video 录屏 录制视频 scenes sources pause resume 暂停 继续录制"
    tools: tools.yaml
---

# OBS Control

Use this skill whenever the user refers to OBS, Open Broadcaster Software,
recording, scene/source inspection, 录屏, or 录制视频. Streaming and scene
switching remain tracked roadmap capabilities, not shipped tools.

## Route

1. Use native plugin status and read-only scene/source discovery first.
2. Ask for confirmation before start/stop/pause/resume recording when the user
   did not explicitly request that state change.
3. Treat a mutation response as provisional. Require its typed readback with
   `postcondition.verified=true` and retain the exact OBS instance identity
   from `context`.
4. Never request, print, log, or return the OBS WebSocket password. The operator
   owns `DCC_MCP_OBS_WEBSOCKET_PASSWORD`.
5. Never construct a raw OBS WebSocket request or execute arbitrary script/code.

## UI fallback

OBS control is native-plugin/WebSocket first. Only an OBS action that has no
typed contract may use the DCC-MCP `ui-control` route with project-owned
DCC-CUA. Bind the exact OBS PID and HWND, capture a fresh snapshot before the
action, and require post-action readback. Never silently substitute generic Computer Use.
