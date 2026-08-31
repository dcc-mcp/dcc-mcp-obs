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

Keep the executable, its `lib` directory, runtime libraries, manifest, and
native plugin archive together. Python 3.10+ remains supported for developers
and users who intentionally install the PyPI package instead.
