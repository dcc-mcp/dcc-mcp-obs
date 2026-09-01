#pragma once

#include <obs.h>

#include <memory>
#include <string>
#include <vector>

namespace dcc_mcp_obs {

struct SceneRecordingSpec {
	std::string scene_name;
	std::string file_name_prefix;
};

class SceneRecordingSessionManager {
public:
	SceneRecordingSessionManager();
	~SceneRecordingSessionManager();

	SceneRecordingSessionManager(const SceneRecordingSessionManager &) = delete;
	SceneRecordingSessionManager &operator=(const SceneRecordingSessionManager &) = delete;

	obs_data_t *start(const std::vector<SceneRecordingSpec> &specs);
	obs_data_t *status(const std::string &session_id);
	obs_data_t *stop(const std::string &session_id);
	bool active() const;
	void shutdown();

private:
	struct Impl;
	std::unique_ptr<Impl> impl_;
};

} // namespace dcc_mcp_obs
