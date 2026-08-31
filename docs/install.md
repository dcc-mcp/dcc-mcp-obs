# Installation and lifecycle

## Security model

The recommended standalone archive contains the sidecar's private Python
runtime and the exact native plugin release artifact. Its manifest binds the
product, version, platform, every file path, size, and SHA-256. The release
publisher also verifies that the nested native plugin is byte-identical to the
separately published native artifact. No system Python or separately installed
`dcc-mcp-core` is required.

The installer rejects path traversal, links, multi-link receipts, mismatched
platforms, member drift, and non-portable Windows aliases. The receipt records
the exact managed file paths. Verify ignores unrelated entries while still
failing closed on any managed-path drift; upgrade and uninstall mutate only
those verified managed files, preserve operator-owned entries, and prune a
managed directory only when it is empty. Files are staged beside the target,
and a failed publication restores the prior managed installation.

## Commands

Standalone release bundle:

```console
dcc-mcp-obs install-bundled
dcc-mcp-obs upgrade-bundled
```

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

`install-bundled` and `upgrade-bundled` resolve the adjacent, release-bound
`dcc-mcp-obs-plugin.zip` and pass its manifest digest into the same Install SOP
implementation. They do not introduce a second installation mechanism.

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
exact OBS PID.
