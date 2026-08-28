from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_delivery", ROOT / "tools/release_delivery.py"
)
assert SPEC is not None and SPEC.loader is not None
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def workflow(name: str) -> dict:
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_release_please_hands_created_release_directly_to_publisher() -> None:
    caller = workflow("release.yml")
    please = workflow("release-please.yml")
    handoff = caller["jobs"].get("identity")
    assert handoff is not None, "GITHUB_TOKEN tag pushes cannot trigger the publisher"
    assert caller["jobs"]["release-please"]["uses"] == "./.github/workflows/release-please.yml"
    assert handoff["needs"] == "release-please"
    assert handoff["if"] == "needs.release-please.outputs.release_created == 'true'"
    assert please["on"].keys() == {"workflow_call"}
    assert caller["on"] == {"push": {"branches": ["main"]}}


def test_publisher_extends_owned_release_instead_of_recreating_it() -> None:
    steps = workflow("release.yml")["jobs"]["publish"]["steps"]
    scripts = "\n".join(step.get("run", "") for step in steps)
    assert "release already exists" not in scripts
    assert "gh release create" not in scripts
    assert "python tools/release_delivery.py upload" in scripts


class FakeGitHub:
    """Offline GitHub API boundary, including create-only asset behavior."""

    def __init__(self, sha: str) -> None:
        self.release = {
            "id": 123,
            "tag_name": "v1.0.0",
            "target_commitish": sha,
            "draft": False,
            "prerelease": False,
            "author": {"login": "github-actions[bot]", "type": "Bot"},
            "assets": [],
        }
        self.ref = {"ref": "refs/tags/v1.0.0", "object": {"type": "commit", "sha": sha}}
        self.writes: list[str] = []
        self.other_release = False

    def get(self, endpoint: str) -> dict:
        if endpoint == "releases/123":
            return copy.deepcopy(self.release)
        if endpoint == "releases/tags/v1.0.0":
            return {"id": 456 if self.other_release else self.release["id"]}
        assert endpoint == "git/ref/tags/v1.0.0"
        return copy.deepcopy(self.ref)

    def upload(self, release_id: str, path: Path) -> None:
        assert release_id == "123"
        assert path.name not in self.writes
        self.writes.append(path.name)
        self.release["assets"].append(
            {
                "name": path.name,
                "state": "uploaded",
                "size": path.stat().st_size,
                "digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
            }
        )


