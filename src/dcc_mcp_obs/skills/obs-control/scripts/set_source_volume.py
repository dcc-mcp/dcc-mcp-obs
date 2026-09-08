from dcc_mcp_obs.skills.obs_control.scripts._typed_source import (
    obs_skill_entry,
    typed_source_success,
)


@obs_skill_entry
def main(source_name, volume, **_kwargs):
    return typed_source_success(
        "set_source_volume",
        "Source volume updated and verified.",
        source_name=source_name,
        volume=volume,
    )
