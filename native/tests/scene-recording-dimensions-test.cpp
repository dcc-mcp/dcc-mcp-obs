#include "../src/scene-recording-dimensions.hpp"

#define CHECK(condition) \
	do { \
		if (!(condition)) \
			return 1; \
	} while (false)

int main()
{
	using dcc_mcp_obs::encoder_compatible_dimension;

	CHECK(encoder_compatible_dimension(3840) == 3840);
	CHECK(encoder_compatible_dimension(3595) == 3594);
	CHECK(encoder_compatible_dimension(2099) == 2098);
	CHECK(encoder_compatible_dimension(2) == 2);
	CHECK(encoder_compatible_dimension(1) == 0);
	CHECK(encoder_compatible_dimension(0) == 0);
	return 0;
}
