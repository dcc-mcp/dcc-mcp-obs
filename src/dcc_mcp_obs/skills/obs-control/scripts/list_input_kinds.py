from dcc_mcp_core.skill import skill_entry

from dcc_mcp_obs.skills.obs_control.scripts._typed_source import typed_source_success


@skill_entry
def main(**_kwargs):
    return typed_source_success("list_input_kinds", "Reviewed input kinds listed.")