@pytest.fixture
def release_fixture(tmp_path: Path) -> tuple[Path, dict, FakeGitHub]:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    for args in (
        ["init", "-q"],
        ["add", "pyproject.toml"],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    env = {
        "RELEASE_TAG": "v1.0.0",
        "RELEASE_SHA": sha,
        "RELEASE_ID": "123",
        "GITHUB_SHA": sha,
        "GITHUB_REPOSITORY": "dcc-mcp/dcc-mcp-obs",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
    }
    return tmp_path, env, FakeGitHub(sha)


def make_artifacts(root: Path) -> None:
    dist = root / "release-artifacts/python-dist"
    dist.mkdir(parents=True)
    metadata = b"Name: dcc-mcp-obs\nVersion: 1.0.0\n"
    with zipfile.ZipFile(dist / "dcc_mcp_obs-1.0.0-py3-none-any.whl", "w") as archive:
        archive.writestr("dcc_mcp_obs-1.0.0.dist-info/METADATA", metadata)
    with tarfile.open(dist / "dcc_mcp_obs-1.0.0.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("dcc_mcp_obs-1.0.0/PKG-INFO")
        info.size = len(metadata)
        archive.addfile(info, io.BytesIO(metadata))
    for platform in ("windows", "linux", "macos"):
        parent = root / f"release-artifacts/native-{platform}"
        parent.mkdir()
        payload = b"offline-native-fixture"
        manifest = {
            "version": "1.0.0",
            "product": "dcc-mcp-obs",
            "platform": platform,
            "files": [{"source": "payload/plugin", "sha256": hashlib.sha256(payload).hexdigest()}],
        }
        with zipfile.ZipFile(parent / f"dcc-mcp-obs-1.0.0-{platform}.zip", "w") as archive:
            archive.writestr("dcc-mcp-obs-plugin.json", json.dumps(manifest))
            archive.writestr("payload/plugin", payload)
    sums = "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}\n"
        for p in sorted((root / "release-artifacts").rglob("*"))
        if p.is_file()
    )
    (root / "SHA256SUMS").write_text(sums)


def test_created_owned_release_accepts_complete_artifacts(release_fixture: tuple) -> None:
    root, env, api = release_fixture
    make_artifacts(root)
    delivery.deliver("check", root, env, api)
    assert api.writes == []
    delivery.deliver("upload", root, env, api)
    assert len(api.writes) == 6
    assert "SHA256SUMS" in api.writes
    assert len(api.release["assets"]) == 6


@pytest.mark.parametrize(
    "key,value",
    [
        ("RELEASE_TAG", ""),
        ("RELEASE_TAG", "v9.9.9"),
        ("RELEASE_SHA", ""),
        ("RELEASE_SHA", "main"),
        ("RELEASE_SHA", "a" * 40),
        ("RELEASE_ID", ""),
        ("RELEASE_ID", "../123"),
        ("GITHUB_SHA", "b" * 40),
        ("GITHUB_REPOSITORY", "attacker/decoy"),
        ("GITHUB_REF", "refs/tags/v1.0.0"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"),
    ],
)
def test_invalid_handoff_cannot_write(release_fixture: tuple, key: str, value: str) -> None:
    root, env, api = release_fixture
    env[key] = value
    with pytest.raises(ValueError):
        delivery.deliver("upload", root, env, api)
    assert api.writes == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", 456),
        ("tag_name", "v9.9.9"),
        ("target_commitish", "main"),
        ("draft", True),
        ("prerelease", True),
        ("author", {"login": "attacker", "type": "User"}),
        ("assets", [{"name": "foreign.zip"}]),
    ],
)
def test_foreign_or_conflicting_release_cannot_write(
    release_fixture: tuple, key: str, value: object
) -> None:
    root, env, api = release_fixture
    api.release[key] = value
    with pytest.raises(ValueError):
        delivery.deliver("upload", root, env, api)
    assert api.writes == []


@pytest.mark.parametrize("drift", ["tag-commit", "tag-release", "missing-release"])
def test_remote_identity_drift_fails_closed(release_fixture: tuple, drift: str) -> None:
    root, env, api = release_fixture
    if drift == "tag-commit":
        api.ref["object"]["sha"] = "b" * 40
    elif drift == "tag-release":
        api.other_release = True
    else:
        api.release = {}
    with pytest.raises(ValueError):
        delivery.deliver("upload", root, env, api)
    assert api.writes == []


def test_conflicting_artifacts_fail_before_upload(release_fixture: tuple) -> None:
    root, env, api = release_fixture
    make_artifacts(root)
    (root / "release-artifacts/python-dist/foreign.whl").write_bytes(b"foreign")
    with pytest.raises(ValueError, match="artifact set"):
        delivery.deliver("upload", root, env, api)
    assert api.writes == []


