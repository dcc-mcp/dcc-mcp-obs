from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(scene_name, scene_item_id, **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "Scene item removed and verified.",
            **bridge.remove_scene_item(scene_name=scene_name, scene_item_id=scene_item_id),
        )
