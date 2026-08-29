from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(scene_name, source_name, source_kind=None, enabled=True, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Scene item created and verified.",
            **bridge.create_scene_item(
                scene_name=scene_name,
                source_name=source_name,
                source_kind=source_kind,
                enabled=enabled,
            ),
        )
