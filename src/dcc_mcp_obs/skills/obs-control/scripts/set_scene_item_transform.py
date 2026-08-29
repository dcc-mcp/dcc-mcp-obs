from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(scene_name, scene_item_id, position=None, scale=None, rotation=None, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Scene item transform updated and verified.",
            **bridge.set_scene_item_transform(
                scene_name=scene_name,
                scene_item_id=scene_item_id,
                position=position,
                scale=scale,
                rotation=rotation,
            ),
        )
