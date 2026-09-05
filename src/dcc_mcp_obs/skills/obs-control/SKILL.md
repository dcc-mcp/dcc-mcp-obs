---
name: obs-control
description: Inspect and control typed OBS lifecycle, profiles, scene collections, scenes, scene items, reviewed sources, inputs, properties, filters, audio, media, exact Windows window-capture sources, built-in Agent keyboard and mouse activity overlays, transitions, Studio Mode, recording, streaming, replay buffer, virtual camera, allowlisted hotkeys, and outputs for OBS Studio and Open Broadcaster Software.
license: GPL-2.0-or-later
compatibility: "OBS Studio 28+ with obs-websocket 5.x; standalone bundles need no system Python; PyPI/source installs need Python 3.10+"
metadata:
  dcc-mcp:
    dcc: obs
    layer: domain
    version: "1.3.0"  # x-release-please-version
    tags: [obs, lifecycle, graceful-shutdown, profiles, scene-collections, scenes, scene-items, sources, inputs, properties, filters, audio, media, window-capture, agent-input-overlay, keyboard-activity, mouse-activity, transitions, studio-mode, recording, streaming, replay-buffer, virtual-camera, allowlisted-hotkeys, screenshots, outputs]
    search-hint: "OBS Open Broadcaster Software lifecycle graceful shutdown exit close profiles scene collections scenes scene graph scene items sources inputs properties filters audio media transitions Studio Mode preview program hotkeys screenshots operator status recording streaming replay buffer virtual camera outputs record video pause resume Agent input overlay keyboard mouse activity keystroke display typing count 录屏 录制视频 直播 回放 缓冲 虚拟摄像头 配置文件 场景集合 场景图 场景项 source 输入 属性 滤镜 音频 媒体 转场 导播台 预览 节目 快捷键 按键展示 键盘 鼠标 输入提示 打字计数 截图 输出 优雅退出 关闭"
    tools: tools.yaml
---

# OBS Control

Use this skill whenever the user refers to OBS, Open Broadcaster Software,
profiles, scene collections, scenes, scene items, transitions, Studio Mode,
recording, streaming, replay buffer, virtual camera, allowlisted hotkeys,
screenshots, outputs, scene/source inspection, inputs, properties, filters,
audio, media, Agent input overlays, keyboard or
mouse activity display, 录屏, 直播, 回放缓冲, 虚拟摄像头, 场景图, 场景项,
按键展示, 键盘, 鼠标, 输入提示, 转场, 导播台, 预览, or 输出.

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
9. Windows window-capture creation requires an exact PID, HWND, and current
   title. The native plugin derives and revalidates the window class,
   executable basename, and process creation time before and after mutation;
   identity drift fails closed.
10. `request_graceful_shutdown` is terminal and requires explicit operator
    intent. The native plugin refuses while recording, streaming, replay
    buffer, or virtual camera output is active. Its response proves only that
    shutdown was accepted and queued; verify process/instance disappearance
    out of band and do not issue another OBS tool call on that connection.
11. Hotkey actions accept only identifiers returned by the allowlist contract.
    `capture_program_frame` returns a bounded in-memory PNG through OBS's
    completed `GetSourceScreenshot` readback. The legacy fire-and-forget
    source/file screenshot path remains unexposed; never synthesize its success.
12. Agent input overlays are plugin-rendered OBS sources, not global input
    listeners. Attach the same source name to each demo scene, then emit only
    the semantic action the Agent is about to perform: an allowlisted shortcut,
    mouse button, wheel direction, or typing count. Never submit typed text,
    secrets, arbitrary labels, or inferred human input.
13. Generic source settings are never forwarded. Public schema version `1.0`
    allows only `color_source_v3` with bounded `width`, `height`, and `color`,
    and `gain_filter` with bounded `db`. Unsupported kinds, versions, fields,
    and values fail before mutation.
14. Bind source, filter, audio, and media operations to exact discovered names.
    Audio setters allow only bounded volume, boolean mute, and the three named
    monitor routes. Media transport requires a controllable-media source and
    every mutation must reconcile through `get_media_status`.

## Scene graph workflow

For reviewed source, property, filter, audio, and media work, start with
`get_source_identity`, `list_input_kinds`, or `list_filters`. Preserve exact
names and schema version `1.0`; continue after a mutation only when
`postcondition.verified=true`. See the
[typed source control reference](../../../docs/typed-source-controls.md).

For scene-graph work, discover scenes before selecting one, then discover the
scene items or transitions needed by the mutation. Keep the returned scene
name, transition name, and scene-item ID in the request context so the native
plugin can bind the operation to one exact object. After switching a scene,
editing an item, changing Studio Mode preview, or transitioning to program,
inspect the typed readback and continue only when `verified` is true. See
[the scene-graph reference](../../../docs/scene-graph.md) for the operation
surface and live-host validation boundary.

For Windows game or application recording, use
`list_window_capture_candidates` with one exact executable basename and,
when stable, one exact title. Then call `create_window_capture_source` only
with a returned PID/HWND/title identity. Use `get_window_capture_source`
immediately before recording to revalidate the binding. If an RL host
candidate reports `captureReady=false` because it is minimized, call
`restore_window_capture_candidate` with that exact executable/PID/HWND/title
and continue only after its verified readback reports a positive client area.
If an RL host
restarts, discover its new visible window and call
`rebind_window_capture_source` with both the stored old identity and the fresh
new identity; the native plugin owns the transaction and rolls back a failed
postcondition. The optional `capture_method` is limited to `automatic`,
`bitblt`, and `windows_graphics_capture`; use `set_window_capture_method` to
change an existing exact source when automatic BitBlt produces a black or
incorrect game frame. These tools never accept arbitrary OBS input settings.
See [the window-capture reference](../../../docs/window-capture.md).

Call `capture_program_frame` before every long recording and inspect the
returned PNG. Do not treat scene/source metadata or an active recording flag
as visual evidence. If the frame is black or incorrect, stop before recording,
repair the exact source binding or capture method, and capture a fresh frame.

For simultaneous Agent demonstrations, call `create_agent_input_overlay` once
per game scene with a distinct source name. Inspect each game frame and use
`set_agent_input_overlay_layout` to choose a non-obscuring edge anchor, bounded
opacity, and margin. Include `agent_id` with every
`emit_agent_input_activity` call. Emit only semantic shortcuts, mouse cues, or
typing counts; never typed text. Call `clear_agent_input_overlay` when a demo
section ends. The overlay does not install OS hooks or observe user input.

Use `start_scene_recordings` for simultaneous per-game MP4s. Each requested
scene must contain exactly one enabled Windows window-capture source; its native
dimensions become that output's video dimensions. Do not include VS Code,
editor, or other non-game scenes. Verify `videoWidth`, `videoHeight`, output
paths, and active state through `get_scene_recording_session`, then stop the
whole exact session with `stop_scene_recordings`.

## UI fallback

OBS control is native-plugin/WebSocket first. Only an OBS action that has no
typed contract may use the DCC-MCP `ui-control` route with project-owned
DCC-CUA. Bind the exact OBS PID and HWND, capture a fresh snapshot before the
action, and require post-action readback. Never silently substitute generic Computer Use.
