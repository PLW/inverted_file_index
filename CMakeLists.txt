# CMakeLists.txt
cmake_minimum_required(VERSION 3.20)
project(distidx_minimal_grpc CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)

set(PROTO_SRC
  ${CMAKE_CURRENT_SOURCE_DIR}/generated/dist_index.pb.cc
  ${CMAKE_CURRENT_SOURCE_DIR}/generated/dist_index.grpc.pb.cc
)

add_library(distidx_proto ${PROTO_SRC})
target_include_directories(distidx_proto PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/generated
)
target_link_libraries(distidx_proto PUBLIC protobuf::libprotobuf gRPC::grpc++)

add_library(distidx_common
  src/common/grpc_util.cpp
  src/common/shard_map.cpp
  src/common/tokenize.cpp
)
target_include_directories(distidx_common PUBLIC src)
target_link_libraries(distidx_common PUBLIC distidx_proto)

add_executable(metadata_server src/metadata/metadata_server.cpp)
target_link_libraries(metadata_server PRIVATE distidx_common)

add_executable(index_server src/index/index_server.cpp)
target_link_libraries(index_server PRIVATE distidx_common)

add_executable(eval_server src/eval/eval_server.cpp)
target_link_libraries(eval_server PRIVATE distidx_common)

add_executable(eval_client src/client/eval_client.cpp)
target_link_libraries(eval_client PRIVATE distidx_common)
