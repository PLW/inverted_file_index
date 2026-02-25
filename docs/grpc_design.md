Below is a **minimal gRPC design pattern** for a distributed inverted index with:

* **Eval nodes**: stateless, load-balanced entrypoints (query fanout + merge).
* **Metadata nodes**: Raft group (leader-based writes; read strategies vary).
* **Index nodes**: shard/replica workers (term → postings retrieval).

Internal indexing/search logic is stubbed; focus is **service boundaries, RPC shapes, routing, leader redirects, fanout**, and C++ gRPC wiring.

---

## 1) Core design pattern

### Request flow

1. Client → **Eval** (via L7/L4 LB, DNS RR, or gRPC xDS).
2. Eval → **Metadata** to fetch:

   * current **cluster epoch**
   * **index shard map** (term→shard or doc-range→node set)
   * optional: preferred replicas, locality
3. Eval → **Index** nodes (parallel RPC fanout) for postings / partial results
4. Eval merges partials → response to client

### Key gRPC ideas used

* **Eval nodes are stateless**: cache shard-map by epoch; refresh on `STALE_EPOCH`.
* **Metadata is leader-aware**: write RPCs return `NOT_LEADER + leader_address`.
* **Index RPCs are fanout-friendly**: small unary for minimal; optionally streaming for large postings.
* **Every request carries**:

  * `request_id`, `trace_id` (optional)
  * `epoch` (to detect stale routing)
  * `deadline` enforced by client

---

## 2) Proto: minimal services + messages

Create `dist_index.proto`:

```proto
syntax = "proto3";

package distidx;

option cc_generic_services = false;

message Status {
  enum Code {
    OK = 0;
    NOT_LEADER = 1;
    STALE_EPOCH = 2;
    UNAVAILABLE = 3;
    INVALID_ARGUMENT = 4;
    INTERNAL = 5;
  }
  Code code = 1;
  string message = 2;

  // For NOT_LEADER replies from metadata nodes.
  string leader_address = 3; // host:port
  uint64 current_epoch = 4;  // for STALE_EPOCH replies
}

message RequestContext {
  string request_id = 1;
  string trace_id = 2;
  uint64 epoch = 3;          // client's view of shard-map epoch
}

//
// Client-facing API (Eval Nodes)
//
message EvalQueryRequest {
  RequestContext ctx = 1;
  string query = 2;
  uint32 top_k = 3;
}

message ScoredDoc {
  uint64 doc_id = 1;
  float score = 2;
}

message EvalQueryResponse {
  Status status = 1;
  repeated ScoredDoc hits = 2;
}

service EvalService {
  rpc EvalQuery(EvalQueryRequest) returns (EvalQueryResponse);
}

//
// Metadata API (Raft group)
//
message GetShardMapRequest {
  RequestContext ctx = 1;
}

message ShardReplica {
  string address = 1;  // index node host:port
}

message ShardInfo {
  uint32 shard_id = 1;
  repeated ShardReplica replicas = 2;
}

message ShardMap {
  uint64 epoch = 1;
  repeated ShardInfo shards = 2;
}

message GetShardMapResponse {
  Status status = 1;
  ShardMap map = 2;
}

message ResolveTermsRequest {
  RequestContext ctx = 1;
  repeated string terms = 2;
}

message TermShard {
  string term = 1;
  uint32 shard_id = 2;
}

message ResolveTermsResponse {
  Status status = 1;
  uint64 epoch = 2;
  repeated TermShard placements = 3;
}

service MetadataService {
  rpc GetShardMap(GetShardMapRequest) returns (GetShardMapResponse);
  rpc ResolveTerms(ResolveTermsRequest) returns (ResolveTermsResponse);

  // Minimal "write" to show leader redirects (stubbed).
  rpc UpdateConfig(bytes opaque) returns (Status);
}

//
// Index node API
//
message PostingsRequest {
  RequestContext ctx = 1;
  string term = 2;
  uint32 shard_id = 3;
  uint32 limit = 4;
}

message Posting {
  uint64 doc_id = 1;
  uint32 tf = 2;
}

message PostingsResponse {
  Status status = 1;
  repeated Posting postings = 2;
}

service IndexService {
  rpc GetPostings(PostingsRequest) returns (PostingsResponse);

  // Optional upgrade path for large lists; keep as a pattern.
  rpc StreamPostings(PostingsRequest) returns (stream Posting);
}
```

**Why this is minimal but complete**

* `EvalService` is your single public RPC.
* `MetadataService` provides routing and epoching (plus leader redirect pattern).
* `IndexService` provides unary postings (plus optional streaming variant).

---

## 3) Load balancing patterns

### Clients → Eval nodes

* Simplest: DNS with multiple A records for `eval.service:443` and gRPC round-robin (or LB).
* Better: Envoy/xDS (gRPC LB policy), but not required for minimal.

### Eval → Index nodes

* Eval selects a replica from `ShardInfo.replicas` (random, RR, or locality).
* Keep a **per-address Channel** cache inside Eval to avoid reconnect cost.

### Eval → Metadata (Raft)

