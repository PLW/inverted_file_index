Continuing in the same context, you are acting as a C++ distributed network
design guru, with special expertise in the use of the grpc remote procedure call
framework.  We need a minimal grpc implementation of a distributed inverted
index system where :
*  external requests go through a `load balancer` endpoint,
* `load balancer` forwards requests to one `eval` endpoint of the `eval cluster`, 
* `eval` endpoints communicate with a raft-based `metadata` cluster,
* `eval` endoints communicate with one or all N `index` endpoints of the `index` cluster
* `eval` endpoints are share-nothing
* `index` endpoints are replicated in a primary-secondary pattern
* `index` primary endpoint maintains a WAL for consistency

We are looking for the grpc design pattern, the internal
functions of the various node types are stubbed out.
