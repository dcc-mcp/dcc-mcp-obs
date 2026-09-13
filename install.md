# Install DCC-MCP OBS

This is the adapter-maintained runbook consumed by `dcc-mcp-cli`. The
recommended release archive contains the shared runtime and adapter wheels,
runtime manifests, and the matching native OBS plugin. Its wrappers currently
require Python 3.10+ as a bootstrap interpreter. The separate PyPI/source path
has its own Python and `dcc-mcp-core` dependency requirements.

## Agent quick path

Resolve the official adapter and read this runbook without mutating the host:

```console
dcc-mcp-cli install --dcc-type obs
```

Download and extract one immutable
`dcc-mcp-obs-<version>-<platform>-runtime` release archive. Keep its files
together. The bundled installer verifies every manifest-bound file before
installing both wheels and the exact native plugin:

```powershell
.\install.ps1 -DryRun
.\install.ps1 -Yes
```

```bash
bash install.sh --dry-run
bash install.sh --yes
```

After the wrapper successfully starts Python, the installer emits one JSON
report. The successful report includes the exact `DCC_MCP_RUNTIME_ROOT` value
and `dcc-mcp-obs-runtime --host-pid <OBS_PID>` launch command. A missing or
unusable interpreter fails before this JSON contract begins and without
modifying the host.

The native plugin is installed through the adapter-owned lifecycle and only
starts the explicitly configured shared-runtime entry point. If the runtime
root or manifest is absent, startup fails closed and no process is launched.

The shared runtime process sets `DCC_MCP_PYTHON_EXECUTABLE` to its own executable
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

Studios should unpack the complete runtime archive into an immutable managed
directory and roll out the runtime wheel, adapter wheel, manifests, and native
plugin archive as one versioned unit. Never point `DCC_MCP_RUNTIME_ROOT` at a
mutable download or unverified directory.
