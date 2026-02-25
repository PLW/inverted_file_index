#!/bin/bash

mkdir -p generated

protoc \
  -I proto \
  --cpp_out=generated \
  --grpc_out=generated \
  --plugin=protoc-gen-grpc=$(which grpc_cpp_plugin) \
  proto/dist_index.proto
