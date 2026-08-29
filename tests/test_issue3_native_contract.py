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
    assert '"pathRedacted"' in source
    assert "obs_frontend_get_current_profile_path" not in source
    assert "obs_frontend_take_source_screenshot" in source
