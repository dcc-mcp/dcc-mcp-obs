#include "sidecar-launcher.hpp"

#include <QCoreApplication>
#include <QFileInfo>
#include <QString>
#include <QStringList>

#include <utility>

namespace dcc_mcp_obs {

SidecarLauncher::SidecarLauncher(ErrorCallback error_callback) : error_callback_(std::move(error_callback))
{
	QObject::connect(&process_, &QProcess::errorOccurred, &process_, [this](QProcess::ProcessError) {
		if (error_callback_)
			error_callback_(process_.errorString().toStdString());
	});
	process_.setStandardOutputFile(QProcess::nullDevice());
	process_.setStandardErrorFile(QProcess::nullDevice());
}

SidecarLauncher::~SidecarLauncher()
{
	stop();
}

SidecarLaunchResult SidecarLauncher::start_from_environment()
{
	if (process_.state() != QProcess::NotRunning)
		return {SidecarLaunchState::AlreadyRunning, process_.program().toStdString(),
			"sidecar already running"};

	const QString configured = qEnvironmentVariable(kObsExecutableEnvironment.data()).trimmed();
	if (configured.isEmpty())
		return {SidecarLaunchState::Disabled, {}, "environment override is not configured"};

	const QFileInfo candidate(configured);
	if (!candidate.isAbsolute() || !candidate.isFile() || !candidate.isExecutable())
		return {SidecarLaunchState::InvalidExecutable, configured.toStdString(),
			"configured sidecar must be an absolute executable file"};
	const QString executable = candidate.canonicalFilePath();
	if (executable.isEmpty())
		return {SidecarLaunchState::InvalidExecutable, configured.toStdString(),
			"configured sidecar could not be resolved"};

	process_.setProgram(executable);
	process_.setArguments({QStringLiteral("--host-pid"), QString::number(QCoreApplication::applicationPid())});
	process_.setWorkingDirectory(candidate.absolutePath());
	process_.start();
	return {SidecarLaunchState::Starting, executable.toStdString(), "sidecar launch requested"};
}

void SidecarLauncher::stop()
{
	if (process_.state() == QProcess::NotRunning)
		return;
	process_.terminate();
	if (!process_.waitForFinished(1000)) {
		process_.kill();
		process_.waitForFinished(1000);
	}
}

} // namespace dcc_mcp_obs
