from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(scene_name, anchor, opacity, margin, width, source_name="DCC-MCP Presentation", **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Presentation overlay layout updated and verified.",
            **bridge.set_presentation_overlay_layout(
                scene_name=scene_name,
                source_name=source_name,
                anchor=anchor,
                opacity=opacity,
                margin=margin,
                width=width,
            ),
        )
