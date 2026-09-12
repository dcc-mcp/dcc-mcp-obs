# DCC-MCP OBS standalone

## Shared runtime (preferred deployment)

For managed deployments, use the shared `dcc-mcp-runtime` executable and load
the `dcc-mcp-obs` wheel from its `lib/site-packages`. Start the adapter through
the `dcc-mcp-obs-runtime` entry point. It validates the runtime/adapter
manifest handshake before opening the MCP endpoint and keeps the native OBS
plugin/WebSocket boundary inside OBS. Set `DCC_MCP_RUNTIME_ROOT` to the
side-by-side runtime root when the launcher is not adjacent to it.

The legacy self-contained PyOxidizer archive remains available for rollback
while shared-runtime rollout is staged; it is not used by the shared entry
point and is not a fallback for a failed handshake.

This bundle contains the OBS sidecar and its private Python runtime. End users
do not need to install Python or `dcc-mcp-core`.

When the release bundle includes `dcc-mcp-obs-plugin.zip`, close OBS and install
the native plugin with:

```powershell
.\dcc-mcp-obs.exe install-bundled
```

Restart OBS, enable OBS WebSocket, and start the sidecar against one exact OBS
process:

```powershell
.\dcc-mcp-obs.exe --host-pid <pid>
```

For managed or automatic startup, set `DCC_MCP_OBS_EXECUTABLE` to the absolute
path of this executable before restarting OBS. The native plugin uses only that
explicit path and passes the current OBS PID. The standalone runtime sets
`DCC_MCP_PYTHON_EXECUTABLE` for its own Core-managed skill processes.

Keep the executable, its `lib` directory, runtime libraries, manifest, and
native plugin archive together. Python 3.10+ remains supported for developers
and users who intentionally install the PyPI package instead.
