from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_obs.skills.obs_control.scripts._client import obs_bridge


@skill_entry
def main(source_name, image_format="png", **_kwargs):
    with obs_bridge() as bridge:
        return skill_success(
            "The OBS source screenshot was captured.",
            **bridge.capture_source_screenshot(source_name, image_format=image_format),
        )