def test_handoff_outputs_and_permission_graph_are_exact() -> None:
    caller, please = workflow("release.yml"), workflow("release-please.yml")
    outputs = {
        "release_created": "release_created",
        "tag": "tag_name",
        "sha": "sha",
        "release_id": "id",
    }
    action_job = please["jobs"]["release-please"]
    assert action_job["steps"][0]["id"] == "release"
    assert action_job["steps"][0]["with"] == {"token": "${{ secrets.GITHUB_TOKEN }}"}
    for public, action in outputs.items():
        assert action_job["outputs"][public] == f"${{{{ steps.release.outputs.{action} }}}}"
        assert (
            please["on"]["workflow_call"]["outputs"][public]["value"]
            == f"${{{{ jobs.release-please.outputs.{public} }}}}"
        )
    jobs = caller["jobs"]
    assert set(jobs) == {
        "release-please",
        "identity",
        "python-artifacts",
        "native-artifacts",
        "publish",
    }
    assert caller["permissions"] == {"contents": "read"}
    assert please["permissions"] == {"contents": "read"}
    assert jobs["release-please"]["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert action_job["permissions"] == jobs["release-please"]["permissions"]
    assert jobs["identity"]["needs"] == "release-please"
    for name in ("python-artifacts", "native-artifacts"):
        assert jobs[name]["needs"] == "identity"
        assert "if" not in jobs[name]
    assert jobs["publish"]["needs"] == ["identity", "python-artifacts", "native-artifacts"]
    assert "if" not in jobs["publish"]  # default success() propagates a skipped/failed identity job
    for name in ("identity", "python-artifacts", "native-artifacts"):
        assert jobs[name].get("permissions", caller["permissions"]) == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "write", "id-token": "write"}
    assert jobs["publish"]["environment"] == "pypi"
    for name in ("identity", "python-artifacts", "native-artifacts", "publish"):
        owner = "release-please" if name == "identity" else "identity"
        checkout = next(
            step
            for step in jobs[name]["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout["with"] == {
            "ref": f"${{{{ needs.{owner}.outputs.sha }}}}",
            "persist-credentials": "false",
        }
    publish_steps = jobs["publish"]["steps"]
    upload_index = next(
        i
        for i, step in enumerate(publish_steps)
        if step.get("run") == "python tools/release_delivery.py upload"
    )
    pypi_index = next(
        i
        for i, step in enumerate(publish_steps)
        if step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
    )
    assert upload_index < pypi_index
    assert "skip-existing" not in publish_steps[pypi_index]["with"]


@pytest.mark.parametrize("created", ["", "false", "False", "true"])
def test_no_release_cannot_reach_publisher_or_oidc(created: str) -> None:
    jobs = workflow("release.yml")["jobs"]
    condition = jobs["identity"]["if"]
    assert condition == "needs.release-please.outputs.release_created == 'true'"
    # Interpret the frozen comparison and default success dependencies, not string truthiness.
    states = {"release-please": "success"}
    states["identity"] = "success" if created == "true" else "skipped"
    for name in ("python-artifacts", "native-artifacts", "publish"):
        needs = jobs[name]["needs"]
        needs = [needs] if isinstance(needs, str) else needs
        states[name] = (
            "success" if all(states[parent] == "success" for parent in needs) else "skipped"
        )
    assert (states["publish"] == "success") is (created == "true")


@pytest.mark.parametrize(
    "key,value",
    [
        (None, None),
        ("RELEASE_SHA", ""),
        ("RELEASE_SHA", "a" * 40),
        ("RELEASE_ID", ""),
        ("RELEASE_TAG", "v1.0.0;echo injected"),
    ],
)
def test_real_precheckout_shell_gate(
    release_fixture: tuple, key: str | None, value: str | None
) -> None:
    root, env, _api = release_fixture
    if key is not None:
        env[key] = value
    step = workflow("release.yml")["jobs"]["identity"]["steps"][0]
    assert step["name"] == "Validate caller before checkout"
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    assert bash is not None
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=root,
        env={**os.environ, **env},
        capture_output=True,
    )
    assert (result.returncode == 0) is (key is None)


