from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from dcc_mcp_obs import __version__
from dcc_mcp_obs.bridge import BridgeError, ObsControlBridge
from dcc_mcp_obs.install_cli import RECEIPT_NAME, run

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
SEMVER = r"[0-9]+\.[0-9]+\.[0-9]+"
RELEASE_VERSION_SURFACES = {
    "pyproject.toml",
    "buildspec.json",
    "native/src/plugin-main.cpp",
    "src/dcc_mcp_obs/__version__.py",
    "src/dcc_mcp_obs/skills/obs-control/SKILL.md",
}
RELEASE_VERSION_ENTRIES = (
    "pyproject.toml",
    {"type": "json", "path": "buildspec.json", "jsonpath": "$.version"},
    "native/src/plugin-main.cpp",
    "src/dcc_mcp_obs/__version__.py",
    "src/dcc_mcp_obs/skills/obs-control/SKILL.md",
)


def _copy_contract_inputs(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    shutil.copytree(ROOT / "tests", root / "tests")
    shutil.copy2(ROOT / "release-please-config.json", root / "release-please-config.json")
    for relative in RELEASE_VERSION_SURFACES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return root


@pytest.fixture
def contract_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _copy_contract_inputs(tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", root)
    return root


def _replace_once(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    source = path.read_text(encoding="utf-8")
    assert source.count(old) == 1
    path.write_text(source.replace(old, new), encoding="utf-8")


def _replace_function_body(root: Path, relative: str, name: str, body: str) -> None:
    path = root / relative
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = _function(tree, name)
    lines = source.splitlines(keepends=True)
    body_start = function.body[0].lineno - 1
    assert function.end_lineno is not None
    path.write_text(
        "".join(lines[:body_start]) + body + "".join(lines[function.end_lineno :]),
        encoding="utf-8",
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        assert key not in result
        result[key] = value
    return result


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _assigned_value(nodes: list[ast.stmt], name: str) -> ast.expr:
    matches = [
        node.value
        for node in nodes
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    assert len(matches) == 1
    return matches[0]


def _dict_value(node: ast.expr, key: str) -> ast.expr:
    assert isinstance(node, ast.Dict)
    matches = [
        value
        for candidate, value in zip(node.keys, node.values, strict=True)
        if isinstance(candidate, ast.Constant) and candidate.value == key
    ]
    assert len(matches) == 1
    return matches[0]


def _static_string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _assert_name(node: ast.expr, name: str) -> None:
    assert isinstance(node, ast.Name)
    assert node.id == name


def _assert_attribute(node: ast.expr, owner: str, attribute: str) -> None:
    assert isinstance(node, ast.Attribute)
    assert isinstance(node.value, ast.Name)
    assert node.value.id == owner
    assert node.attr == attribute


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def _parametrize_rows(function: ast.FunctionDef, names: tuple[str, ...]) -> list[ast.expr]:
    matches = [
        decorator
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and _qualified_name(decorator.func) == "pytest.mark.parametrize"
        and len(decorator.args) == 2
        and decorator.keywords == []
        and isinstance(decorator.args[0], ast.Tuple)
        and tuple(_static_string(item) for item in decorator.args[0].elts) == names
    ]
    assert len(matches) == 1
    rows = matches[0].args[1]
    assert isinstance(rows, ast.List)
    return rows.elts


def _assert_ast_body(actual: list[ast.stmt], expected_source: str) -> None:
    expected = ast.parse(expected_source).body
    assert ast.dump(ast.Module(body=actual, type_ignores=[])) == ast.dump(
        ast.Module(body=expected, type_ignores=[])
    )


def _assert_foreign_bridge_behavior(function: ast.FunctionDef) -> None:
    assert function.decorator_list == []
    _assert_ast_body(
        function.body,
        'status = {**IDENTITY, "pluginVersion": "999.0.0", "ready": True}\n'
        'with pytest.raises(BridgeError, match="OBS_PLUGIN_VERSION_UNSUPPORTED"):\n'
        "    ObsControlBridge(FakeTransport([status]), expected_pid=4242)\n",
    )


def _assert_foreign_receipt_behavior(function: ast.FunctionDef) -> None:
    assert len(function.decorator_list) == 2
    command_decorator = function.decorator_list[0]
    assert isinstance(command_decorator, ast.Call)
    assert _qualified_name(command_decorator.func) == "pytest.mark.parametrize"
    assert len(command_decorator.args) == 2
    assert command_decorator.keywords == []
    assert _static_string(command_decorator.args[0]) == "command"
    command_rows = command_decorator.args[1]
    assert isinstance(command_rows, ast.List)
    assert tuple(_static_string(row) for row in command_rows.elts) == (
        "status",
        "upgrade",
        "uninstall",
    )
    mutation_decorator = function.decorator_list[1]
    assert isinstance(mutation_decorator, ast.Call)
    assert _qualified_name(mutation_decorator.func) == "pytest.mark.parametrize"
    assert len(mutation_decorator.args) == 2
    assert mutation_decorator.keywords == []
    mutation_names = mutation_decorator.args[0]
    assert isinstance(mutation_names, ast.Tuple)
    assert tuple(_static_string(item) for item in mutation_names.elts) == (
        "mutation",
        "value",
    )
    _parametrize_rows(function, ("mutation", "value"))
    _assert_ast_body(
        function.body[-4:],
        "code, report = run(args)\n"
        "assert code == 40\n"
        'assert report["verify"]["failure_reason"] == "OBS_RECEIPT_INVALID"\n'
        'assert (target / "bin" / "dcc-mcp-obs.plugin").read_bytes() == original\n',
    )


def test_contract_rejects_foreign_bridge_comment_decoy(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_bridge.py",
        '"pluginVersion": "999.0.0"',
        '"pluginVersion": __version__',
    )
    _replace_once(
        contract_root,
        "tests/test_bridge.py",
        "def test_incompatible_native_plugin_version_is_not_ready() -> None:",
        "def test_incompatible_native_plugin_version_is_not_ready() -> None:\n"
        '    # decoy: "pluginVersion": "999.0.0"',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_foreign_receipt_comment_decoy(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_install_lifecycle.py",
        '("version", "999.0.0")',
        '("version", __version__)',
    )
    _replace_once(
        contract_root,
        "tests/test_install_lifecycle.py",
        "def test_lifecycle_rejects_noncanonical_receipt_envelope(",
        "def test_lifecycle_rejects_noncanonical_receipt_envelope(\n"
        '    # decoy: ("version", "999.0.0")',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_nonexecuting_foreign_bridge_test(contract_root: Path) -> None:
    _replace_function_body(
        contract_root,
        "tests/test_bridge.py",
        "test_incompatible_native_plugin_version_is_not_ready",
        '    status = {**IDENTITY, "pluginVersion": "999.0.0", "ready": True}\n'
        '    assert status["pluginVersion"] == "999.0.0"\n\n',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_nonexecuting_foreign_receipt_test(contract_root: Path) -> None:
    _replace_function_body(
        contract_root,
        "tests/test_install_lifecycle.py",
        "test_lifecycle_rejects_noncanonical_receipt_envelope",
        "    del tmp_path, command\n    assert mutation and value is not None\n\n",
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_skipped_foreign_bridge_test(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_bridge.py",
        "def test_incompatible_native_plugin_version_is_not_ready() -> None:",
        '@pytest.mark.skip(reason="decoy")\n'
        "def test_incompatible_native_plugin_version_is_not_ready() -> None:",
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_skipped_foreign_receipt_test(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_install_lifecycle.py",
        "def test_lifecycle_rejects_noncanonical_receipt_envelope(",
        '@pytest.mark.skip(reason="decoy")\n'
        "def test_lifecycle_rejects_noncanonical_receipt_envelope(",
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_extra_receipt_parametrize_option(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_install_lifecycle.py",
        '        ("unexpected", "not-owned"),\n    ],\n)',
        '        ("unexpected", "not-owned"),\n    ],\n    None,\n)',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_extra_receipt_parametrize_keyword(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_install_lifecycle.py",
        '        ("unexpected", "not-owned"),\n    ],\n)',
        '        ("unexpected", "not-owned"),\n    ],\n    ids=None,\n)',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_receipt_parametrize_alias(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_install_lifecycle.py",
        '@pytest.mark.parametrize(\n    ("mutation", "value"),',
        '@parametrize(\n    ("mutation", "value"),',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


@pytest.mark.parametrize(
    ("relative", "function_name"),
    [
        ("tests/test_bridge.py", "test_incompatible_native_plugin_version_is_not_ready"),
        (
            "tests/test_install_lifecycle.py",
            "test_lifecycle_rejects_noncanonical_receipt_envelope",
        ),
    ],
)
def test_contract_rejects_missing_foreign_behavior_test(
    contract_root: Path, relative: str, function_name: str
) -> None:
    _replace_once(
        contract_root,
        relative,
        f"def {function_name}(",
        f"def decoy_{function_name}(",
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


@pytest.mark.parametrize(
    ("relative", "function_name"),
    [
        ("tests/test_bridge.py", "test_incompatible_native_plugin_version_is_not_ready"),
        (
            "tests/test_install_lifecycle.py",
            "test_lifecycle_rejects_noncanonical_receipt_envelope",
        ),
    ],
)
def test_contract_rejects_xfailed_foreign_behavior_test(
    contract_root: Path, relative: str, function_name: str
) -> None:
    _replace_once(
        contract_root,
        relative,
        f"def {function_name}(",
        f'@pytest.mark.xfail(reason="decoy")\ndef {function_name}(',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_foreign_versions_are_rejected_by_production_interfaces(tmp_path: Path) -> None:
    class SingleResponseTransport:
        def vendor_request(
            self,
            request_type: str,
            data: Mapping[str, object],
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            del request_type, data, deadline
            return {
                "instanceId": "obs-instance-1",
                "pluginVersion": "999.0.0",
                "obsVersion": "31.1.1",
                "hostPid": 4242,
                "eventSequence": 7,
                "ok": True,
                "ready": True,
            }

    with pytest.raises(BridgeError, match="OBS_PLUGIN_VERSION_UNSUPPORTED"):
        ObsControlBridge(SingleResponseTransport(), expected_pid=4242)

    payload = b"native-plugin-binary"
    platform = (
        "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    )
    manifest = {
        "schema_version": 1,
        "product": "dcc-mcp-obs",
        "version": __version__,
        "platform": platform,
        "files": [
            {
                "source": "payload/dcc-mcp-obs.plugin",
                "target": "bin/dcc-mcp-obs.plugin",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    archive = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("dcc-mcp-obs-plugin.json", json.dumps(manifest))
        package.writestr("payload/dcc-mcp-obs.plugin", payload)
    target = tmp_path / "installed"
    code, _report = run(
        [
            "install",
            "--plugin-archive",
            str(archive),
            "--sha256",
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            "--plugin-dir",
            str(target),
        ]
    )
    assert code == 0
    receipt_path = target / RECEIPT_NAME
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["version"] = "999.0.0"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    code, report = run(["status", "--plugin-dir", str(target)])

    assert code == 40
    assert report["verify"]["failure_reason"] == "OBS_RECEIPT_INVALID"


def test_contract_rejects_split_bridge_fixture_version(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_bridge.py",
        '"pluginVersion": __version__',
        '"pluginVersion": "0." + "1.0"',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_split_bundle_fixture_version(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_plugin_bundle.py",
        'create_bundle(root, "windows", __version__, output)',
        'create_bundle(root, "windows", "0." + "1.0", output)',
    )

    with pytest.raises(AssertionError):
        test_current_version_fixtures_do_not_embed_release_versions()


def test_contract_rejects_duplicate_release_surface(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "release-please-config.json",
        '        "pyproject.toml",',
        '        "pyproject.toml",\n        "pyproject.toml",',
    )

    with pytest.raises(AssertionError):
        test_release_owned_version_surfaces_match_canonical_package_version()


def test_contract_rejects_release_updater_type_downgrade(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "release-please-config.json",
        '{"type": "json", "path": "buildspec.json", "jsonpath": "$.version"}',
        '"buildspec.json"',
    )

    with pytest.raises(AssertionError):
        test_release_owned_version_surfaces_match_canonical_package_version()


def test_contract_rejects_duplicate_release_config_key(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "release-please-config.json",
        '      "extra-files": [',
        '      "extra-files": [],\n      "extra-files": [',
    )

    with pytest.raises(AssertionError):
        test_release_owned_version_surfaces_match_canonical_package_version()


def test_contract_ignores_comments_and_nonexecuting_strings(contract_root: Path) -> None:
    _replace_once(
        contract_root,
        "tests/test_bridge.py",
        "from __future__ import annotations",
        "from __future__ import annotations\n\n"
        '# inert: "pluginVersion": "0.1.0"\n'
        'INERT_VERSION_TEXT = \'"pluginVersion": "0.1.0"\'',
    )

    test_current_version_fixtures_do_not_embed_release_versions()


def test_current_version_fixtures_do_not_embed_release_versions() -> None:
    plugin_fixture_specs = [
        ("tests/test_bridge.py", None, "IDENTITY", "name"),
        ("tests/test_dispatcher.py", None, "IDENTITY", "name"),
        ("tests/test_mcp_integration.py", None, "IDENTITY", "name"),
        (
            "tests/test_skill_contract.py",
            "test_every_skill_output_schema_accepts_the_real_core_success_envelope",
            "identity",
            "name",
        ),
        ("tests/verify_installed_skill_contract.py", "main", "identity", "attribute"),
    ]
    parsed: dict[str, ast.Module] = {}
    for relative, function_name, assignment_name, reference_kind in plugin_fixture_specs:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        parsed[relative] = tree
        nodes = tree.body if function_name is None else _function(tree, function_name).body
        version = _dict_value(_assigned_value(nodes, assignment_name), "pluginVersion")
        if reference_kind == "name":
            _assert_name(version, "__version__")
        else:
            _assert_attribute(version, "dcc_mcp_obs", "__version__")

    bridge_function = _function(
        parsed["tests/test_bridge.py"], "test_incompatible_native_plugin_version_is_not_ready"
    )
    _assert_foreign_bridge_behavior(bridge_function)
    foreign_version = _dict_value(_assigned_value(bridge_function.body, "status"), "pluginVersion")
    assert isinstance(foreign_version, ast.Constant)
    assert foreign_version.value == "999.0.0"

    for relative, tree in parsed.items():
        for dictionary in (node for node in ast.walk(tree) if isinstance(node, ast.Dict)):
            for key, value in zip(dictionary.keys, dictionary.values, strict=True):
                if _static_string(key) != "pluginVersion" or value is foreign_version:
                    continue
                static_value = _static_string(value)
                assert static_value is None or re.fullmatch(SEMVER, static_value) is None, relative

    lifecycle_path = "tests/test_install_lifecycle.py"
    lifecycle = ast.parse(
        (ROOT / lifecycle_path).read_text(encoding="utf-8"), filename=lifecycle_path
    )
    manifest_version = _dict_value(
        _assigned_value(_function(lifecycle, "_bundle").body, "manifest"), "version"
    )
    _assert_name(manifest_version, "__version__")
    envelope_test = _function(lifecycle, "test_lifecycle_rejects_noncanonical_receipt_envelope")
    _assert_foreign_receipt_behavior(envelope_test)
    foreign_rows = [
        row
        for row in _parametrize_rows(envelope_test, ("mutation", "value"))
        if isinstance(row, ast.Tuple)
        and len(row.elts) == 2
        and _static_string(row.elts[0]) == "version"
    ]
    assert len(foreign_rows) == 1
    assert isinstance(foreign_rows[0].elts[1], ast.Constant)
    assert foreign_rows[0].elts[1].value == "999.0.0"

    bundle_path = "tests/test_plugin_bundle.py"
    bundle = ast.parse((ROOT / bundle_path).read_text(encoding="utf-8"), filename=bundle_path)
    bundle_test = _function(bundle, "test_windows_plugin_bundle_is_install_contract_compatible")
    bundle_calls = [
        node
        for node in ast.walk(bundle_test)
        if isinstance(node, ast.Call) and _qualified_name(node.func) == "create_bundle"
    ]
    assert len(bundle_calls) == 1
    assert len(bundle_calls[0].args) == 4
    assert bundle_calls[0].keywords == []
    _assert_name(bundle_calls[0].args[2], "__version__")


def test_release_owned_version_surfaces_match_canonical_package_version() -> None:
    release_config = _load_json(ROOT / "release-please-config.json")
    assert isinstance(release_config, dict)
    configured = release_config["packages"]["."]["extra-files"]
    assert tuple(configured) == RELEASE_VERSION_ENTRIES

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    buildspec = _load_json(ROOT / "buildspec.json")
    assert isinstance(buildspec, dict)
    native = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    native_match = re.search(
        r'constexpr char kPluginVersion\[\] = "([^"]+)"; // x-release-please-version',
        native,
    )
    assert native_match is not None
    skill = (ROOT / "src" / "dcc_mcp_obs" / "skills" / "obs-control" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    skill_metadata = yaml.safe_load(skill.split("---", maxsplit=2)[1])

    versions = {
        "pyproject.toml": pyproject["project"]["version"],
        "buildspec.json": buildspec["version"],
        "native/src/plugin-main.cpp": native_match.group(1),
        "src/dcc_mcp_obs/__version__.py": __version__,
        "src/dcc_mcp_obs/skills/obs-control/SKILL.md": skill_metadata["metadata"]["dcc-mcp"][
            "version"
        ],
    }
    assert versions == dict.fromkeys(RELEASE_VERSION_SURFACES, __version__)
