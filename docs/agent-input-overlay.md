# Built-in Agent input overlay

`dcc-mcp-obs` registers `dcc_mcp_agent_input_overlay` as a native OBS source.
It gives recorded Agent demonstrations a consistent DCC-MCP keyboard and mouse
activity cue without installing a separate OBS plugin or observing global
input.

## Privacy contract

The source renders only an event explicitly emitted by the Agent:

- an allowlisted shortcut of up to four keys;
- one mouse button;
- one wheel direction; or
- a character count for typing activity.

Every emitted cue also includes a bounded `agent_id`, shown in the badge so a
viewer can tell which Agent is operating without revealing typed content.

The contract has no text or arbitrary-label field. It does not install OS
keyboard or mouse hooks, infer human activity, store keystroke history, or open
another network service. Code, terminals, and game windows remain ordinary OBS
sources; the overlay explains the Agent's next action without duplicating
their content.

## Three-scene recording workflow

1. Discover the exact three game scenes.
2. Call `create_agent_input_overlay` for each scene with a distinct source name
   when different Agents can operate simultaneously.
3. Inspect each game frame, then call `set_agent_input_overlay_layout` with a
   safe edge anchor, 20-100 opacity, and 8-160 pixel margin.
4. Before an Agent action, call `emit_agent_input_activity` with that scene's
   source name and `agent_id`.
5. Call `clear_agent_input_overlay` between sections when an immediate clear is
   preferable to the bounded automatic expiry.
6. Capture and inspect a fresh program frame before starting a long recording.
7. Start one independent recording per game scene. Do not include editor or VS
   Code scenes in the recording plan.

The default `dcc_mcp_dark` theme uses 78% opacity and a 48-pixel margin. Eight
edge anchors are available; the center of the game frame is intentionally not
an allowed anchor. Independent recording clones scale the overlay to at most
40% of the game-window width and 18% of its height.

## Typed tools

- `get_agent_input_overlay`
- `create_agent_input_overlay`
- `set_agent_input_overlay_layout`
- `emit_agent_input_activity`
- `clear_agent_input_overlay`

Create, emit, and clear require the `agent_input_overlay` capability and a
verified typed readback. Source and scene names are exact identities; duplicate
or wrong-kind collisions fail closed.
