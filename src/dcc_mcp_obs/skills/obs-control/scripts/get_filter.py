from dcc_mcp_obs.skills.obs_control.scripts._typed_source import (
    obs_skill_entry,
    typed_source_success,
)


@obs_skill_entry
def main(source_name, filter_name, filter_kind, schema_version="1.0", **_kwargs):
    return typed_source_success(
        "get_filter",
        "Reviewed filter read.",
        source_name=source_name,
        filter_name=filter_name,
        filter_kind=filter_kind,
        schema_version=schema_version,
    )
