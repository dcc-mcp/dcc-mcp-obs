from __future__ import annotations

import json
import subprocess
import sys
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from dcc_mcp_obs.config import ObsEndpointConfig
from dcc_mcp_obs.disposable_obs import DisposableObsLayout
from dcc_mcp_obs.owned_obs import HandleIdentity, OwnedObsError, OwnedObsHost
from dcc_mcp_obs.real_obs_acceptance import await_authenticated_status


@pytest.fixture
def host(tmp_path, monkeypatch):
    executable = tmp_path / "obs64.exe"
    executable.write_bytes(b"obs-image")
    layout = DisposableObsLayout(
        root=tmp_path,
        executable=executable,
        config_root=tmp_path / "config",
        plugin_install_root=tmp_path / "plugin",
        recordings=tmp_path / "recordings",
        command=(str(executable), "--portable", "--multi"),
        environment={},
    )
    events = []
    process = SimpleNamespace(pid=42, returncode=None, _handle=0x1234)

    def wait(timeout):
        events.append("wait")
        if process.returncode is None:
            raise subprocess.TimeoutExpired("obs", timeout)
        return process.returncode

    process.wait = wait
    identity = HandleIdentity(42, 0x01DC0000ABCDEF12, str(executable))
    owner = OwnedObsHost(
        layout,
        ObsEndpointConfig(port=45454, password="operator-secret-password"),
        platform="windows",
        identity_reader=lambda original: identity if original is process else None,
    )

    def shutdown():
        events.append("shutdown")
        process.returncode = 7
        return {"hostPid": 42, "instanceId": "native-instance", "shutdownScheduled": True}

    bridge = SimpleNamespace(
        status=lambda: {"ready": True, "hostPid": 42, "instanceId": "native-instance"},
        request_graceful_shutdown=shutdown,
    )
    transport = SimpleNamespace(close=lambda: events.append("transport-close"))
    captured = {}

    def popen(command, **kwargs):
        captured.update(kwargs)
        captured["command"] = command
        kwargs["stdout"].write(b"native stdout\n")
        kwargs["stderr"].write(b"native stderr\n")
        return process

    monkeypatch.setattr(
        "dcc_mcp_obs.real_obs_acceptance._probe_authentication_required", lambda _: True
    )
    monkeypatch.setattr(
        "dcc_mcp_obs.real_obs_acceptance.await_authenticated_status",
        partial(await_authenticated_status, sleeper=lambda _: None, attempts=3),
    )
    start = {
        "popen_factory": popen,
        "readiness_probe": lambda *_: None,
        "transport_factory": lambda _: transport,
        "bridge_factory": lambda *_, **__: bridge,
    }
    return SimpleNamespace(
        owner=owner,
        process=process,
        events=events,
        identity=identity,
        bridge=bridge,
        transport=transport,
        start=start,
        captured=captured,
    )


def test_owned_start_and_shutdown_uses_original_wait_after_disconnect(host, monkeypatch):
    monkeypatch.setenv("DCC_MCP_OBS_EXECUTABLE", "must-not-autospawn.exe")
    monkeypatch.setenv("DCC_MCP_OBS_WEBSOCKET_PASSWORD", "inherited-secret")
    owner = host.owner.start(**host.start)
    assert owner.process is host.process
    assert owner.identity.creation_filetime == 0x01DC0000ABCDEF12
    assert owner.ready
    assert "DCC_MCP_OBS_EXECUTABLE" not in host.captured["env"]
    assert "DCC_MCP_OBS_WEBSOCKET_PASSWORD" not in host.captured["env"]
    assert owner.stdout_path.read_bytes() == b"native stdout\n"
    assert owner.stderr_path.read_bytes() == b"native stderr\n"
    assert host.captured["stdin"] == subprocess.DEVNULL
    assert owner.close(close_adapter=lambda: host.events.append("adapter-close")) == 7
    assert host.events == ["shutdown", "adapter-close", "transport-close", "wait"]
    evidence = json.loads(owner.evidence_path.read_text())
    assert evidence["state"] == "TERMINAL"
    assert evidence["originalWaitReturncode"] == 7
    assert "operator-secret" not in owner.evidence_path.read_text()
    assert "inherited-secret" not in owner.evidence_path.read_text()
    with pytest.raises(OwnedObsError, match="SHUTDOWN_ALREADY_ATTEMPTED"):
        owner.close()
    assert host.events.count("shutdown") == 1


