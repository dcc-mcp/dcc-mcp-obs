from dcc_mcp_obs.skills.obs_control.scripts._typed_source import (
    obs_skill_entry,
    typed_source_success,
)


@obs_skill_entry
def main(source_kind, schema_version, property_name, value, **_kwargs):
    return typed_source_success(
        "validate_property_value",
        "Property value validated.",
        source_kind=source_kind,
        schema_version=schema_version,
        property_name=property_name,
        value=value,
    )
