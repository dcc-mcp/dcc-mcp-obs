#include "../src/recording-timecode.hpp"

#include <string>

#define CHECK(condition) \
	do { \
		if (!(condition)) \
			return 1; \
	} while (false)

int main()
{
	using namespace dcc_mcp_obs;

	CHECK(duration_ms_from_frames(0, 60, 1) == 0);
	CHECK(duration_ms_from_frames(60, 60, 1) == 1000);
	CHECK(duration_ms_from_frames(30, 60, 1) == 500);
	CHECK(duration_ms_from_frames(1, 60, 1) == 16);
	CHECK(duration_ms_from_frames(2, 60, 1) == 33);
	// NTSC 29.97: 30000 frames at 30000/1001 fps is exactly 1001 seconds.
	CHECK(duration_ms_from_frames(30000, 30000, 1001) == 1001000);
	CHECK(duration_ms_from_frames(100, 0, 1) == 0);
	CHECK(duration_ms_from_frames(100, 60, 0) == 0);

	CHECK(format_timecode(0) == "00:00:00.000");
	CHECK(format_timecode(999) == "00:00:00.999");
	CHECK(format_timecode(1000) == "00:00:01.000");
	CHECK(format_timecode(61'000) == "00:01:01.000");
	CHECK(format_timecode(3'661'000) == "01:01:01.000");
	CHECK(format_timecode(100 * 3'600'000 + 999) == "100:00:00.999");
	return 0;
}
