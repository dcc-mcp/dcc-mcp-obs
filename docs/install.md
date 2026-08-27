# Installation and lifecycle

## Security model

The Python package and native plugin are separate release artifacts. Install
only a native bundle from the same GitHub Release as the Python version, and
pass its published SHA-256 to the installer. The bundle contains a manifest
that binds product, version, platform, every target path, and every file hash.

The installer rejects path traversal, links, multi-link receipts, mismatched
platforms, member drift, and non-portable Windows aliases. The receipt records
the exact managed file paths. Verify ignores unrelated entries while still
failing closed on any managed-path drift; upgrade and uninstall mutate only
those verified managed files, preserve operator-owned entries, and prune a
managed directory only when it is empty. Files are staged beside the target,
and a failed publication restores the prior managed installation.

## Commands

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

Close OBS before installing, upgrading, or uninstalling a loaded native
plugin. After installation, enable OBS WebSocket, set the password only in
`DCC_MCP_OBS_WEBSOCKET_PASSWORD`, restart OBS, and start the sidecar with the
exact OBS PID.
