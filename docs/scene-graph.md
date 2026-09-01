# Typed scene graph controls

Issue #6 adds a bounded, typed scene-graph surface to the native OBS plugin.
The Python sidecar exposes named operations; callers never send arbitrary OBS
WebSocket requests or scripts.

## Operations

- Discover scenes and read the current program scene.
- Select one discovered scene by exact name and verify the current-scene
  readback.
- List scene items and create, update, or remove one item using its exact
  scene name and numeric `sceneItemId`.
- Read and select a transition by exact name, with typed duration settings.
- Read Studio Mode status, select the preview scene, and trigger a
  preview-to-program transition.

The operation names and response schemas are defined in the bundled
`obs-control` skill (`src/dcc_mcp_obs/skills/obs-control/tools.yaml`). Every
successful mutation includes `verified: true` only after the native plugin has
read the resulting state back from OBS.

## Safety contract

Mutations are capability-scoped to the operation being requested, bound to the
exact native plugin instance (including its instance ID and host PID), and
bounded by an absolute deadline. The native UI gate can cancel work before it
mutates OBS. A timeout or identity drift fails closed; it never reports a
mutation as verified. Duplicate or undiscovered names are rejected before the
request crosses the native boundary.

## Validation boundary

The repository runs fake-host/adversarial contract tests and compiles the
native contract tests on Windows, macOS, and Linux. The separate
[disposable real-OBS gate](real-obs-acceptance.md) then exercises the packaged
plugin and installed wheel against actual OBS processes on those platforms,
including scene-item CRUD, transitions, and Studio Mode readback.
