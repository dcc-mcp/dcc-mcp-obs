# Architecture

```text
Agent -> DCC-MCP Core/Gateway -> Python sidecar
      -> authenticated OBS WebSocket 5.x CallVendorRequest
      -> native dcc-mcp-obs plugin -> bounded OBS UI task
      -> libobs / OBS frontend API -> event + typed readback
```

The native plugin creates a fresh instance ID at load, reports the host PID and
OBS/plugin versions on every response, and increments an event sequence from
OBS frontend callbacks. The sidecar pins this identity at startup and rejects
any drift. Native work is queued to the OBS UI task queue with a five-second
wait bound. A timed-out request cannot access the WebSocket response object.

Recording mutations return only acceptance from the native plugin. The sidecar
then performs bounded, separate `GetRecordingStatus` requests and verifies the
requested state. This separation prevents command delivery from being
misreported as an observed postcondition while allowing OBS frontend events to
settle inside the enclosing Core job.

The full product surface is partitioned into capability domains in
`contracts/obs-capabilities-v1.json`. Follow-up slices add reviewed request and
response schemas; they do not add a generic vendor-request bridge.