* Maintain a list of metadata addresses.
* Use a small helper: `CallLeaderAware()` which retries on `NOT_LEADER` using returned leader address.

---

## 4) C++ skeleton: servers

### 4.1 Eval node server (handles query, fans out)

```cpp
class EvalServiceImpl final : public distidx::EvalService::Service {
 public:
  EvalServiceImpl(std::shared_ptr<distidx::MetadataService::Stub> meta_stub,
                  /* Index stub cache etc. */)
      : meta_stub_(std::move(meta_stub)) {}

  grpc::Status EvalQuery(grpc::ServerContext* ctx,
                         const distidx::EvalQueryRequest* req,
                         distidx::EvalQueryResponse* resp) override {
    // 1) Parse query into terms (stub)
    std::vector<std::string> terms = Tokenize(req->query()); // stub

    // 2) Resolve terms -> shard placements (metadata)
    distidx::ResolveTermsRequest rreq;
    *rreq.mutable_ctx() = req->ctx();
    for (auto& t : terms) rreq.add_terms(t);

    distidx::ResolveTermsResponse rresp;
    auto s = CallMetadataLeaderAware_ResolveTerms(ctx, rreq, &rresp);
    if (!s.ok() || rresp.status().code() != distidx::Status::OK) {
      *resp->mutable_status() = rresp.status();
      return grpc::Status::OK;
    }

    // 3) Fanout to index nodes for postings (parallel; stub merge)
    // Minimal pattern: sequential; production: thread pool / async.
    for (const auto& placement : rresp.placements()) {
      // shard_id -> choose replica address from cached shard map
      auto addr = ChooseReplicaAddress(placement.shard_id()); // stub
      auto index_stub = GetIndexStub(addr);                   // channel cache

      distidx::PostingsRequest preq;
      *preq.mutable_ctx() = req->ctx();
      preq.mutable_ctx()->set_epoch(rresp.epoch());
      preq.set_term(placement.term());
      preq.set_shard_id(placement.shard_id());
      preq.set_limit(1000);

      distidx::PostingsResponse presp;
      grpc::ClientContext cctx;
      cctx.set_deadline(ctx->deadline());
      index_stub->GetPostings(&cctx, preq, &presp);

      if (presp.status().code() == distidx::Status::STALE_EPOCH) {
        // Pattern: invalidate cache and retry once (omitted here)
      }

      // Merge stubbed
      MergePostingsIntoTopK(presp.postings(), req->top_k(), resp->mutable_hits()); // stub
    }

    resp->mutable_status()->set_code(distidx::Status::OK);
    return grpc::Status::OK;
  }

 private:
  std::shared_ptr<distidx::MetadataService::Stub> meta_stub_;

  grpc::Status CallMetadataLeaderAware_ResolveTerms(grpc::ServerContext* server_ctx,
                                                   const distidx::ResolveTermsRequest& req,
                                                   distidx::ResolveTermsResponse* out) {
    // Minimal leader-aware retry loop.
    std::string leader = current_meta_leader_; // host:port; initial may be empty
    for (int attempt = 0; attempt < 3; ++attempt) {
      auto stub = (leader.empty()) ? meta_stub_ : MakeMetadataStub(leader);
      grpc::ClientContext cctx;
      cctx.set_deadline(server_ctx->deadline());
      auto s = stub->ResolveTerms(&cctx, req, out);
      if (!s.ok()) continue;
      if (out->status().code() == distidx::Status::NOT_LEADER &&
          !out->status().leader_address().empty()) {
        leader = out->status().leader_address();
        current_meta_leader_ = leader;
        continue;
      }
      return grpc::Status::OK;
    }
    out->mutable_status()->set_code(distidx::Status::UNAVAILABLE);
    out->mutable_status()->set_message("metadata unavailable");
    return grpc::Status::OK;
  }

  std::string current_meta_leader_;

  // Stubs you’d implement:
  std::vector<std::string> Tokenize(const std::string&);
  std::string ChooseReplicaAddress(uint32_t shard_id);
  std::shared_ptr<distidx::IndexService::Stub> GetIndexStub(const std::string& addr);
  std::shared_ptr<distidx::MetadataService::Stub> MakeMetadataStub(const std::string& addr);
  void MergePostingsIntoTopK(const google::protobuf::RepeatedPtrField<distidx::Posting>&,
                            uint32_t top_k,
                            google::protobuf::RepeatedPtrField<distidx::ScoredDoc>* hits);
};
```

### 4.2 Metadata node server (Raft leader redirect pattern)

