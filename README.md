# dcc-mcp-obs

Native, typed OBS Studio control for the DCC-MCP ecosystem.

This product is an OBS plugin plus a DCC-MCP sidecar. The C++ plugin runs
inside the exact OBS process, owns host lifecycle and UI-thread dispatch, and
registers bounded vendor requests through the official OBS WebSocket 5.x API.
The out-of-process sidecar exposes those contracts through MCP, the Gateway,
an Install SOP v1 CLI, and a bundled Agent skill. Release runtime bundles carry
the shared `dcc-mcp-runtime` and adapter wheels alongside the native plugin.

OBS WebSocket is the authenticated transport. It is not used as an
unrestricted request escape hatch, and this product exposes no arbitrary
script or raw WebSocket tool.

<!-- dcc-mcp-agent-quickstart:start -->
## Use OBS Studio with AI agents

Install the official DCC-MCP Agent Skill. Codex users can use the native plugin
marketplace:

```powershell
codex plugin marketplace add dcc-mcp/dcc-mcp-agent-plugins
codex plugin add dcc-mcp@dcc-mcp
```

For Claude Code, CodeBuddy, Cursor, Gemini CLI, and other supported agents, use
the [official installation guide](https://github.com/dcc-mcp/dcc-mcp-agent-plugins#install). Start OBS Studio,
enable this adapter, and verify that the running instance is registered:

```powershell
dcc-mcp-cli list
```

Then ask your agent:

```text
Use dcc-mcp to inspect the current OBS scene and streaming status.
```

The list must include `dcc_type=obs`. If it does not, follow the
[connection troubleshooting guide](https://github.com/dcc-mcp/dcc-mcp-agent-plugins#adapter-connection-troubleshooting).
<!-- dcc-mcp-agent-quickstart:end -->

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
- Start, stop, pause, and resume recording with an optional per-run output directory
- Typed streaming, replay-buffer, virtual-camera, and named-output controls
- Reviewed source/input/property/filter contracts plus exact audio and media controls
- A separate typed status readback after every mutation
- Stable redacted errors, bounded UI dispatch, and exact-instance drift rejection

The machine-readable [capability matrix](contracts/obs-capabilities-v1.json)
tracks delivered and remaining product domains. Operations are represented as
shipped tools only after their typed contracts land.

## Delivered control surfaces

- [Streaming, replay buffer, virtual camera, and typed output controls](https://github.com/dcc-mcp/dcc-mcp-obs/issues/2)
- [Inputs, properties, filters, audio, and media](docs/typed-source-controls.md)
- [Typed scene graph controls](docs/scene-graph.md)
- [Exact Windows window capture](docs/window-capture.md)
- [Built-in Agent input overlay](docs/agent-input-overlay.md)

## Full-control roadmap

- [Profiles, scene collections, bounded hotkeys, screenshots, and operator status](https://github.com/dcc-mcp/dcc-mcp-obs/issues/3)
- [Disposable real-OBS acceptance](docs/real-obs-acceptance.md)

## Requirements

- OBS Studio 28 or newer with OBS WebSocket 5.x enabled
- A matching Windows, macOS, or Linux shared-runtime release bundle

Python 3.10+ and `dcc-mcp-core>=0.20.14,<1.0.0` are resolved by the adapter
wheel; the shared runtime owns the process environment.

## Install

Download and extract the matching `*-runtime` archive from the GitHub Release.
It contains the shared runtime and adapter wheels, manifests, and the exact
native plugin bundle. Set the runtime root, install both wheels, and run:

```console
$env:DCC_MCP_RUNTIME_ROOT = (Resolve-Path .).Path
python -m pip install wheels/dcc_mcp_runtime-*.whl wheels/dcc_mcp_obs-*.whl
python -m dcc_mcp_obs.runtime_entry --host-pid <obs-pid>
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

The adapter exposes its own MCP control endpoint separately from OBS WebSocket.
It defaults to `127.0.0.1:9766`; override it with
`DCC_MCP_OBS_CONTROL_PORT`. The two ports must differ. `DCC_MCP_OBS_TRANSPORT`
defaults to `dual`, keeping the independent control endpoint and the
obs-websocket compatibility path available together; set it to `websocket` for
compatibility-only deployments.

Run the sidecar against one exact OBS process:

```console
dcc-mcp-obs-runtime --host-pid <obs-pid>
```

## Agent discovery

The bundled `obs-control` skill includes English and Chinese discovery aliases
for OBS, Open Broadcaster Software, recording, streaming, replay buffer,
virtual camera, outputs, scene/source inspection, pause, resume, 录屏, 直播,
回放缓冲, 虚拟摄像头, 场景图, 场景切换, 按键展示, 键盘, 鼠标, 转场, and
Studio Mode, properties, filters, audio, and media. Agents search
and load the skill before calling the typed tools. Scene-graph mutations are
available only through the native typed contract and require verified
postconditions.

Generic input settings are never forwarded. The public reviewed settings
contract is version `1.0`: `color_source_v3` exposes only bounded `width`,
`height`, and `color`, while `gain_filter` exposes only bounded `db`. Source,
filter, audio, and media mutations use exact names and bounded reconciliation.
See [typed source controls](docs/typed-source-controls.md).

Windows `window_capture` sources expose a typed `capture_audio` boolean for
normal Program recordings. Use `set_window_capture_audio` with the exact
PID/HWND/title binding and current capture method; the plugin reads the setting
back and rolls it back if verification fails.

For recorded Agent demonstrations, `create_agent_input_overlay` attaches a
built-in input source to each selected scene. Use a distinct source name per
simultaneously operating Agent, then `set_agent_input_overlay_layout` can choose
one of eight edge anchors plus bounded opacity and margin after inspecting the
game frame. `emit_agent_input_activity` displays the Agent identity and only an
allowlisted shortcut, mouse button, wheel direction, or typing count. It never
captures global input or accepts arbitrary text. See the
[Agent input overlay contract](docs/agent-input-overlay.md).

Normal Program recordings accept an optional absolute `output_directory` on
`start_recording`; omitting it preserves the active OBS profile default.
`stop_recording` waits up to two minutes for the muxer and returns
`stopPending=true` with `outputState=finalizing` when bounded polling must
continue. Status classifies terminal artifacts as `complete`, `stalled`,
`empty`, `failed`, or `missing`, reports the actual `.stalled` path, and reads
the final byte count from disk after the output closes.

`start_scene_recordings` starts a private video-only output without switching
the OBS program scene. Calls create separate sessions and may overlap up to
eight active outputs in one OBS instance; stopping one session leaves the
others running. Each output uses its scene's single enabled `window_capture`
source at native dimensions and includes that scene's Agent input overlay.
The MP4 muxer receives a private silent AAC timing track because OBS requires
an audio encoder for MP4 outputs; the session never reads the OBS audio mixer,
so its public contract remains video-only and cannot leak another app's audio.
Pass an absolute `output_directory` to separate application artifacts, or omit
it to use the current OBS profile recording directory. Application/run IDs and
an expected source/PID/HWND can be supplied and are returned in typed status;
start fails closed when an expected window binding no longer matches.

The native plugin adds a top-level `DCC MCP` menu to OBS. `Server Status...`
shows the exact plugin and OBS versions, bridge readiness, active outputs, and
current scene. `Add Agent Input Overlay` attaches the shared built-in source to
the current scene. `Open Gateway Admin` opens only the loopback Gateway URL
(`127.0.0.1`, port 9766 by default or a valid `DCC_MCP_OBS_CONTROL_PORT`). The menu
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

Unit tests, adversarial fake protocol sessions, native compilation, and package
smoke tests remain separate from host acceptance. The disposable real-OBS gate
launches the packaged plugin and installed wheel on Windows, macOS, and Linux,
verifies exact process/session binding and state readback, and publishes only
privacy-safe evidence. See [the acceptance contract](docs/real-obs-acceptance.md).

## License

GPL-2.0-or-later. The native module links to OBS Studio and vendors the official
OBS WebSocket plugin API header with its original notice.
