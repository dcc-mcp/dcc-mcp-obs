from dcc_mcp_obs.skills.obs_control.scripts._typed_source import (
    obs_skill_entry,
    typed_source_success,
)


@obs_skill_entry
def main(source_name, source_kind, schema_version, settings, **_kwargs):
    return typed_source_success(
        "set_input_settings",
        "Reviewed input settings updated and verified.",
        source_name=source_name,
        source_kind=source_kind,
        schema_version=schema_version,
        settings=settings,
    )
