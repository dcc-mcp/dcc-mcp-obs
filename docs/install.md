# Installation and lifecycle

## Shared runtime deployment

The preferred deployment installs one digest-pinned `dcc-mcp-runtime` per machine and
places the versioned `dcc-mcp-obs` wheel in its adapter bundle. Use
`dcc-mcp-obs-runtime` (or `python -m dcc_mcp_obs.runtime_entry`) to start the
sidecar. The entry point requires a successful `obs` capability handshake and
sets `DCC_MCP_PYTHON_EXECUTABLE` only for that process. Install and upgrade the
native OBS plugin separately through the existing Install SOP; do not inject a
second Python interpreter into OBS.

Set `DCC_MCP_RUNTIME_ROOT` to the extracted shared-runtime directory when the
launcher is not adjacent to the runtime manifests.

## Security model

The shared-runtime archive contains verified runtime and adapter wheels,
runtime/adapter manifests, and the exact native plugin release artifact. Its
manifest binds the product, version, platform, every file path, size, and
SHA-256. The release publisher also verifies that the nested adapter and native
payloads are byte-identical to their separately published release artifacts.

The installer rejects path traversal, links, multi-link receipts, mismatched
platforms, member drift, and non-portable Windows aliases. The receipt records
the exact managed file paths. Verify ignores unrelated entries while still
failing closed on any managed-path drift; upgrade and uninstall mutate only
those verified managed files, preserve operator-owned entries, and prune a
managed directory only when it is empty. Files are staged beside the target,
and a failed publication restores the prior managed installation.

## Commands

Start with the Core planner. It returns this adapter-owned runbook as the first
next step and does not silently modify the OBS plugin directory:

```console
dcc-mcp-cli install --dcc-type obs
```

After extracting the platform runtime archive, install its exact wheels and
native plugin as one versioned unit. The release-level `SHA256SUMS` must be
verified before extraction; the bundled installer then verifies every nested
file against the archive manifest before any installation command:

```powershell
.\install.ps1 -DryRun
.\install.ps1 -Yes
```

```bash
bash install.sh --dry-run
bash install.sh --yes
```

The runtime entry sets `DCC_MCP_PYTHON_EXECUTABLE` to its own interpreter for
Core-managed Agent skills. Do not persist that generic variable globally on a
mixed-DCC workstation. A missing runtime root, manifest, or compatible adapter
handshake fails closed before the OBS sidecar starts.

Optional PyPI/source installation:

```console
dcc-mcp-obs-install install --plugin-archive <bundle> --sha256 <digest>
dcc-mcp-obs-install upgrade --plugin-archive <bundle> --sha256 <digest>
dcc-mcp-obs-install status
dcc-mcp-obs-install verify
dcc-mcp-obs-install uninstall
```

Every command supports `--plugin-dir` for an explicit operator-owned OBS
plugin location and `--dry-run` for a zero-mutation plan. Each invocation emits
one Install SOP v1 JSON document and uses stable exit families: `0` success,
`10` preflight, `20` acquisition, `30` installation, and `40` verification.
File installation returns `requires_restart`; file-only status/verify returns
`partial`. Both keep `verify.directly_usable=false` with
`LIVE_OBS_VERIFICATION_REQUIRED` until an exact live OBS plugin session is
observed through the sidecar.

The shared-runtime installer resolves the release-bound
`native/dcc-mcp-obs-plugin.zip` and passes its verified digest into the same
Install SOP implementation. It does not introduce a second installation
mechanism.

The default plugin directory follows the OBS platform layout. On Windows it is
`%PROGRAMDATA%\obs-studio\plugins\dcc-mcp-obs`; on macOS and Linux it remains
inside the current user's OBS plugin directory. Use `--plugin-dir` only when
OBS itself is configured to scan a different operator-owned location.

On Linux and macOS, successful filesystem verification is synchronous and
point-in-time. Unprivileged POSIX processes cannot revoke already-open writable
descriptors or pin the managed root name while its operator-owned parent stays
writable. The installer therefore restores its internal verification guard
before returning and publishes `POSIX_REVERIFY_BEFORE_USE` in `next_steps`.
It does not claim a persistent lease: re-run `status` or `verify` immediately
before relying on the installed files. Any subsequent namespace or content
drift fails closed on that follow-up command.

Close OBS before installing, upgrading, or uninstalling a loaded native
plugin. After installation, enable OBS WebSocket, set the password only in
`DCC_MCP_OBS_WEBSOCKET_PASSWORD`, restart OBS, and start the sidecar with the
exact OBS PID. Confirm the registered runtime with:

```console
dcc-mcp-cli wait-ready --dcc-type obs
```
