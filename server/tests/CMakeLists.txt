cmake_minimum_required(VERSION 3.21)
project(server_security_offline LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 REQUIRED COMPONENTS Core Network Sql HttpServer Concurrent)
if(NOT SHADNET_ROOT)
    message(FATAL_ERROR "Set SHADNET_ROOT to a disposable pinned tree patched by recipe/security.py")
endif()
add_executable(test_server_security
    "${SHADNET_ROOT}/tests/test_server_security.cpp"
    "${SHADNET_ROOT}/src/bloodborne_summon_broker.cpp"
    "${SHADNET_ROOT}/src/database.cpp"
    "${SHADNET_ROOT}/src/webapi_auth.cpp"
    "${SHADNET_ROOT}/src/stun_server.cpp" "${SHADNET_ROOT}/src/stun_server.h")
target_include_directories(test_server_security PRIVATE "${SHADNET_ROOT}/src")
target_include_directories(test_server_security PRIVATE "${SHADNET_GENERATED}" "${SHADNET_PROTOBUF}/src")
target_link_libraries(test_server_security PRIVATE Qt6::Core Qt6::Network Qt6::Sql Qt6::HttpServer)
add_executable(test_bloodborne_summon_broker
    "${SHADNET_ROOT}/tests/test_bloodborne_summon_broker.cpp"
    "${SHADNET_ROOT}/src/bloodborne_summon_broker.cpp")
target_include_directories(test_bloodborne_summon_broker PRIVATE "${SHADNET_ROOT}/src")
target_link_libraries(test_bloodborne_summon_broker PRIVATE Qt6::Core)
if(SHADNET_GENERATED AND SHADNET_PROTOBUF)
    # Compile all other changed translation units using existing generated proto
    # headers. This target does not link or launch the full service.
    add_library(changed_server_objects OBJECT
        "${SHADNET_ROOT}/src/server.cpp" "${SHADNET_ROOT}/src/webapi_server.cpp"
        "${SHADNET_ROOT}/src/cmd_account.cpp" "${SHADNET_ROOT}/src/client_session.cpp")
    target_include_directories(changed_server_objects PRIVATE "${SHADNET_ROOT}/src"
        "${SHADNET_GENERATED}" "${SHADNET_PROTOBUF}/src")
    target_link_libraries(changed_server_objects PRIVATE Qt6::Core Qt6::Network Qt6::Sql Qt6::HttpServer Qt6::Concurrent)
endif()
enable_testing()
add_test(NAME server_security COMMAND test_server_security)
add_test(NAME bloodborne_summon_broker COMMAND test_bloodborne_summon_broker)
