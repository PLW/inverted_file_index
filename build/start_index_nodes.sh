# shard 0 primary
./index_server --listen=127.0.0.1:50061 --role=primary --primary_addr=127.0.0.1:50061 --epoch=1 --secondaries=127.0.0.1:50062
# shard 0 secondary
./index_server --listen=127.0.0.1:50062 --role=secondary --primary_addr=127.0.0.1:50061 --epoch=1

# shard 1 primary
./index_server --listen=127.0.0.1:50063 --role=primary --primary_addr=127.0.0.1:50063 --epoch=1 --secondaries=127.0.0.1:50064
# shard 1 secondary
./index_server --listen=127.0.0.1:50064 --role=secondary --primary_addr=127.0.0.1:50063 --epoch=1
