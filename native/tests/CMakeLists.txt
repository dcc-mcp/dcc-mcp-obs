cmake_minimum_required(VERSION 3.20)

project(dcc_mcp_obs_native_contract_tests LANGUAGES CXX)

include(CTest)
find_package(Threads REQUIRED)

add_executable(ui-task-gate-contract ui-task-gate-test.cpp)
target_compile_features(ui-task-gate-contract PRIVATE cxx_std_17)
target_link_libraries(ui-task-gate-contract PRIVATE Threads::Threads)

add_executable(dcc-mcp-menu-model-contract dcc-mcp-menu-model-test.cpp)
target_compile_features(dcc-mcp-menu-model-contract PRIVATE cxx_std_17)

if(MSVC)
  target_compile_options(ui-task-gate-contract PRIVATE /W4 /WX)
  target_compile_options(dcc-mcp-menu-model-contract PRIVATE /W4 /WX)
else()
  target_compile_options(ui-task-gate-contract PRIVATE -Wall -Wextra -Werror)
  target_compile_options(dcc-mcp-menu-model-contract PRIVATE -Wall -Wextra -Werror)
endif()

add_test(NAME ui-task-gate-contract COMMAND ui-task-gate-contract)
add_test(NAME dcc-mcp-menu-model-contract COMMAND dcc-mcp-menu-model-contract)