@pytest.mark.parametrize("phase", ["constructor", "status"])
def test_readiness_retries_bridge_construction_and_status(host, phase):
    attempts = []
    primary = RuntimeError("OBS_INSTANCE_NOT_READY")
    ready_status = host.bridge.status

    def status():
        if phase == "status" and len(attempts) == 1:
            raise primary
        return ready_status()

    def bridge_factory(transport, *, expected_pid):
        assert transport is host.transport
        assert expected_pid == host.process.pid
        attempts.append(expected_pid)
        if phase == "constructor" and len(attempts) == 1:
            raise primary
        host.bridge.status = status
        return host.bridge

    host.start["bridge_factory"] = bridge_factory
    owner = host.owner.start(**host.start)

    assert len(attempts) == 2
    assert owner.ready
    assert owner.bridge is host.bridge
    assert owner.primary_error is None
    assert owner.process is host.process
    assert "shutdown" not in host.events


def test_readiness_exhaustion_preserves_first_constructor_error(host):
    errors = [RuntimeError("first private failure"), RuntimeError("later private failure")]
    attempts = []

    def bridge_factory(*_, **__):
        error = errors[bool(attempts)]
        attempts.append(error)
        raise error

    host.start["bridge_factory"] = bridge_factory
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)

    assert len(attempts) == 3
    assert caught.value.primary_error is errors[0]
    assert caught.value.owner.process is host.process
    assert not host.owner.ready
    assert host.owner.returncode is None
    assert not host.owner.shutdown_attempted
    assert "private failure" not in host.owner.evidence_path.read_text()


@pytest.mark.parametrize(
    "field,value", [("pid", 999), ("creation_filetime", 0), ("image", "other")]
)
def test_start_rejects_invalid_identity_but_retains_original_process(host, field, value):
    values = vars(host.identity).copy()
    if field == "image":
        other = host.owner.layout.root / "other.exe"
        other.write_bytes(b"obs-image")
        value = str(other)
    values[field] = value
    host.owner._identity_reader = lambda _: HandleIdentity(**values)
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert caught.value.owner.process is host.process
    assert "IDENTITY_MISMATCH" in str(caught.value.primary_error)
    assert caught.value.owner.returncode is None
    assert not host.events
    assert json.loads(host.owner.evidence_path.read_text())["state"] == "UNKNOWN"


def test_readiness_rechecks_full_birth_on_same_handle(host):
    reads = iter(
        [
            host.identity,
            HandleIdentity(42, host.identity.creation_filetime + 1, host.identity.image),
        ]
    )
    host.owner._identity_reader = lambda _: next(reads)
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert "IDENTITY_MISMATCH" in str(caught.value.primary_error)
    assert not host.owner.ready
    assert host.owner.process is host.process


@pytest.mark.parametrize("phase", ["before", "after"])
def test_hash_drift_is_rejected_before_ready(host, phase):
    def drift(*_):
        host.owner.executable.write_bytes(b"replaced-image")

    if phase == "before":
        drift()
    else:
        host.start["readiness_probe"] = drift
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert "IMAGE_HASH_CHANGED" in str(caught.value.primary_error)
    assert host.owner.process is (None if phase == "before" else host.process)


@pytest.mark.parametrize("phase", ["readiness_probe", "transport_factory", "bridge_factory"])
def test_post_launch_failures_keep_primary_owner_and_stderr(host, phase):
    primary = RuntimeError("operator-secret-password")

    def fail(*_, **__):
        raise primary

    host.start[phase] = fail
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert caught.value.primary_error is primary
    assert caught.value.owner.process is host.process
    assert host.owner.stderr_path.read_bytes() == b"native stderr\n"
    assert "operator-secret-password" not in str(caught.value)
    assert "operator-secret-password" not in host.owner.evidence_path.read_text()


def test_authenticated_native_pid_mismatch_is_not_ready(host):
    host.bridge.status = lambda: {"ready": True, "hostPid": 43, "instanceId": "another-host"}
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert "NATIVE_PID_MISMATCH" in str(caught.value.primary_error)
    assert host.owner.instance_id is None


