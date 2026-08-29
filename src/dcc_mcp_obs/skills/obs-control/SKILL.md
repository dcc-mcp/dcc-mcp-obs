---
name: obs-control
description: Inspect and control typed OBS scenes, recording, streaming, replay buffer, virtual camera, and outputs for OBS Studio and Open Broadcaster Software.
license: GPL-2.0-or-later
compatibility: "Python 3.10+, OBS Studio 28+ with obs-websocket 5.x"
metadata:
  dcc-mcp:
    dcc: obs
    layer: domain
    version: "1.0.0"  # x-release-please-version
    tags: [obs, recording, streaming, replay-buffer, virtual-camera, outputs, scenes, sources]
    search-hint: "OBS Open Broadcaster Software recording streaming replay buffer virtual camera outputs record video pause resume 录屏 录制视频 直播 回放 缓冲 虚拟摄像头 输出"
    tools: tools.yaml
---

# OBS Control

Use this skill whenever the user refers to OBS, Open Broadcaster Software,
recording, streaming, replay buffer, virtual camera, outputs, scene/source
inspection, 录屏, 直播, 回放缓冲, 虚拟摄像头, or 输出.

## Route

1. Use native plugin status and read-only scene/source discovery first.
2. Ask for confirmation before start/stop/pause/resume recording when the user
   did not explicitly request that state change.
3. Treat a mutation response as provisional. Require its typed readback with
   `postcondition.verified=true` and retain the exact OBS instance identity
   from `context`.
4. Never request, print, log, or return the OBS WebSocket password. The operator
   owns `DCC_MCP_OBS_WEBSOCKET_PASSWORD`.
5. Streaming, replay-buffer, virtual-camera, and output start/stop/save tools are
   dangerous mutations: require explicit operator intent and use their verified
   typed postcondition. Reconciliation is bounded and never retries indefinitely.
6. Never construct a raw OBS WebSocket request or execute arbitrary script/code.

## UI fallback

OBS control is native-plugin/WebSocket first. Only an OBS action that has no
typed contract may use the DCC-MCP `ui-control` route with project-owned
DCC-CUA. Bind the exact OBS PID and HWND, capture a fresh snapshot before the
action, and require post-action readback. Never silently substitute generic Computer Use.
