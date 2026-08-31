"""Validate the Release Please handoff and attach assets without replacing anything."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

REPOSITORY = "dcc-mcp/dcc-mcp-obs"
WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


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


def _validate_native_archive(data: bytes, version: str, platform: str) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("dcc-mcp-obs-plugin.json"))
        require(
            manifest["version"] == version
            and manifest["product"] == "dcc-mcp-obs"
            and manifest["platform"] == platform,
            "native identity drift",
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
                hashlib.sha256(archive.read(entry["source"])).hexdigest() == entry["sha256"],
                "native digest drift",
            )


def _standalone_contents(path: Path, data: bytes) -> dict[str, bytes]:
    if path.suffix == ".zip":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            require(
                all(not stat.S_ISLNK(member.external_attr >> 16) for member in members),
                "standalone links are forbidden",
            )
            _require_portable_standalone_names(names)
            return {member.filename: archive.read(member) for member in members}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        require(
            all(member.isfile() for member in members),
            "standalone inventory is unsafe",
        )
        _require_portable_standalone_names(names)
        contents = {}
        for member in members:
            stream = archive.extractfile(member)
            require(stream is not None, "standalone member is unreadable")
            contents[member.name] = stream.read()
        return contents


def _require_portable_standalone_names(names: list[str]) -> None:
    require(
        names
        and len(names) == len(set(names))
        and len(names) == len({name.casefold() for name in names}),
        "standalone duplicate member",
    )
    for name in names:
        path = PurePosixPath(name)
        require(
            name == path.as_posix()
            and not path.is_absolute()
            and "\\" not in name
            and ":" not in name
            and 0 < len(name) <= 240
            and all(part not in {"", ".", ".."} for part in path.parts),
            "standalone member path is unsafe",
        )
        for part in path.parts:
            require(
                part == part.rstrip(" .")
                and all(ord(character) >= 32 for character in part)
                and part.split(".", maxsplit=1)[0].upper() not in WINDOWS_RESERVED_NAMES,
                "standalone member path is not portable",
            )


def _validate_standalone_archive(
    path: Path,
    data: bytes,
    version: str,
    platform: str,
    core_version: str,
    native_archive: bytes,
) -> None:
    contents = _standalone_contents(path, data)
    manifest = json.loads(contents["dcc-mcp-obs-standalone.json"])
    require(
        manifest.get("schema_version") == 1
        and manifest.get("product") == "dcc-mcp-obs-standalone"
        and manifest.get("version") == version
        and manifest.get("platform") == platform
        and manifest.get("core_version") == core_version,
        "standalone identity drift",
    )
    entries = manifest.get("files")
    require(isinstance(entries, list) and entries, "standalone manifest is empty")
    names = [entry.get("path") for entry in entries]
    require(
        all(isinstance(name, str) for name in names)
        and len(names) == len(set(names))
        and set(contents) == {"dcc-mcp-obs-standalone.json", *names},
        "standalone inventory drift",
    )
    executable = "dcc-mcp-obs.exe" if platform == "windows" else "dcc-mcp-obs"
    require(
        executable in names and "dcc-mcp-obs-plugin.zip" in names, "standalone payload incomplete"
    )
    for entry in entries:
        payload = contents[entry["path"]]
        require(
            entry.get("size") == len(payload)
            and entry.get("sha256") == hashlib.sha256(payload).hexdigest(),
            "standalone digest drift",
        )
    bundled_plugin = contents["dcc-mcp-obs-plugin.zip"]
    require(bundled_plugin == native_archive, "standalone native plugin differs from release asset")
    _validate_native_archive(bundled_plugin, version, platform)


def artifact_paths(root: Path, version: str) -> dict[Path, tuple[int, str]]:
    """Freeze the digest of the same bytes used for archive and manifest validation."""
    directory = root / "release-artifacts"
    standalone_config = tomllib.loads(
        (root / "packaging" / "standalone.toml").read_text(encoding="utf-8")
    )
    core_version = standalone_config["core_version"]
    expected = {
        f"python-dist/dcc_mcp_obs-{version}-py3-none-any.whl",
        f"python-dist/dcc_mcp_obs-{version}.tar.gz",
        *(
            f"native-{platform}/dcc-mcp-obs-{version}-{platform}.zip"
            for platform in ("linux", "macos", "windows")
        ),
        *(
            f"standalone-{platform}/dcc-mcp-obs-{version}-{platform}-standalone."
            f"{'zip' if platform == 'windows' else 'tar.gz'}"
            for platform in ("linux", "macos", "windows")
        ),
    }
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    require(
        {path.relative_to(directory).as_posix() for path in files} == expected,
        "release artifact set is not exact",
    )
    snapshots = {}
    artifact_data = {path.relative_to(directory).as_posix(): path.read_bytes() for path in files}
    for path in files:
        relative = path.relative_to(directory).as_posix()
        data = artifact_data[relative]
        require(not path.is_symlink() and len(data) > 0, "invalid artifact")
        snapshots[path] = content_digest(data)
        if path.parent.name.startswith("native-"):
            _validate_native_archive(data, version, path.parent.name.removeprefix("native-"))
        elif path.parent.name.startswith("standalone-"):
            platform = path.parent.name.removeprefix("standalone-")
            native = artifact_data[f"native-{platform}/dcc-mcp-obs-{version}-{platform}.zip"]
            _validate_standalone_archive(path, data, version, platform, core_version, native)
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
