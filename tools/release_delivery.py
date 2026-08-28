"""Validate the Release Please handoff and attach assets without replacing anything."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

REPOSITORY = "dcc-mcp/dcc-mcp-obs"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


class GitHub:
    """The only network boundary; GET and create-only asset POST, never edit/delete."""

    def get(self, endpoint: str) -> object:
        result = subprocess.run(
            ["gh", "api", f"repos/{REPOSITORY}/{endpoint}"],
            check=True,
            capture_output=True,
        )
        return json.loads(result.stdout)

    def upload(self, release_id: str, path: Path) -> None:
        url = (
            f"https://uploads.github.com/repos/{REPOSITORY}/releases/{release_id}/assets"
            f"?name={quote(path.name, safe='')}"
        )
        subprocess.run(
            [
                "gh",
                "api",
                url,
                "--method",
                "POST",
                "-H",
                "Content-Type: application/octet-stream",
                "--input",
                str(path),
            ],
            check=True,
            capture_output=True,
        )


def handoff(root: Path, env: dict[str, str]) -> tuple[str, str, str, str]:
    tag, sha, release_id = (
        env.get(key, "") for key in ("RELEASE_TAG", "RELEASE_SHA", "RELEASE_ID")
    )
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY, "foreign repository")
    require(env.get("GITHUB_EVENT_NAME") == "push", "not a main push")
    require(env.get("GITHUB_REF") == "refs/heads/main", "not the main branch")
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "invalid release commit")
    require(re.fullmatch(r"[1-9][0-9]*", release_id) is not None, "invalid release ID")
    require(sha == env.get("GITHUB_SHA"), "release commit differs from caller")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    require(head == sha, "checkout differs from release commit")
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None, "invalid version")
    require(tag == f"v{version}", "release tag does not match the package version")
    return tag, sha, release_id, version


def verify_release(api: GitHub, tag: str, sha: str, release_id: str) -> dict:
    release = api.get(f"releases/{release_id}")
    require(isinstance(release, dict), "missing release")
    require(type(release.get("id")) is int and str(release["id"]) == release_id, "release ID drift")
    require(release.get("tag_name") == tag, "release tag drift")
    require(release.get("target_commitish") == sha, "release target drift")
    require(
        release.get("draft") is False and release.get("prerelease") is False, "not a final release"
    )
    author = release.get("author", {})
    require(
        author.get("login") == "github-actions[bot]" and author.get("type") == "Bot",
        "foreign release owner",
    )
    by_tag = api.get(f"releases/tags/{tag}")
    require(
        isinstance(by_tag, dict) and by_tag.get("id") == release["id"], "tag names another release"
    )
    ref = api.get(f"git/ref/tags/{tag}")
    require(isinstance(ref, dict) and ref.get("ref") == f"refs/tags/{tag}", "missing tag")
    obj = ref.get("object", {})
    for _ in range(4):
        if obj.get("type") != "tag":
            break
        oid = obj.get("sha", "")
        require(re.fullmatch(r"[0-9a-f]{40}", oid) is not None, "invalid tag object")
        obj = api.get(f"git/tags/{oid}").get("object", {})
    require(obj.get("type") == "commit" and obj.get("sha") == sha, "tag commit drift")
    require(isinstance(release.get("assets"), list), "missing asset inventory")
    return release


def content_digest(data: bytes) -> tuple[int, str]:
    return len(data), f"sha256:{hashlib.sha256(data).hexdigest()}"


def artifact_paths(root: Path, version: str) -> dict[Path, tuple[int, str]]:
    """Freeze the digest of the same bytes used for archive and manifest validation."""
    directory = root / "release-artifacts"
    expected = {
        f"python-dist/dcc_mcp_obs-{version}-py3-none-any.whl",
        f"python-dist/dcc_mcp_obs-{version}.tar.gz",
        *(
            f"native-{platform}/dcc-mcp-obs-{version}-{platform}.zip"
            for platform in ("linux", "macos", "windows")
        ),
    }
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    require(
        {path.relative_to(directory).as_posix() for path in files} == expected,
        "release artifact set is not exact",
    )
    snapshots = {}
    for path in files:
        data = path.read_bytes()
        require(not path.is_symlink() and len(data) > 0, "invalid artifact")
        snapshots[path] = content_digest(data)
        if path.suffix == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                manifest = json.loads(archive.read("dcc-mcp-obs-plugin.json"))
                require(
                    manifest["version"] == version and manifest["product"] == "dcc-mcp-obs",
                    "native version drift",
                )
                require(
                    path.parent.name == f"native-{manifest['platform']}", "native platform drift"
                )
                entries = manifest["files"]
                names = ["dcc-mcp-obs-plugin.json", *(entry["source"] for entry in entries)]
                require(
                    entries
                    and len(names) == len(set(names))
                    and sorted(names) == sorted(archive.namelist()),
                    "native inventory drift",
                )
                for entry in entries:
                    require(
                        hashlib.sha256(archive.read(entry["source"])).hexdigest()
                        == entry["sha256"],
                        "native digest drift",
                    )
        else:
            if path.suffix == ".whl":
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    metadata = archive.read(f"dcc_mcp_obs-{version}.dist-info/METADATA")
            else:
                with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                    stream = archive.extractfile(f"dcc_mcp_obs-{version}/PKG-INFO")
                    require(stream is not None, "missing sdist metadata")
                    metadata = stream.read()
            message = BytesParser().parsebytes(metadata)
            require(
                message["Name"] == "dcc-mcp-obs" and message["Version"] == version,
                "Python distribution version drift",
            )
    checksums = "".join(
        f"{snapshots[path][1].removeprefix('sha256:')}  {path.relative_to(root).as_posix()}\n"
        for path in files
    )
    manifest_path = root / "SHA256SUMS"
    manifest = manifest_path.read_bytes()
    require(
        manifest.decode().replace("\r\n", "\n") == checksums,
        "frozen checksums differ from artifacts",
    )
    snapshots[manifest_path] = content_digest(manifest)
    return snapshots


def deliver(mode: str, root: Path, env: dict[str, str], api: GitHub) -> None:
    tag, sha, release_id, version = handoff(root, env)
    release = verify_release(api, tag, sha, release_id)
    require(release["assets"] == [], "release already has assets; refusing to clobber")
    if mode == "check":
        return
    require(mode == "upload", "invalid operation")
    snapshots = artifact_paths(root, version)
    expected = {}
    for path, validated in snapshots.items():
        # Each write addresses the transaction-owned numeric ID, never a tag-selected release.
        current = verify_release(api, tag, sha, release_id)
        require(asset_digests(current["assets"]) == expected, "release assets changed")
        require(content_digest(path.read_bytes()) == validated, "local artifact changed")
        api.upload(release_id, path)  # GitHub rejects an existing name with HTTP 422.
        expected[path.name] = validated  # Never trust a post-upload path as the baseline.
        require(content_digest(path.read_bytes()) == validated, "local artifact changed")
    current = verify_release(api, tag, sha, release_id)
    require(asset_digests(current["assets"]) == expected, "uploaded assets differ")
    for path, validated in snapshots.items():
        require(content_digest(path.read_bytes()) == validated, "local artifact changed")


def asset_digests(assets: list[dict]) -> dict:
    result = {}
    for asset in assets:
        name = asset.get("name")
        require(
            isinstance(name, str) and name not in result and asset.get("state") == "uploaded",
            "conflicting asset inventory",
        )
        result[name] = (asset.get("size"), asset.get("digest"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "upload"))
    args = parser.parse_args()
    deliver(args.mode, Path.cwd(), dict(os.environ), GitHub())


if __name__ == "__main__":
    main()
