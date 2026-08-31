from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def test_delayed_native_ui_mutation_is_cancelled_before_execution(tmp_path: Path) -> None:
    compiler = next(
        (candidate for name in ("c++", "g++", "clang++") if (candidate := shutil.which(name))),
        None,
    )
    if compiler is None:
        pytest.skip("a standalone C++ compiler is unavailable")
    source = ROOT / "native" / "tests" / "ui-task-gate-test.cpp"
    executable = tmp_path / "ui-task-gate-test"
    subprocess.run(
        [compiler, "-std=c++17", "-pthread", str(source), "-o", str(executable)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    subprocess.run([str(executable)], check=True, timeout=10)


def test_every_destructive_obs_mutation_claims_after_state_probes() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    cases = (
        ("StartRecording", "obs_frontend_recording_start();"),
        ("StopRecording", "obs_frontend_recording_stop();"),
        ("PauseRecording", "obs_frontend_recording_pause(true);"),
        ("ResumeRecording", "obs_frontend_recording_pause(false);"),
    )
    for index, (operation, mutation) in enumerate(cases):
        start = source.index(f"case UiOperation::{operation}:")
        end = (
            source.index(f"case UiOperation::{cases[index + 1][0]}:", start)
            if index + 1 < len(cases)
            else source.index("\n\t\t}", source.index(mutation, start)) + 4
        )
        branch = source[start:end]
        probe = branch.index("obs_frontend_recording_active()")
        claim = branch.index("claim_mutation")
        mutate = branch.index(mutation)
        assert probe < claim < mutate
        assert "run_mutation" not in branch

    timeout = source.split("if (!state->condition.wait_for", maxsplit=1)[1].split(
        "obs_data_apply", maxsplit=1
    )[0]
    assert timeout.index("state->gate.cancel_pending()") < timeout.index(
        'set_error(response, "OBS_UI_TIMEOUT");'
    )


def test_graceful_shutdown_is_output_safe_capability_gated_and_deferred() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    start = source.index("case UiOperation::RequestGracefulShutdown:")
    end = source.index("case UiOperation::RecordingStatus:", start)
    branch = source[start:end]

    claim = branch.index("claim_mutation")
    for probe in (
        "obs_frontend_recording_active()",
        "obs_frontend_streaming_active()",
        "obs_frontend_replay_buffer_active()",
        "obs_frontend_virtualcam_active()",
        "obs_frontend_get_main_window()",
    ):
        assert branch.index(probe) < claim
    assert 'set_error(result, "OBS_INSTANCE_NOT_READY")' in branch
    assert 'set_error(result, "OBS_OUTPUT_ACTIVE")' in branch
    assert 'obs_data_set_bool(result, "shutdownScheduled", true)' in branch
    assert "QWidget" not in branch

    assert 'required_capability = "application_lifecycle"' in source
    run_call = source.index("run_ui_operation(operation_for(request)")
    deferred_exit = source.index(
        "obs_queue_task(OBS_TASK_UI, request_frontend_exit, nullptr, false)", run_call
    )
    assert run_call < deferred_exit
    exit_callback = source.split("void request_frontend_exit(void *)", maxsplit=1)[1].split(
        "void vendor_request", maxsplit=1
    )[0]
    assert "obs_frontend_get_main_window()" in exit_callback
    assert "QMetaObject::invokeMethod" in exit_callback
    assert "Qt::QueuedConnection" in exit_callback
    assert "obs_frontend_exit" not in source

    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "find_package(Qt6 REQUIRED COMPONENTS Widgets)" in cmake
    assert "Qt6::Widgets" in cmake
