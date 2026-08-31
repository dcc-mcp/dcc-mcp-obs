# dcc-mcp-obs

Native, typed OBS Studio control for the DCC-MCP ecosystem.

This product is an OBS plugin plus a DCC-MCP sidecar. The C++ plugin runs
inside the exact OBS process, owns host lifecycle and UI-thread dispatch, and
registers bounded vendor requests through the official OBS WebSocket 5.x API.
The out-of-process sidecar exposes those contracts through MCP, the Gateway,
an Install SOP v1 CLI, and a bundled Agent skill. Release standalone bundles
carry a private Python runtime; they do not require a system Python install.

OBS WebSocket is the authenticated transport. It is not used as an
unrestricted request escape hatch, and this product exposes no arbitrary
script or raw WebSocket tool.

## First slice

- Exact native plugin, OBS version, PID, instance ID, readiness, and event sequence
- Bounded scene discovery and current-scene readback
- Bounded source discovery for the current or an exact named scene
- Typed scene switching, scene-item CRUD, transitions, and Studio Mode
- Exact Windows PID/HWND window-capture source creation and readback
  preview/program operations with verified readback
- A built-in privacy-safe Agent keyboard/mouse activity overlay source
- A native top-level `DCC MCP` menu for status, overlay setup, Gateway Admin,
  and plugin information
- Recording status
- Start, stop, pause, and resume recording
- A separate typed status readback after every mutation
- Stable redacted errors, bounded UI dispatch, and exact-instance drift rejection

The machine-readable [capability matrix](contracts/obs-capabilities-v1.json)
tracks the remaining product domains. They are intentionally not represented
as shipped tools until their typed contracts land.

## Full-control roadmap

- Streaming, replay buffer, virtual camera, and typed output controls (issue #2)
- [Profiles, scene collections, bounded hotkeys, screenshots, and operator status](https://github.com/dcc-mcp/dcc-mcp-obs/issues/3)
- [Disposable real-OBS acceptance](https://github.com/dcc-mcp/dcc-mcp-obs/issues/4)
- [Inputs, properties, filters, audio, and media](https://github.com/dcc-mcp/dcc-mcp-obs/issues/5)
- [Typed scene graph controls](docs/scene-graph.md)
- [Exact Windows window capture](docs/window-capture.md)
- [Built-in Agent input overlay](docs/agent-input-overlay.md)

## Requirements

- OBS Studio 28 or newer with OBS WebSocket 5.x enabled
- A matching Windows, macOS, or Linux standalone release bundle

Python 3.10+ and `dcc-mcp-core>=0.20.14,<1.0.0` are required only for the
optional PyPI/source installation path. pip resolves Core automatically.

## Install

Download and extract the matching `*-standalone` archive from the GitHub
Release. It contains the sidecar, its private runtime, and the exact native
plugin bundle. Close OBS, then run:

```console
dcc-mcp-obs.exe install-bundled
dcc-mcp-obs.exe --host-pid <obs-pid>
```

On macOS and Linux, use `./dcc-mcp-obs` instead of the `.exe` name. For
developers and users who intentionally prefer the Python package, the existing
installation path remains supported:

```console
python -m pip install dcc-mcp-obs
dcc-mcp-obs-install install \
  --plugin-archive dcc-mcp-obs-plugin.zip \
  --sha256 <release-sha256>
dcc-mcp-obs-install verify
```

Both installer paths emit one Install SOP v1 JSON object. `--dry-run` performs bundle
and ownership preflight without changing the OBS plugin directory. See
[installation details](docs/install.md).

On POSIX systems, a successful filesystem result is a synchronous point-in-time
verification, not a persistent namespace or writer lock. The report publishes
`POSIX_REVERIFY_BEFORE_USE` in `next_steps`; re-run `status` or `verify`
immediately before relying on the files.

## Password and endpoint

Configure the OBS WebSocket password in the operator-owned environment:

```console
set DCC_MCP_OBS_WEBSOCKET_PASSWORD=your-password
```

The first release accepts only `ws://127.0.0.1:<port>` and defaults to port
4455. A password is never returned in tool results, receipts, public errors, or
logs. Use `DCC_MCP_OBS_WEBSOCKET_URL` only to select another loopback port.

Run the sidecar against one exact OBS process. Use the standalone executable
shown above, or this command for a PyPI installation:

```console
dcc-mcp-obs --host-pid <obs-pid>
```

## Agent discovery

The bundled `obs-control` skill includes English and Chinese discovery aliases
for OBS, Open Broadcaster Software, recording, streaming, replay buffer,
virtual camera, outputs, scene/source inspection, pause, resume, 录屏, 直播,
回放缓冲, 虚拟摄像头, 场景图, 场景切换, 按键展示, 键盘, 鼠标, 转场, and
Studio Mode. Agents search
and load the skill before calling the typed tools. Scene-graph mutations are
available only through the native typed contract and require verified
postconditions.

For recorded Agent demonstrations, `create_agent_input_overlay` attaches the
plugin's shared `DCC-MCP Agent Input` source to each selected scene. The Agent
then uses `emit_agent_input_activity` to display only an allowlisted shortcut,
mouse button, wheel direction, or typing count. It never captures global input
or accepts arbitrary text. This keeps code visible through a normal scene
source while the overlay explains the action immediately before the Agent runs
it. See the [Agent input overlay contract](docs/agent-input-overlay.md).

The native plugin adds a top-level `DCC MCP` menu to OBS. `Server Status...`
shows the exact plugin and OBS versions, bridge readiness, active outputs, and
current scene. `Add Agent Input Overlay` attaches the shared built-in source to
the current scene. `Open Gateway Admin` opens only the loopback Gateway URL
(`127.0.0.1`, port 9765 by default or a valid `DCC_MCP_GATEWAY_PORT`). The menu
is registered idempotently on OBS's UI thread and removed during plugin unload.

Use `request_graceful_shutdown` instead of terminating the OBS process. The
native plugin refuses the request while recording, streaming, replay buffer,
or virtual camera output is active, returns a terminal queued acknowledgement,
and then asks the OBS frontend to exit normally. Callers verify process and
plugin-instance disappearance outside the closed connection.

OBS control is native-plugin/WebSocket first. Unsupported visual-only actions
may use DCC-MCP `ui-control` with project-owned DCC-CUA only after exact PID
and HWND binding, a fresh snapshot, and post-action readback. There is no
generic Computer Use fallback.

## Development

```console
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m ruff format --check .
dcc-mcp-cli lint src/dcc_mcp_obs/skills/obs-control --warnings-as-errors
```

The native build uses the official OBS plugin template toolchain and OBS
31.1.1 SDK inputs pinned with SHA-256 hashes:

```console
cmake --preset windows-x64
cmake --build --preset windows-ci-x64
```

Equivalent CI builds run on Windows, macOS, and Linux.

## Validation boundary

Unit tests, fake protocol sessions, native compilation, package smoke tests,
and CI do not prove a licensed/live OBS host. This initial delivery makes no
real-OBS acceptance claim. A disposable live-host acceptance issue remains a
separate release gate.

## License

GPL-2.0-or-later. The native module links to OBS Studio and vendors the official
OBS WebSocket plugin API header with its original notice.
