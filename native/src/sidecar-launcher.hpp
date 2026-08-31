#pragma once

#include <QProcess>

#include <functional>
#include <string>
#include <string_view>

namespace dcc_mcp_obs {

inline constexpr std::string_view kObsExecutableEnvironment = "DCC_MCP_OBS_EXECUTABLE";

enum class SidecarLaunchState {
	Disabled,
	Starting,
	AlreadyRunning,
	InvalidExecutable,
};

struct SidecarLaunchResult {
	SidecarLaunchState state;
	std::string executable;
	std::string message;
};

class SidecarLauncher final {
public:
	using ErrorCallback = std::function<void(const std::string &)>;

	explicit SidecarLauncher(ErrorCallback error_callback);
	~SidecarLauncher();

	SidecarLauncher(const SidecarLauncher &) = delete;
	SidecarLauncher &operator=(const SidecarLauncher &) = delete;

	SidecarLaunchResult start_from_environment();
	void stop();

private:
	QProcess process_;
	ErrorCallback error_callback_;
};

} // namespace dcc_mcp_obs
