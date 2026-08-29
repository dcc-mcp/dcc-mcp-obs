from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RELEASE_ACTION = (
    "googleapis/release-please-action@5c625bfb5d1ff62eadeeb3772007f7f66fdcf071"
)
DECOY_ACTION = "attacker/decoy-action@0123456789abcdef0123456789abcdef01234567"
YAML_MERGE_TAG = "tag:yaml.org,2002:merge"


class _StrictWorkflowLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[object, object]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "expected a mapping node",
                node.start_mark,
            )

        keys: set[object] = set()
        for key_node, _ in node.value:
            if key_node.tag == YAML_MERGE_TAG:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "merge keys are forbidden",
                    key_node.start_mark,
                )
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in keys
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "mapping keys must be hashable",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate mapping key: {key!r}",
                    key_node.start_mark,
                )
            keys.add(key)

        return super().construct_mapping(node, deep=deep)


def _strict_workflow_load(workflow: str) -> object:
    for token in yaml.scan(workflow):
        if isinstance(token, (AnchorToken, AliasToken)):
            raise ConstructorError(
                "while scanning a workflow",
                token.start_mark,
                "anchors and aliases are forbidden",
                token.start_mark,
            )
    return yaml.load(workflow, Loader=_StrictWorkflowLoader)


def _executable_action_uses(workflow: str) -> list[str]:
    document = _strict_workflow_load(workflow)
    assert isinstance(document, dict)

    jobs = document.get("jobs")
    assert isinstance(jobs, dict) and jobs

    action_uses: list[str] = []
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps")
        assert isinstance(steps, list)
        for step in steps:
            assert isinstance(step, dict)
            if "uses" in step:
                uses = step["uses"]
                assert isinstance(uses, str)
                action_uses.append(uses)
    return action_uses


def _assert_release_action_contract(workflow: str) -> None:
    action_uses = _executable_action_uses(workflow)
    assert action_uses == [EXPECTED_RELEASE_ACTION]

    repository, ref = action_uses[0].rsplit("@", maxsplit=1)
    assert repository == "googleapis/release-please-action"
    assert re.fullmatch(r"[0-9a-f]{40}", ref)


def _workflow_with_steps(steps: str, *, suffix: str = "") -> str:
    return f"""name: Contract fixture
on: push
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
{steps}
{suffix}"""


def test_windows_build_script_preset_exists() -> None:
    presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
    build_presets = {preset["name"]: preset for preset in presets["buildPresets"]}

    assert build_presets["windows-x64"]["configurePreset"] == "windows-x64"
    assert build_presets["windows-ci-x64"]["configurePreset"] == "windows-ci-x64"

    script = (ROOT / ".github" / "scripts" / "Build-Windows.ps1").read_text(encoding="utf-8")
    assert '"windows-${Target}"' in script


def test_native_macos_jobs_use_obs_compatible_runner() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    native_ci = ci.split("\n  native:\n", maxsplit=1)[1]

    assert "- os: macos-15\n            target: universal" in native_ci
    assert "- os: macos-15\n            target: universal" in release

    compiler_contract = (ROOT / "cmake" / "macos" / "compilerconfig.cmake").read_text(
        encoding="utf-8"
    )
    assert "set(obs_macos_minimum_sdk 15.0)" in compiler_contract
    assert "set(obs_macos_minimum_xcode 16.0)" in compiler_contract


def test_native_matrix_runs_host_independent_contract_tests() -> None:
    """Keep issue-6 native safety checks on every supported runner."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    native_ci = workflow.split("\n  native:\n", maxsplit=1)[1]

    for runner in ("windows-2022", "macos-15", "ubuntu-24.04"):
        assert f"- os: {runner}" in native_ci

    assert "name: Run native contract tests" in native_ci
    assert "cmake -S native/tests -B native-tests-build" in native_ci
    assert "ctest --test-dir native-tests-build" in native_ci

    contract_cmake = (ROOT / "native" / "tests" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "find_package(Threads REQUIRED)" in contract_cmake
    assert "target_link_libraries(ui-task-gate-contract PRIVATE Threads::Threads)" in contract_cmake
    assert "add_executable(ui-task-gate-contract ui-task-gate-test.cpp)" in contract_cmake
    assert "add_test(NAME ui-task-gate-contract COMMAND ui-task-gate-contract)" in contract_cmake


def test_release_please_workflow_uses_official_immutable_v4_4_1_commit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-please.yml").read_text(encoding="utf-8")
    _assert_release_action_contract(workflow)


@pytest.mark.parametrize(
    "inert_data",
    [
        "      # - uses: attacker/decoy-action@0123456789abcdef0123456789abcdef01234567\n",
        """      - name: inert block scalar
        run: |
          - uses: attacker/decoy-action@0123456789abcdef0123456789abcdef01234567
