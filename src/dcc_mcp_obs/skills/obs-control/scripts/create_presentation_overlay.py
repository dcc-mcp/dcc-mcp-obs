from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(
    scene_name,
    source_name="DCC-MCP Presentation",
    anchor="top_right",
    opacity=88,
    margin=48,
    width=520,
    **_kwargs,
):
    with obs_bridge() as bridge:
        return skill_success(
            "Presentation overlay created and verified.",
            **bridge.create_presentation_overlay(
                scene_name=scene_name,
                source_name=source_name,
                anchor=anchor,
                opacity=opacity,
                margin=margin,
                width=width,
            ),
        )
