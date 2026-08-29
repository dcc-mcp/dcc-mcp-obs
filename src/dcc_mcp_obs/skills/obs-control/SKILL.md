---
name: obs-control
description: Inspect and control typed OBS profiles, scene collections, scenes, scene items, transitions, Studio Mode, recording, streaming, replay buffer, virtual camera, allowlisted hotkeys, and outputs for OBS Studio and Open Broadcaster Software.
license: GPL-2.0-or-later
compatibility: "Python 3.10+, OBS Studio 28+ with obs-websocket 5.x"
metadata:
  dcc-mcp:
    dcc: obs
    layer: domain
    version: "1.1.0"  # x-release-please-version
    tags: [obs, profiles, scene-collections, scenes, scene-items, transitions, studio-mode, recording, streaming, replay-buffer, virtual-camera, allowlisted-hotkeys, screenshots, outputs, sources]
    search-hint: "OBS Open Broadcaster Software profiles scene collections scenes scene graph scene items transitions Studio Mode preview program hotkeys screenshots operator status recording streaming replay buffer virtual camera outputs record video pause resume 录屏 录制视频 直播 回放 缓冲 虚拟摄像头 配置文件 场景集合 场景图 场景项 转场 导播台 预览 节目 快捷键 截图 输出"
    tools: tools.yaml
---

# OBS Control

Use this skill whenever the user refers to OBS, Open Broadcaster Software,
profiles, scene collections, scenes, scene items, transitions, Studio Mode,
recording, streaming, replay buffer, virtual camera, allowlisted hotkeys,
screenshots, outputs, scene/source inspection, 录屏, 直播, 回放缓冲,
虚拟摄像头, 场景图, 场景项, 转场, 导播台, 预览, or 输出.

## Route

1. Use native plugin status and read-only profile, scene-collection, and
   scene/source discovery first.
2. Ask for confirmation before start/stop/pause/resume recording when the user
   did not explicitly request that state change.
3. Treat a mutation response as provisional. For state mutations, require its
   typed readback with `postcondition.verified=true`; replay-buffer saves are
   asynchronous and only report `accepted=true, submitted=true`.
4. Never request, print, log, or return the OBS WebSocket password. The operator
   owns `DCC_MCP_OBS_WEBSOCKET_PASSWORD`.
5. Streaming, replay-buffer, virtual-camera, and output start/stop/save tools are
   dangerous mutations: require explicit operator intent. State changes use a
   verified typed postcondition; replay saves remain submitted until a later
   completion/artifact contract exists. Reconciliation is bounded and never
   retries indefinitely.
6. Never construct a raw OBS WebSocket request or execute arbitrary script/code.
7. Profile and scene-collection changes require exact-name discovery and a
   verified current-name readback; duplicate names fail closed.
8. Scene-graph mutations require exact scene identity and (for scene items)
   the numeric item ID returned by discovery. Select transitions and scenes by
   exact discovered names; Studio Mode preview/program changes require typed
   status readback.
9. Hotkey actions accept only identifiers returned by the allowlist contract.
   Screenshot capture is intentionally not exposed until OBS provides a
   completion/artifact readback contract; never synthesize success or verified
   output for the fire-and-forget API.

## Scene graph workflow

For scene-graph work, discover scenes before selecting one, then discover the
scene items or transitions needed by the mutation. Keep the returned scene
name, transition name, and scene-item ID in the request context so the native
plugin can bind the operation to one exact object. After switching a scene,
editing an item, changing Studio Mode preview, or transitioning to program,
inspect the typed readback and continue only when `verified` is true. See
[the scene-graph reference](../../../docs/scene-graph.md) for the operation
surface and live-host validation boundary.

## UI fallback

OBS control is native-plugin/WebSocket first. Only an OBS action that has no
typed contract may use the DCC-MCP `ui-control` route with project-owned
DCC-CUA. Bind the exact OBS PID and HWND, capture a fresh snapshot before the
action, and require post-action readback. Never silently substitute generic Computer Use.
