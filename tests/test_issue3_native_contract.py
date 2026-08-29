from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_issue3_native_vendor_surface_is_typed_and_fail_closed() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    for request in (
        "ListProfiles",
        "GetCurrentProfile",
        "SetCurrentProfile",
        "ListSceneCollections",
        "GetCurrentSceneCollection",
        "SetCurrentSceneCollection",
        "ListAllowlistedHotkeys",
        "TriggerAllowlistedHotkey",
        "CaptureSourceScreenshot",
        "CaptureScreenshot",
    ):
        assert f'"{request}"' in source
    assert "return UiOperation::Invalid;" in source
    assert "RawRequest" not in source
    assert "ExecuteScript" not in source


def test_issue3_native_hotkeys_and_screenshots_redact_untrusted_surfaces() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    assert "kAllowlistedHotkeys" in source
    assert "OBS_HOTKEY_NOT_ALLOWLISTED" in source
    assert "OBS_SCREENSHOT_UNVERIFIED" in source
    assert "obs_frontend_get_current_profile_path" not in source
    assert "obs_frontend_take_source_screenshot" not in source


def test_native_deadline_and_response_contracts_are_explicit() -> None:
    source = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    assert "deadline_ms" in source
    assert "claim_mutation(state->deadline)" in source
    assert 'obs_data_set_string(result, "hotkeyName"' in source
    assert "OBS_SCREENSHOT_UNVERIFIED" in source
    assert "OBS_UI_INDETERMINATE" in source
    assert "names[count] != nullptr" in source
