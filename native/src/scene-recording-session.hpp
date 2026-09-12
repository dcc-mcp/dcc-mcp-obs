#pragma once

#include <obs.h>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace dcc_mcp_obs {

struct SceneRecordingSpec {
	std::string scene_name;
	std::string file_name_prefix;
	std::string file_name;
	std::string output_directory;
	std::string application_id;
	std::string run_id;
	std::string source_name;
	uint32_t process_id = 0;
	uint64_t window_handle = 0;
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
