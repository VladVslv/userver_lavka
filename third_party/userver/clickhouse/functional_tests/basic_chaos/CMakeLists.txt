project(userver-clickhouse-tests-basic-chaos CXX)

add_executable(${PROJECT_NAME} "clickhouse_service.cpp")
target_link_libraries(${PROJECT_NAME} userver-core userver-clickhouse)

userver_chaos_testsuite_add()
if (TARGET testsuite-${PROJECT_NAME})
  set_tests_properties(testsuite-${PROJECT_NAME} PROPERTIES ENVIRONMENT
          "TESTSUITE_CLICKHOUSE_SERVER_START_TIMEOUT=10.0")
endif()
