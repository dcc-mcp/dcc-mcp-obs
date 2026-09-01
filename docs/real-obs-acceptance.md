# Disposable real-OBS acceptance

The release gate launches the packaged native plugin and installed Python wheel
inside a disposable OBS Studio instance. It does not attach to or change an
operator's running OBS process, profiles, scenes, plugins, or recordings.

## Isolation and identity

- Windows copies the selected OBS installation into the work root and launches
  that copy in portable, multi-instance mode.
- macOS launches OBS with an isolated CoreFoundation user home plus `HOME` and
  `XDG_CONFIG_HOME`; Linux uses isolated `HOME` and `XDG_CONFIG_HOME` roots.
- The runner installs the supplied, manifest-verified native bundle into the
  isolated plugin root and hashes the binary mapped by the spawned OBS process.
- The supplied wheel is installed before the run. Every installed adapter file
  and its distribution inventory are checked against that wheel before
  acceptance starts; editable installations are rejected.
- OBS WebSocket listens on a random loopback port with a generated password.
  The password is scoped to the adapter process and is never placed in the OBS
  command line, child environment, logs, evidence, or result envelope.
- Every typed call must retain the exact spawned PID, native plugin instance ID,
  and MCP adapter session. Any drift fails the run closed.

## Exercised postconditions

The gate creates and reads back scenes, a source, and a scene item; updates the
item transform; selects and triggers a transition; exercises Studio Mode
preview-to-program switching; and captures a bounded program frame. It then
verifies the recording state sequence
`stopped -> recording -> paused -> recording -> stopped`, waits for the output
file to be finalized, and records its stable size and SHA-256 digest.

Cleanup is also verified. The temporary scene item and source disappear, test
scenes are removed, the original disposable scene is restored, and OBS must
exit through the typed graceful-shutdown request without an active output.

## Running the gate

Install the built wheel, then provide an OBS executable, its platform-matching
canonical plugin bundle, an empty work root, and an evidence destination:

```console
dcc-mcp-obs-accept-host \
  --obs-executable /path/to/obs \
  --native-plugin-archive /path/to/dcc-mcp-obs-<version>-<platform>.zip \
  --python-wheel /path/to/dcc_mcp_obs-<version>-py3-none-any.whl \
  --work-root /temporary/empty-root \
  --output /temporary/evidence.json
```

Linux requires a graphical display; the repository workflow uses `xvfb-run`.
The caller owns removal of the disposable root after inspecting a failure.

## Evidence contract

Successful evidence contains only:

- product, schema, platform architecture, and public versions;
- SHA-256 digests of the supplied native bundle and wheel;
- salted one-way fingerprints proving host, plugin, and adapter binding;
- boolean postconditions for authentication, artifact identity, scene graph,
  transitions, Studio Mode, and recording lifecycle; and
- the finalized recording size and SHA-256 digest.

It contains no PID, instance/session ID, port, password, hostname, local path,
command line, scene/source name, screenshot, or log text. The CI matrix runs the
same real-host gate on Windows, macOS, and Linux and uploads only this JSON file.
Mock tests and native compilation remain useful contracts, but they are not
reported as real-OBS acceptance.

The repository includes the latest checked-in
[Windows acceptance evidence](evidence/real-obs/windows-obs-32.2.1.json).
Exact-head macOS and Linux evidence is published by the real-OBS workflow so
that it remains bound to the artifacts built for each operating system.
