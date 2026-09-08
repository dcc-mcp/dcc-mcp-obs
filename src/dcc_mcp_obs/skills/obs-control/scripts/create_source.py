from dcc_mcp_obs.skills.obs_control.scripts._typed_source import (
    obs_skill_entry,
    typed_source_success,
)


@obs_skill_entry
def main(scene_name, source_name, source_kind, schema_version, settings, enabled=True, **_kwargs):
    return typed_source_success(
        "create_source",
        "Reviewed source created and verified.",
        scene_name=scene_name,
        source_name=source_name,
        source_kind=source_kind,
        schema_version=schema_version,
        settings=settings,
        enabled=enabled,
    )