def test_upload_conflict_never_overwrites_or_retries(
    release_fixture: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, env, api = release_fixture
    make_artifacts(root)
    attempts = []

    def conflict(release_id: str, path: Path) -> None:
        attempts.append((release_id, path.name))
        raise subprocess.CalledProcessError(1, ["gh", "api"], stderr=b"HTTP 422")

    monkeypatch.setattr(api, "upload", conflict)
    with pytest.raises(subprocess.CalledProcessError):
        delivery.deliver("upload", root, env, api)
    assert len(attempts) == 1
    assert api.writes == []


def test_mid_upload_tag_drift_preserves_foreign_release(
    release_fixture: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, env, api = release_fixture
    make_artifacts(root)
    upload = api.upload

    def drift(release_id: str, path: Path) -> None:
        upload(release_id, path)
        api.other_release = True

    monkeypatch.setattr(api, "upload", drift)
    with pytest.raises(ValueError, match="another release"):
        delivery.deliver("upload", root, env, api)
    assert len(api.writes) == 1  # Keep the already attached asset; no deletion or foreign write.


@pytest.mark.parametrize("seam", ["unchanged", "before-body", "after-body", "before-body-restored"])
@pytest.mark.parametrize(
    "relative",
    [
        "release-artifacts/native-linux/dcc-mcp-obs-1.0.0-linux.zip",
        "release-artifacts/python-dist/dcc_mcp_obs-1.0.0-py3-none-any.whl",
        "release-artifacts/python-dist/dcc_mcp_obs-1.0.0.tar.gz",
        "SHA256SUMS",
    ],
)
def test_cli_upload_keeps_frozen_digest_through_body_consumption(
    release_fixture: tuple, monkeypatch: pytest.MonkeyPatch, seam: str, relative: str
) -> None:
    root, env, api = release_fixture
    make_artifacts(root)
    frozen = (root / "SHA256SUMS").read_bytes()
    target = root / relative
    original = target.read_bytes()
    ordered = [
        path.name for path in sorted((root / "release-artifacts").rglob("*")) if path.is_file()
    ] + ["SHA256SUMS"]
    consumed = {}
    run = subprocess.run

    def offline_gh(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if args == ["git", "rev-parse", "HEAD"]:
            return run(args, **kwargs)
        assert args[:2] == ["gh", "api"]  # Deny all other subprocess/network operations.
        assert kwargs == {"check": True, "capture_output": True}
        if "--method" not in args:
            prefix = "repos/dcc-mcp/dcc-mcp-obs/"
            assert len(args) == 3 and args[2].startswith(prefix)
            payload = api.get(args[2][len(prefix) :])
        else:
            path = Path(args[-1])
            assert args == [
                "gh",
                "api",
                "https://uploads.github.com/repos/dcc-mcp/dcc-mcp-obs/releases/123/assets"
                f"?name={quote(path.name, safe='')}",
                "--method",
                "POST",
                "-H",
                "Content-Type: application/octet-stream",
                "--input",
                str(path),
            ]
            assert path.name not in consumed  # No overwrite or retry.
            if path == target and seam in {"before-body", "before-body-restored"}:
                path.write_bytes(b"changed after validation, before POST body consumption")
            data = path.read_bytes()  # The production gh --input body-consumption seam.
            consumed[path.name] = data
            payload = {
                "name": path.name,
                "state": "uploaded",
                "size": len(data),
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
            }
            api.release["assets"].append(payload)
            if path == target and seam == "after-body":
                path.write_bytes(b"changed after POST body consumption")
            elif path == target and seam == "before-body-restored":
                path.write_bytes(original)  # Local rechecks alone cannot detect consumed drift.
        return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")

    monkeypatch.chdir(root)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(subprocess, "run", offline_gh)
    steps = workflow("release.yml")["jobs"]["publish"]["steps"]
    reached_pypi = False
    failure = None
    for step in steps:
        script = step.get("run", "").strip()
        if script.startswith("python tools/release_delivery.py "):
            assert script in {
                "python tools/release_delivery.py check",
                "python tools/release_delivery.py upload",
            }
            assert "continue-on-error" not in step and "if" not in step
            monkeypatch.setattr(sys, "argv", ["release_delivery.py", script.split()[-1]])
            try:
                delivery.main()  # Real argparse, handoff, validator and GitHub command adapter.
            except ValueError as exc:
                failure = str(exc)
                break
        elif step.get("uses", "").startswith("pypa/gh-action-pypi-publish@"):
            reached_pypi = True  # Reachability only; never invoke a publisher.
    changed_body = seam in {"before-body", "before-body-restored"}
    assert (consumed[target.name] != original) is changed_body
    if target.name != "SHA256SUMS":
        assert (root / "SHA256SUMS").read_bytes() == frozen
        assert hashlib.sha256(original).hexdigest().encode() in frozen
    else:
        assert original == frozen
    assert len(api.release["assets"]) == len(consumed)  # Preserve already-created assets.
    if seam == "unchanged":
        assert reached_pypi and failure is None
        assert list(consumed) == ordered
        assert consumed["SHA256SUMS"] == frozen
        for line in frozen.decode().splitlines():
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(consumed[Path(name).name]).hexdigest() == digest
    else:
        assert not reached_pypi, "Artifact drift must not reach the downstream PyPI step"
        assert failure is not None
        assert list(consumed) == ordered[: ordered.index(target.name) + 1]
