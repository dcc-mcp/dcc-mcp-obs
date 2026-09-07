#pragma once

#include <cstdint>

namespace dcc_mcp_obs {

constexpr uint32_t encoder_compatible_dimension(uint32_t value)
{
	return value >= 2 ? value & ~uint32_t{1} : 0;
}

} // namespace dcc_mcp_obs
