from dcc_mcp_core.skill import skill_entry

from dcc_mcp_obs.skills.obs_control.scripts._typed_source import typed_source_success


@skill_entry
def main(source_kind, schema_version="1.0", **_kwargs):
    return typed_source_success(
        "describe_properties",
        "Reviewed properties described.",
        source_kind=source_kind,
        schema_version=schema_version,
    )
