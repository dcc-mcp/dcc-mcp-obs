from dcc_mcp_core.skill import skill_entry

from dcc_mcp_obs.skills.obs_control.scripts._typed_source import typed_source_success


@skill_entry
def main(source_name, filter_name, filter_kind, schema_version, settings, **_kwargs):
    return typed_source_success(
        "set_filter_settings",
        "Filter settings updated and verified.",
        source_name=source_name,
        filter_name=filter_name,
        filter_kind=filter_kind,
        schema_version=schema_version,
        settings=settings,
    )
