# Repository instructions

- Follow SOLID, explicit contracts, and Clean Architecture.
- Use `loonghao <hal.long@outlook.com>` for Git commits.
- Keep the native libobs plugin authoritative for OBS lifecycle and UI-thread work.
- Use typed OBS WebSocket 5.x vendor requests only as the authenticated sidecar transport;
  never expose raw scripting or arbitrary request forwarding.
- Keep passwords operator-owned and never expose them through logs or results.
- Use DCC-CUA/UI Control only as a scoped fallback for unsupported OBS UI actions.
