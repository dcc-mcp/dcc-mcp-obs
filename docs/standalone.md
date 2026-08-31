# DCC-MCP OBS standalone

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
