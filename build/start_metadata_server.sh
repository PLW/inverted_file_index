#!/bin/bash
./metadata_server --listen=127.0.0.1:50051 --is_leader=1 --leader_addr=127.0.0.1:50051 --epoch=1 --shard_map=../shard_map.txt
