# Install DCC-MCP OBS

This is the adapter-maintained runbook consumed by `dcc-mcp-cli`. The
recommended release archive contains the standalone sidecar, its private
Python runtime, an exact `dcc-mcp-core`, and the matching native OBS plugin.
Python 3.10+ remains optional for the separate PyPI/source path.

## Agent quick path

Resolve the official adapter and read this runbook without mutating the host:

```console
dcc-mcp-cli install --dcc-type obs
```

For the no-system-Python path, download and extract one immutable
`dcc-mcp-obs-<version>-<platform>-standalone` release archive. Keep its files
together, close OBS, then install the adjacent checksummed native plugin:

```powershell
.\dcc-mcp-obs.exe install-bundled
$obsExe = (Resolve-Path .\dcc-mcp-obs.exe).Path
$env:DCC_MCP_OBS_EXECUTABLE = $obsExe
[Environment]::SetEnvironmentVariable("DCC_MCP_OBS_EXECUTABLE", $obsExe, "User")
```

```bash
./dcc-mcp-obs install-bundled
export DCC_MCP_OBS_EXECUTABLE="$(pwd -P)/dcc-mcp-obs"
```

`DCC_MCP_OBS_EXECUTABLE` is the only native-plugin autostart override. It must
name an absolute executable file. The plugin passes only `--host-pid` for the
current OBS process and never invokes a shell. If the variable is absent, no
process is launched and the sidecar can still be started manually.

The standalone process sets `DCC_MCP_PYTHON_EXECUTABLE` to its own executable
for Core-managed skill scripts. Do not persist that generic variable globally
on a mixed-DCC workstation. `dcc-mcp-cli` calls should route through the live
OBS instance instead.

Restart OBS after changing the plugin or environment. Enable OBS WebSocket and
provide its password only through `DCC_MCP_OBS_WEBSOCKET_PASSWORD`. Then verify
the exact live instance:

```console
dcc-mcp-cli doctor
dcc-mcp-cli list
dcc-mcp-cli wait-ready --dcc-type obs
dcc-mcp-cli search --dcc-type obs --query recording
```

## Optional PyPI/source path

Use `dcc-mcp-cli install --dcc-type obs --execute` only when a managed system
Python deployment is intentional. The catalog installs the pinned wheel and
pip resolves `dcc-mcp-core`; the native plugin still requires the adapter-owned
lifecycle command documented in [docs/install.md](docs/install.md).

## Internal deployment

Studios may unpack the complete standalone archive into an immutable managed
directory and set `DCC_MCP_OBS_EXECUTABLE` to that release's absolute launcher.
Roll out the executable, its `lib` directory/runtime libraries, manifest, and
`dcc-mcp-obs-plugin.zip` as one versioned unit. Never point the variable at a
mutable download, wrapper script, or unverified binary.
