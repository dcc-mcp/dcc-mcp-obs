from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(
    scene_name,
    source_name="DCC-MCP Agent Input",
    anchor="bottom_right",
    **_kwargs,
):
    with obs_bridge() as bridge:
        return skill_success(
            "Built-in OBS Agent input overlay attached and verified.",
            **bridge.create_agent_input_overlay(
                scene_name=scene_name,
                source_name=source_name,
                anchor=anchor,
            ),
        )
