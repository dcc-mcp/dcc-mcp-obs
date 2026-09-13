# DCC-MCP OBS runtime distribution

## Shared runtime (preferred deployment)

For managed deployments, use the shared `dcc-mcp-runtime` executable and load
the `dcc-mcp-obs` wheel from its `lib/site-packages`. Start the adapter through
the `dcc-mcp-obs-runtime` entry point. It validates the runtime/adapter
manifest handshake before opening the MCP endpoint and keeps the native OBS
plugin/WebSocket boundary inside OBS. Set `DCC_MCP_RUNTIME_ROOT` to the
side-by-side runtime root when the launcher is not adjacent to it.

The retired per-adapter PyOxidizer source remains in the repository only for
historical compatibility. CI no longer builds or publishes it, and operators
must not use the old `dcc-mcp-obs[.exe]` standalone commands as a fallback for a
failed shared-runtime handshake.

Keep the shared runtime wheel, adapter wheel, runtime manifests, and native
plugin archive together. Verify `SHA256SUMS` before deployment, install the
native plugin through the Install SOP, and start the adapter with
`dcc-mcp-obs-runtime --host-pid <pid>`. Python 3.10+ remains supported only for
developers and users who intentionally choose the separate PyPI path.
