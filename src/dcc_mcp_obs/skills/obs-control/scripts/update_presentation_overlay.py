from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(
    scene_name,
    title,
    rows,
    logo_path="",
    visible=True,
    source_name="DCC-MCP Presentation",
    **_kwargs,
):
    with obs_bridge() as bridge:
        return skill_success(
            "Presentation overlay content updated and verified.",
            **bridge.update_presentation_overlay(
                scene_name=scene_name,
                source_name=source_name,
                title=title,
                rows=rows,
                logo_path=logo_path,
                visible=visible,
            ),
        )
