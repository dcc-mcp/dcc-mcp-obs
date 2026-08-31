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

The contract has no text or arbitrary-label field. It does not install OS
keyboard or mouse hooks, infer human activity, store keystroke history, or open
another network service. Code, terminals, and game windows remain ordinary OBS
sources; the overlay explains the Agent's next action without duplicating
their content.

## Three-scene recording workflow

1. Discover the exact three game scenes.
2. Call `create_agent_input_overlay` for each scene with the same default
   source name, `DCC-MCP Agent Input`. OBS reuses the shared source while each
   scene keeps its own positioned scene item.
3. Before an Agent action, call `emit_agent_input_activity` against any attached
   scene. All scenes containing that shared source receive the same bounded cue.
4. To demonstrate code, keep the editor or terminal visible through its normal
   source, emit a shortcut/mouse/typing-count cue, and then run the code.
5. Call `clear_agent_input_overlay` between sections when an immediate clear is
   preferable to the bounded automatic expiry.
6. Capture and inspect a fresh program frame before starting a long recording.

The default `dcc_mcp_dark` theme and bottom-right anchor are deliberately fixed
product choices. `bottom_left` and `bottom_center` are also supported when they
avoid important game UI.

## Typed tools

- `get_agent_input_overlay`
- `create_agent_input_overlay`
- `emit_agent_input_activity`
- `clear_agent_input_overlay`

Create, emit, and clear require the `agent_input_overlay` capability and a
verified typed readback. Source and scene names are exact identities; duplicate
or wrong-kind collisions fail closed.