def test_candidate_port_is_not_published_as_ready_before_authenticated_status(host):
    def inspect(*_):
        evidence = json.loads(host.owner.evidence_path.read_text())
        assert evidence["candidateWebsocketPort"] == 45454
        assert evidence["state"] == "STARTING"
        assert evidence["instanceId"] is None

    host.start["readiness_probe"] = inspect
    host.owner.start(**host.start)
    assert json.loads(host.owner.evidence_path.read_text())["state"] == "READY"


def test_shutdown_failure_keeps_primary_after_timeout_and_no_retry(host):
    owner = host.owner.start(**host.start)
    primary = RuntimeError("shutdown refused")

    def fail():
        host.events.append("shutdown")
        raise primary

    with pytest.raises(OwnedObsError) as caught:
        owner.close(shutdown=fail, close_adapter=lambda: host.events.append("adapter-close"))
    assert caught.value.owner is owner
    assert caught.value.primary_error is primary
    assert owner.returncode is None
    assert host.events == ["shutdown", "adapter-close", "transport-close", "wait"]
    assert json.loads(owner.evidence_path.read_text())["state"] == "UNKNOWN"
    with pytest.raises(OwnedObsError):
        owner.close(shutdown=fail)
    assert host.events.count("shutdown") == 1
    host.process.returncode = 9
    assert owner.wait_for_exit() == 9


def test_adapter_close_failure_does_not_skip_transport_or_original_wait(host):
    owner = host.owner.start(**host.start)
    primary = RuntimeError("adapter-close failed")

    def fail():
        host.events.append("adapter-close")
        raise primary

    with pytest.raises(OwnedObsError) as caught:
        owner.close(close_adapter=fail)
    assert caught.value.primary_error is primary
    assert owner.returncode == 7
    assert host.events == ["shutdown", "adapter-close", "transport-close", "wait"]


def test_log_open_failure_does_not_spawn_or_lose_primary(host):
    host.owner.stderr_path.write_bytes(b"existing evidence")
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert isinstance(caught.value.primary_error, FileExistsError)
    assert host.owner.process is None
    assert host.owner.stderr_path.read_bytes() == b"existing evidence"


def test_evidence_failure_after_spawn_retains_owner_and_primary(host, monkeypatch):
    primary = OSError("evidence write failed")

    def fail_record():
        raise primary

    monkeypatch.setattr(host.owner, "_record", fail_record)
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert caught.value.owner.process is host.process
    assert caught.value.primary_error is primary
    assert host.owner.cleanup_errors == [primary]