```cpp
class MetadataServiceImpl final : public distidx::MetadataService::Service {
 public:
  grpc::Status ResolveTerms(grpc::ServerContext*,
                           const distidx::ResolveTermsRequest* req,
                           distidx::ResolveTermsResponse* resp) override {
    // Reads can be served by leader or follower depending on your consistency.
    // Minimal: serve from leader only.
    if (!IsLeader()) {
      resp->mutable_status()->set_code(distidx::Status::NOT_LEADER);
      resp->mutable_status()->set_leader_address(CurrentLeaderAddress());
      return grpc::Status::OK;
    }

    // Stub: term -> shard_id mapping
    resp->set_epoch(CurrentEpoch());
    for (const auto& t : req->terms()) {
      auto* ts = resp->add_placements();
      ts->set_term(t);
      ts->set_shard_id(HashToShard(t)); // stub
    }
    resp->mutable_status()->set_code(distidx::Status::OK);
    return grpc::Status::OK;
  }

  grpc::Status GetShardMap(grpc::ServerContext*,
                          const distidx::GetShardMapRequest*,
                          distidx::GetShardMapResponse* resp) override {
    if (!IsLeader()) {
      resp->mutable_status()->set_code(distidx::Status::NOT_LEADER);
      resp->mutable_status()->set_leader_address(CurrentLeaderAddress());
      return grpc::Status::OK;
    }
    auto* map = resp->mutable_map();
    map->set_epoch(CurrentEpoch());
    // Stub fill shards/replicas
    BuildShardMap(map); // stub
    resp->mutable_status()->set_code(distidx::Status::OK);
    return grpc::Status::OK;
  }

  grpc::Status UpdateConfig(grpc::ServerContext*,
                           const google::protobuf::BytesValue*,
                           distidx::Status* resp) override {
    if (!IsLeader()) {
      resp->set_code(distidx::Status::NOT_LEADER);
      resp->set_leader_address(CurrentLeaderAddress());
      return grpc::Status::OK;
    }
    // Stub: apply to raft log (omitted)
    resp->set_code(distidx::Status::OK);
    return grpc::Status::OK;
  }

 private:
  bool IsLeader();
  std::string CurrentLeaderAddress();
  uint64_t CurrentEpoch();
  uint32_t HashToShard(const std::string& term);
  void BuildShardMap(distidx::ShardMap* out);
};
```

### 4.3 Index node server (unary + optional stream)

```cpp
class IndexServiceImpl final : public distidx::IndexService::Service {
 public:
  grpc::Status GetPostings(grpc::ServerContext*,
                          const distidx::PostingsRequest* req,
                          distidx::PostingsResponse* resp) override {
    if (req->ctx().epoch() != LocalEpoch()) {
      resp->mutable_status()->set_code(distidx::Status::STALE_EPOCH);
      resp->mutable_status()->set_current_epoch(LocalEpoch());
      return grpc::Status::OK;
    }

    // Stub: return empty postings
    resp->mutable_status()->set_code(distidx::Status::OK);
    return grpc::Status::OK;
  }

  grpc::Status StreamPostings(grpc::ServerContext*,
                             const distidx::PostingsRequest* req,
                             grpc::ServerWriter<distidx::Posting>* writer) override {
    if (req->ctx().epoch() != LocalEpoch()) return grpc::Status::OK;

    // Stub: no postings
    (void)writer;
    return grpc::Status::OK;
  }

 private:
    uint64_t LocalEpoch();
};
```

---

## 5) C++ skeleton: client + channels

### Channel creation (shared pattern)

* Use one `grpc::Channel` per remote address, cached in a map.
* For internal calls: prefer **mTLS**; for minimal, use insecure creds.

```cpp
std::shared_ptr<grpc::Channel> MakeChannel(const std::string& addr) {
  grpc::ChannelArguments args;
  // Keepalive & limits (optional, pattern only)
  args.SetInt(GRPC_ARG_KEEPALIVE_TIME_MS, 30'000);
  args.SetInt(GRPC_ARG_KEEPALIVE_TIMEOUT_MS, 10'000);
  args.SetInt(GRPC_ARG_MAX_RECEIVE_MESSAGE_LENGTH, 64 * 1024 * 1024);

  return grpc::CreateCustomChannel(addr, grpc::InsecureChannelCredentials(), args);
}
```

### Client → Eval call

```cpp
distidx::EvalQueryResponse CallEval(const std::string& eval_addr, std::string query) {
  auto ch = MakeChannel(eval_addr);
  auto stub = distidx::EvalService::NewStub(ch);

  distidx::EvalQueryRequest req;
  req.mutable_ctx()->set_request_id("req-123");
  req.mutable_ctx()->set_epoch(0); // client doesn't need shard epoch
  req.set_query(std::move(query));
  req.set_top_k(10);

  distidx::EvalQueryResponse resp;
  grpc::ClientContext ctx;
  ctx.set_deadline(std::chrono::system_clock::now() + std::chrono::milliseconds(250));
  stub->EvalQuery(&ctx, req, &resp);
  return resp;
}
```

---

## 6) Minimal “rules of thumb” baked into this pattern

* **Eval nodes**: no local truth; cache shard maps; invalidate on `STALE_EPOCH`.
* **Metadata nodes**: leader redirect for writes (and for reads if you want simple correctness).
* **Index nodes**: validate epoch; respond `STALE_EPOCH` rather than silently serving wrong shard layouts.
* **Deadlines**: always propagate caller deadline to downstream RPCs.
* **Fanout**: start with sync/unary; later switch to Async gRPC + thread pool.
