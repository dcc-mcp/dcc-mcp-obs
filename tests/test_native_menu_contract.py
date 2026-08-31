from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_native_plugin_registers_one_top_level_menu_on_ui_thread_and_removes_it() -> None:
    plugin = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    menu = (ROOT / "native" / "src" / "dcc-mcp-menu.cpp").read_text(encoding="utf-8")
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    assert "native/src/dcc-mcp-menu.cpp" in cmake
    assert "obs_queue_task(OBS_TASK_UI, install_dcc_mcp_menu, nullptr, true)" in plugin
    remove_menu = "obs_queue_task(OBS_TASK_UI, remove_dcc_mcp_menu, nullptr, true)"
    assert remove_menu in plugin
    assert plugin.index(remove_menu) < plugin.index("obs_frontend_remove_event_callback")
    assert "QMainWindow" in menu
    assert "->menuBar()" in menu
    assert "kDccMcpMenuObjectName" in menu
    assert "findChild<QMenu *>" in menu
    assert "Qt::FindDirectChildrenOnly" in menu


def test_menu_actions_are_closed_native_callbacks() -> None:
    plugin = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    menu = (ROOT / "native" / "src" / "dcc-mcp-menu.cpp").read_text(encoding="utf-8")

    assert "kDccMcpMenuEntries" in menu
    assert "menu_->addSeparator()" in menu
    assert "menu_->addAction" in menu
    assert "dispatch_dcc_mcp_menu_action" in menu
    assert '"dccMcpObsMenu.%1"' in menu
    assert "format_dcc_mcp_menu_status" in plugin
    assert "create_agent_input_overlay" in plugin
    assert "obs_frontend_get_current_scene" in plugin
    assert "QDesktopServices::openUrl" in plugin
    assert '"http://127.0.0.1:%1/admin?panel=instances"' in plugin
    assert "QMessageBox::information" in plugin
    assert "QProcess" not in plugin
    assert "std::system" not in plugin


def test_native_plugin_autostarts_only_the_explicit_standalone_executable() -> None:
    plugin = (ROOT / "native" / "src" / "plugin-main.cpp").read_text(encoding="utf-8")
    launcher = (ROOT / "native" / "src" / "sidecar-launcher.cpp").read_text(encoding="utf-8")
    header = (ROOT / "native" / "src" / "sidecar-launcher.hpp").read_text(encoding="utf-8")
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    assert "native/src/sidecar-launcher.cpp" in cmake
    assert "SidecarLauncher" in plugin
    assert "start_configured_sidecar" in plugin
    assert "stop_configured_sidecar" in plugin
    assert "DCC_MCP_OBS_EXECUTABLE" in header
    assert "qEnvironmentVariable" in launcher
    assert "isAbsolute" in launcher
    assert "isFile" in launcher
    assert "isExecutable" in launcher
    assert 'QStringLiteral("--host-pid")' in launcher
    assert "QProcess::start" not in launcher
    assert ".start()" in launcher
    assert "startDetached" not in launcher
    assert "QProcess" not in plugin
    assert "std::system" not in launcher