def test_stdio_close_failure_after_spawn_retains_original_child(host, monkeypatch):
    from contextlib import contextmanager

    original_open = Path.open
    primary = OSError("stdio close failed")

    @contextmanager
    def failing_close(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as stream:
            yield stream
        raise primary

    def open_file(path, *args, **kwargs):
        if path == host.owner.stderr_path and args == ("xb",):
            return failing_close(path, *args, **kwargs)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert caught.value.owner.process is host.process
    assert caught.value.primary_error is primary
    assert host.owner.stderr_path.read_bytes() == b"native stderr\n"


@pytest.mark.parametrize("print_error", [None, BrokenPipeError, KeyboardInterrupt])
def test_cli_holds_live_owner_before_system_exit(host, monkeypatch, capsys, print_error):
    from dcc_mcp_obs import disposable_obs

    owner = host.owner.start(**host.start)
    primary = RuntimeError("private secret")
    failure = owner.failure("OBS_OWNED_TEST_FAILURE", primary)
    monkeypatch.setattr(
        disposable_obs,
        "_parse_args",
        lambda _: SimpleNamespace(
            obs_executable=Path("obs"),
            native_plugin_archive=Path("plugin"),
            python_wheel=Path("wheel"),
            work_root=Path("work"),
            output=Path("out"),
        ),
    )

    def run(**_):
        raise failure

    monkeypatch.setattr(disposable_obs, "run_disposable_acceptance", run)

    def wait(timeout):
        assert timeout is None
        if print_error is None:
            assert "UNKNOWN" in capsys.readouterr().out
        assert owner.process is host.process
        assert host.events == ["transport-close"]
        return 8

    host.process.wait = wait
    if print_error is not None:

        def fail_print(*_, **__):
            raise print_error()

        monkeypatch.setattr("builtins.print", fail_print)
    with pytest.raises(print_error or SystemExit) as caught:
        disposable_obs.main([])
    assert owner.returncode == 8
    if print_error is None:
        assert caught.value.code == 1
        final = json.loads(capsys.readouterr().out)
        assert final["state"] == "TERMINAL"
        assert final["originalWaitReturncode"] == 8
    assert json.loads(owner.evidence_path.read_text())["originalWaitReturncode"] == 8


def test_cli_retention_survives_interrupted_wait(host, monkeypatch):
    owner = host.owner.start(**host.start)
    outcomes = iter([KeyboardInterrupt(), 0])

    def wait(timeout):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    host.process.wait = wait

    def interrupted_sleep(_):
        raise KeyboardInterrupt

    monkeypatch.setattr("dcc_mcp_obs.owned_obs.time.sleep", interrupted_sleep)
    owner.retain_until_exit()
    assert owner.returncode == 0
    assert isinstance(owner.primary_error, KeyboardInterrupt)


@pytest.mark.parametrize("instance_id", [None, 123, "", "  "])
def test_ready_instance_is_explicitly_validated(host, monkeypatch, instance_id):
    monkeypatch.setattr(
        "dcc_mcp_obs.real_obs_acceptance.await_authenticated_status",
        lambda _: {"ready": True, "hostPid": 42, "instanceId": instance_id},
    )
    with pytest.raises(OwnedObsError) as caught:
        host.owner.start(**host.start)
    assert "NATIVE_INSTANCE_INVALID" in str(caught.value.primary_error)
    assert (
        json.loads(host.owner.evidence_path.read_text())["primaryErrorCode"]
        == "OBS_OWNED_NATIVE_INSTANCE_INVALID"
    )


def test_windows_identity_reads_full_filetime_image_and_pid_from_original_handle(monkeypatch):
    from dcc_mcp_obs import owned_obs

    handles = []

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, handle, *args):
            handles.append(handle.value)
            return self.callback(*args)

    def times(created, *_):
        created._obj.dwHighDateTime = 0x01DCABCD
        created._obj.dwLowDateTime = 0x87654321
        return 1

    def image(_flags, buffer, length):
        buffer.value = "C:\\private-obs\\obs64.exe"
        length._obj.value = len(buffer.value)
        return 1

    kernel = SimpleNamespace(
        GetProcessId=Function(lambda: 42),
        GetProcessTimes=Function(times),
        QueryFullProcessImageNameW=Function(image),
    )
    monkeypatch.setattr(owned_obs.ctypes, "WinDLL", lambda *_, **__: kernel, raising=False)
    identity = owned_obs.windows_handle_identity(SimpleNamespace(_handle=0x98765))
    assert identity == HandleIdentity(42, 0x01DCABCD87654321, "C:\\private-obs\\obs64.exe")
    assert handles == [0x98765] * 3


def test_server_ownership_query_does_not_attach_or_start(monkeypatch):
    from dcc_mcp_obs import server

    monkeypatch.setattr(server, "_server", None)
    assert not server.has_server()
    monkeypatch.setattr(server, "_server", SimpleNamespace(is_running=False))
    assert server.has_server()


def test_module_enumeration_resizes_and_uses_original_handle(monkeypatch):
    from ctypes import sizeof, wintypes

    from dcc_mcp_obs import owned_obs

    handles = []
    capacities = []

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, handle, *args):
            handles.append(handle.value)
            return self.callback(*args)

    def enum(modules, size, needed, flags):
        assert flags == 3
        capacities.append(size)
        needed._obj.value = (1200 if len(capacities) == 1 else 1) * sizeof(wintypes.HMODULE)
        modules[0] = 123
        return 1

    def filename(module, buffer, _size):
        assert module == 123
        buffer.value = "C:\\private-obs\\dcc-mcp-obs.dll"
        return len(buffer.value)

    kernel = SimpleNamespace(
        K32EnumProcessModulesEx=Function(enum),
        K32GetModuleFileNameExW=Function(filename),
    )
    monkeypatch.setattr(owned_obs.ctypes, "WinDLL", lambda *_, **__: kernel, raising=False)
    assert owned_obs.windows_module_paths(SimpleNamespace(_handle=0x65432)) == [
        Path("C:\\private-obs\\dcc-mcp-obs.dll")
    ]
    assert handles == [0x65432] * 3
    assert capacities[1] > capacities[0]


