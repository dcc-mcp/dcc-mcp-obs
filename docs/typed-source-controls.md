# Typed source controls

Issue #5 is exposed through native libobs operations on OBS's UI thread. The
sidecar accepts only named, typed requests; it never forwards raw OBS requests,
arbitrary settings, scripts, URLs, or filesystem paths.

## Reviewed settings schema

Schema version `1.0` contains two deliberately small public setting contracts:

- `color_source_v3`: integer `width` and `height` from 1 through 8192, plus an
  unsigned 32-bit integer `color`.
- `gain_filter`: finite numeric `db` from -30 through 30.

Unknown kinds, versions, fields, types, and out-of-range values fail before a
mutation is submitted. Input settings and gain-filter settings may not contain
additional properties.

## Exact object binding

Source and filter operations use exact discovered names. Creation is scoped to
one exact scene; duplicate names fail closed. Rename and removal apply only to
input sources. Filter changes are limited to the reviewed gain-filter kind.
Every mutation is bounded by a caller deadline and either returns verified
typed readback or a stable redacted error.

Audio setters expose only linear volume from 0 through 20, boolean mute, and
`none`, `monitor_only`, or `monitor_and_output`. Media transport operates only
on sources carrying libobs's controllable-media flag. Seek is bounded to a
non-negative millisecond cursor no greater than one day.

## Verification boundary

Unit and protocol tests verify rejection before transport, exact-instance
identity, mutation annotations, bounded reconciliation, and Skill schema
parity. Native builds verify the libobs implementation on Windows, macOS, and
Linux. A real OBS readback remains the acceptance boundary for host-specific
source/filter availability and actual media state transitions.
