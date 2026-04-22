cmake_minimum_required(VERSION 3.10)
project(mini_project VERSION 1.0.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)

add_executable(mini_project
    src/main.c
    src/utils.c
)

target_include_directories(mini_project PRIVATE include)

# 옵션
option(ENABLE_TESTS "Enable tests" ON)
if(ENABLE_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()