@pytest.mark.parametrize("length", [0, 32768])
def test_module_query_does_not_accept_missing_or_truncated_path(monkeypatch, length):
    from ctypes import sizeof, wintypes

    from dcc_mcp_obs import owned_obs

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    def enum(_handle, modules, _size, needed, _flags):
        needed._obj.value = sizeof(wintypes.HMODULE)
        modules[0] = 123
        return 1

    kernel = SimpleNamespace(
        K32EnumProcessModulesEx=Function(enum),
        K32GetModuleFileNameExW=Function(lambda *_: length),
    )
    monkeypatch.setattr(owned_obs.ctypes, "WinDLL", lambda *_, **__: kernel, raising=False)
    monkeypatch.setattr(owned_obs.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        owned_obs.ctypes, "WinError", lambda code: OSError(code, "denied"), raising=False
    )
    with pytest.raises((OSError, RuntimeError)) as caught:
        owned_obs.windows_module_paths(SimpleNamespace(_handle=123))
    if length == 0:
        assert caught.value.errno == 5
    else:
        assert str(caught.value) == "OBS_OWNED_MODULE_QUERY_FAILED"


@pytest.mark.parametrize(
    "owned,exit_code,failure_phase",
    [
        (True, 0, None),
        (True, 17, None),
        (False, 0, None),
        (False, 17, None),
        (False, None, None),
        (False, False, None),
        (False, 0.0, None),
        (True, 0, "client"),
        (True, 0, "url"),
        (True, 0, "start"),
    ],
)
def test_acceptance_caller_uses_owned_shutdown_and_rejects_unverified_exit(
    host, monkeypatch, owned, exit_code, failure_phase
):
    from dcc_mcp_obs import real_obs_acceptance as acceptance
    from dcc_mcp_obs import server

    owner = host.owner.start(**host.start)
    owner.layout.recordings.mkdir()
    monkeypatch.setattr(acceptance, "_platform_name", lambda: "windows")
    monkeypatch.setattr(acceptance.ObsEndpointConfig, "from_environment", lambda: owner.config)
    monkeypatch.setattr(acceptance, "await_authenticated_status", lambda _: host.bridge.status())

    def forbid_pid_attachment(*_args, **_kwargs):
        raise AssertionError("owned path reopened process by PID")

    if owned:
        monkeypatch.setattr(acceptance, "_verify_disposable_process", forbid_pid_attachment)
        monkeypatch.setattr(acceptance, "_loaded_native_module_paths", forbid_pid_attachment)
        monkeypatch.setattr(owner, "mapped_module_paths", lambda: [Path("native.dll")])
    else:

        def legacy_wait(**_):
            host.events.append("legacy-wait")
            return exit_code

        process = SimpleNamespace(wait=legacy_wait)
        monkeypatch.setattr(acceptance, "_verify_disposable_process", lambda *_: process)
        monkeypatch.setattr(
            acceptance, "_loaded_native_module_paths", lambda *_, **__: [Path("native.dll")]
        )
    monkeypatch.setattr(
        acceptance,
        "verify_loaded_native_plugin",
        lambda *_, **__: {
            "version": acceptance.__version__,
            "platform": "windows",
            "archive_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        acceptance,
        "verify_installed_python_wheel",
        lambda *_, **__: {
            "version": acceptance.__version__,
            "wheel_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(server, "has_server", lambda: False)
    monkeypatch.setattr(
        server, "start_server", lambda **_: SimpleNamespace(mcp_url="http://local-test")
    )
    monkeypatch.setattr(server, "stop_server", lambda: host.events.append("adapter-close"))

    def call(name, args):
        assert name == "obs_control__request_graceful_shutdown" and args == {}
        host.events.append("shutdown")
        host.process.returncode = exit_code
        return {
            "success": True,
            "context": {
                "shutdownScheduled": True,
                "hostPid": 42,
                "instanceId": "native-instance",
            },
        }

    client = SimpleNamespace(initialize=lambda: None, call=call)
    monkeypatch.setattr(acceptance, "McpAcceptanceClient", lambda *_, **__: client)
    monkeypatch.setattr(acceptance, "exercise_live_obs", lambda *_, **__: {})
    monkeypatch.setattr(acceptance, "build_public_evidence", lambda *_, **__: {"result": "passed"})
    output = owner.layout.root / "public-evidence.json"
    kwargs = dict(
        host_pid=42,
        native_plugin_archive=Path("native.zip"),
        python_wheel=Path("adapter.whl"),
        disposable_root=owner.layout.root,
        output=output,
        owner=owner if owned else None,
    )
    if failure_phase:
        primary = KeyboardInterrupt()

        def fail(*_, **__):
            raise primary

        if failure_phase == "client":
            monkeypatch.setattr(acceptance, "McpAcceptanceClient", fail)
        elif failure_phase == "start":
            monkeypatch.setattr(server, "start_server", fail)
        else:

            class BadUrl:
                mcp_url = property(fail)

            monkeypatch.setattr(server, "start_server", lambda **_: BadUrl())
        with pytest.raises(KeyboardInterrupt) as caught:
            acceptance.run_real_obs_acceptance(**kwargs)
        assert caught.value is primary
        assert owner.primary_error is primary
        assert owner.process is host.process
        assert host.events == ["adapter-close"]
        return
    if type(exit_code) is not int or exit_code != 0:
        with pytest.raises(acceptance.AcceptanceContractError, match="HOST_EXIT_FAILED"):
            acceptance.run_real_obs_acceptance(**kwargs)
        assert not output.exists()
        if owned:
            assert owner.returncode == exit_code
    else:
        assert acceptance.run_real_obs_acceptance(**kwargs) == {"result": "passed"}
        assert output.exists()
    assert host.events == (
        ["shutdown", "adapter-close", "transport-close", "wait"]
        if owned
        else ["shutdown", "adapter-close", "legacy-wait"]
    )


@pytest.mark.parametrize("close_fails", [False, True])
def test_server_construction_failure_closes_or_retains_transport(monkeypatch, close_fails):
    from dcc_mcp_obs import server

    primary = KeyboardInterrupt()
    events = []

    def close():
        events.append("close")
        if close_fails:
            raise RuntimeError("close failed")

    transport = SimpleNamespace(close=close)
    monkeypatch.setattr(server, "resolve_obs_pid", lambda _: 42)
    monkeypatch.setattr(server.ObsEndpointConfig, "from_environment", lambda: ObsEndpointConfig())
    monkeypatch.setattr(server, "ObsWebSocketTransport", lambda _: transport)

    def bridge(*_, **__):
        raise primary

    monkeypatch.setattr(server, "ObsControlBridge", bridge)
    with pytest.raises(
        server.AdapterConstructionError if close_fails else KeyboardInterrupt
    ) as caught:
        server.ObsMcpServer(port=0, host_pid=42)
    assert events == ["close"]
    if close_fails:
        assert caught.value.primary_error is primary
        assert caught.value.adapter._transport is transport
    else:
        assert caught.value is primary


@pytest.mark.parametrize("close_fails", [False, True])
def test_server_start_interrupt_closes_or_retains_candidate(monkeypatch, close_fails):
    from dcc_mcp_obs import server

    primary = KeyboardInterrupt()
    events = []

    def start():
        raise primary

    def close():
        events.append("close")
        if close_fails:
            raise RuntimeError("close failed")

    candidate = SimpleNamespace(register_builtin_actions=lambda: None, start=start, stop=close)
    monkeypatch.setattr(server, "_server", None)
    monkeypatch.setattr(server, "ObsMcpServer", lambda **_: candidate)
    with pytest.raises(KeyboardInterrupt) as caught:
        server.start_server(host_pid=42)
    assert caught.value is primary
    assert events == ["close"]
    assert server._server is (candidate if close_fails else None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows original-handle API")
def test_native_windows_queries_use_an_owned_console_child(tmp_path):
    from dcc_mcp_obs.owned_obs import windows_handle_identity, windows_module_paths

    with (
        (tmp_path / "stdout.log").open("xb") as stdout,
        (tmp_path / "stderr.log").open("xb") as stderr,
    ):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; print('ready', flush=True); sys.stdin.buffer.read()",
            ],
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    try:
        deadline = time.monotonic() + 10
        while b"ready" not in (tmp_path / "stdout.log").read_bytes():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        identity = windows_handle_identity(process)
        assert identity.pid == process.pid
        assert 2**32 < identity.creation_filetime < 2**64
        assert Path(identity.image).resolve() == Path(sys.executable).resolve()
        assert windows_handle_identity(process) == identity
        assert Path(sys.executable).resolve() in [
            path.resolve() for path in windows_module_paths(process)
        ]
    finally:
        process.stdin.close()
        assert process.wait(timeout=15) == 0
