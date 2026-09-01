from dcc_mcp_core.skill import skill_entry

from dcc_mcp_obs.skills.obs_control.scripts._typed_source import typed_source_success


@skill_entry
def main(source_name, filter_name, enabled, **_kwargs):
    return typed_source_success(
        "set_filter_enabled",
        "Filter enabled state updated and verified.",
        source_name=source_name,
        filter_name=filter_name,
        enabled=enabled,
    )