""",
        """      - name: inert string
        env:
          PAYLOAD: "- uses: attacker/decoy-action@0123456789abcdef0123456789abcdef01234567"
        run: echo safe
""",
    ],
)
def test_release_action_contract_ignores_inert_step_data(inert_data: str) -> None:
    steps = f"      - uses: {EXPECTED_RELEASE_ACTION}\n{inert_data}"
    _assert_release_action_contract(_workflow_with_steps(steps))


def test_release_action_contract_ignores_non_step_data() -> None:
    workflow = _workflow_with_steps(
        f"      - uses: {EXPECTED_RELEASE_ACTION}",
        suffix=f"metadata:\n  uses: {DECOY_ACTION}\n",
    )
    _assert_release_action_contract(workflow)


def test_release_action_contract_accepts_quoted_merge_text_without_aliases() -> None:
    workflow = _workflow_with_steps(
        f"      - uses: {EXPECTED_RELEASE_ACTION}",
        suffix='metadata:\n  "<<": ordinary string\n',
    )
    _assert_release_action_contract(workflow)


@pytest.mark.parametrize(
    "steps",
    [
        "      - run: echo no-action",
        """      - name: inert official text
        run: |
          - uses: googleapis/release-please-action@5c625bfb5d1ff62eadeeb3772007f7f66fdcf071""",
        f"""      - uses: {EXPECTED_RELEASE_ACTION}
      - uses: attacker/decoy-action@0123456789abcdef0123456789abcdef01234567""",
        f"""      - uses: {EXPECTED_RELEASE_ACTION}
      - uses: {EXPECTED_RELEASE_ACTION}""",
        "      - uses: attacker/release-please-action@5c625bfb5d1ff62eadeeb3772007f7f66fdcf071",
        "      - uses: googleapis/release-please-action@v4.4.1",
    ],
)
def test_release_action_contract_rejects_wrong_executable_actions(steps: str) -> None:
    with pytest.raises(AssertionError):
        _assert_release_action_contract(_workflow_with_steps(steps))


@pytest.mark.parametrize(
    "workflow",
    [
        "- not-a-workflow",
        "jobs: []",
        "jobs:\n  release: invalid",
        "jobs:\n  release:\n    steps: {}",
        "jobs:\n  release:\n    steps:\n      - invalid",
        "jobs:\n  release:\n    steps:\n      - uses: []",
        "jobs: [",
    ],
)
def test_release_action_contract_rejects_malformed_shapes(workflow: str) -> None:
    with pytest.raises((AssertionError, yaml.YAMLError)):
        _executable_action_uses(workflow)


@pytest.mark.parametrize(
    "workflow",
    [
        f"""jobs:
  attacker:
    steps:
      - uses: {DECOY_ACTION}
jobs:
  release:
    steps:
      - uses: {EXPECTED_RELEASE_ACTION}
""",
        f"""jobs:
  release:
    steps:
      - uses: {DECOY_ACTION}
    steps:
      - uses: {EXPECTED_RELEASE_ACTION}
""",
        f"""jobs:
  release:
    steps:
      - uses: {DECOY_ACTION}
        uses: {EXPECTED_RELEASE_ACTION}
""",
        f"""defaults: &defaults
  uses: {DECOY_ACTION}
jobs:
  release:
    steps:
      - <<: *defaults
        uses: {EXPECTED_RELEASE_ACTION}
""",
        f"""official: &official {EXPECTED_RELEASE_ACTION}
jobs:
  release:
    steps:
      - uses: *official
""",
        f"""jobs:
  release:
    steps:
      - name: nested duplicate
        env:
          VALUE: first
          VALUE: second
        uses: {EXPECTED_RELEASE_ACTION}
""",
    ],
)
def test_release_action_contract_rejects_ambiguous_yaml(workflow: str) -> None:
    with pytest.raises(ConstructorError):
        _assert_release_action_contract(workflow)


@pytest.mark.parametrize(
    "workflow",
    [
        "jobs: !unsupported {}",
        "%YAML invalid\n---\njobs: {}",
    ],
)
def test_release_action_contract_rejects_malformed_tags_and_directives(workflow: str) -> None:
    with pytest.raises(yaml.YAMLError):
        _assert_release_action_contract(workflow)
