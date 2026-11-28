import json
from types import SimpleNamespace

import pytest

from dcc_mcp_obs import launch_studio


def test_launch_resolves_relative_binary_from_another_directory(tmp_path, monkeypatch, capsys):
    binary = tmp_path / "OBS Studio" / "bin" / "64bit" / "obs64.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.chdir(tmp_path)
    calls = []

    def spawn(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(launch_studio.subprocess, "Popen", spawn)
    launch_studio.main(["--obs-executable", str(binary.relative_to(tmp_path)), "--portable"])
    assert calls == [([str(binary), "--portable"], binary.parent)]
    assert json.loads(capsys.readouterr().out) == {"hostPid": 42}


def test_missing_binary_fails_without_a_pid(tmp_path, capsys):
    with pytest.raises(SystemExit) as failure:
        launch_studio.main(["--obs-executable", str(tmp_path / "obs64.exe")])
    assert failure.value.code != 0
    assert capsys.readouterr().out == ""
