from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(
    scene_name,
    event_kind,
    source_name="DCC-MCP Agent Input",
    keys=None,
    mouse_button="none",
    wheel_direction="none",
    character_count=0,
    duration_ms=1600,
    agent_id="agent",
    **_kwargs,
):
    with obs_bridge() as bridge:
        return skill_success(
            "Privacy-safe Agent input activity displayed and verified.",
            **bridge.emit_agent_input_activity(
                scene_name=scene_name,
                source_name=source_name,
                event_kind=event_kind,
                keys=keys,
                mouse_button=mouse_button,
                wheel_direction=wheel_direction,
                character_count=character_count,
                duration_ms=duration_ms,
                agent_id=agent_id,
            ),
        )
