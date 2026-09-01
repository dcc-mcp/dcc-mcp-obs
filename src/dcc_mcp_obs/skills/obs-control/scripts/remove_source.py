from dcc_mcp_core.skill import skill_entry

from dcc_mcp_obs.skills.obs_control.scripts._typed_source import typed_source_success


@skill_entry
def main(source_name, **_kwargs):
    return typed_source_success(
        "remove_source", "Source removed and verified.", source_name=source_name
    )
