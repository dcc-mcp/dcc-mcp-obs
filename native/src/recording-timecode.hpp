#pragma once

#include <cstdint>
#include <iomanip>
#include <sstream>
#include <string>

namespace dcc_mcp_obs {

// Whole-millisecond output duration for a frame count at a rational frame
// rate (fps = fps_num / fps_den).  Returns 0 when the rate is unavailable.
//
// OBS frame rates are small (fps_num/fps_den bounded by the active video
// output), so the intermediate product stays inside uint64 for any real frame
// count.  The division floors to whole milliseconds, matching obs-websocket's
// GetRecordStatus outputDuration.
constexpr uint64_t duration_ms_from_frames(uint64_t frames, uint32_t fps_num, uint32_t fps_den)
{
	if (fps_num == 0 || fps_den == 0)
		return 0;
	return frames * fps_den * 1000 / fps_num;
}

// Format a whole-millisecond duration as HH:MM:SS.mmm, the timecode shape used
// by obs-websocket's GetRecordStatus outputTimecode.  Hours are not truncated.
inline std::string format_timecode(uint64_t duration_ms)
{
	const uint64_t hours = duration_ms / 3'600'000;
	const uint64_t minutes = (duration_ms / 60'000) % 60;
	const uint64_t seconds = (duration_ms / 1000) % 60;
	const uint64_t millis = duration_ms % 1000;
	std::ostringstream out;
	out << std::setfill('0') << std::setw(2) << hours << ':' << std::setw(2) << minutes << ':' << std::setw(2)
	    << seconds << '.' << std::setw(3) << millis;
	return out.str();
}

} // namespace dcc_mcp_obs
