## Distributed Inverted Index
## Top-level abstractions and major components

### 1) Cluster and topology

* **Cluster**

  * `ClusterConfig` (shard count, replica factor, hash ring / ranges)
  * `NodeRegistry` (live nodes, capabilities, load)
* **Node**

  * `EvalNode`
  * `DataNode`
* **ShardRouting**

  * `HashRouter` (URL/doc_id → `ShardId`)
  * `ShardMap` (ShardId → `ReplicaGroupId` / endpoints)
  * `ReplicaSelector` (pick replica per shard for queries; hedge policy)

---

### 2) Sharding and replication

* **Shard**

  * `ShardId` (logical id / hash range)
  * `ShardReplica` (one shard instance on one data node)
  * `ShardState` (in-memory state machine: follower/leader/readonly/draining)
* **ReplicaGroup**

  * `ReplicaGroupId`
  * `ReplicaMember` (node + role)
  * `PrimaryLease` / `LeadershipTerm` (epoch/term fencing)
* **ReplicationProtocol**

  * `LeaderElection` (Raft/Paxos wrapper or lease manager)
  * `LogReplicator` (primary → secondaries)
  * `ReplicaApplier` (apply log to local state)
  * `ReadConsistencyPolicy` (read from primary/secondary; freshness constraints)

---

### 3) Durability, logging, and recovery

* **WAL**

  * `WalRecord` (upsert/delete, doc_id, version, payload ref)
  * `WalSegment` (file/segment chunk)
  * `WalWriter` / `WalReader`
* **CommitLog**

  * `CommitIndex` (quorum-acked position)
  * `ReplicationStream` (transport abstraction)
* **Checkpointing**

  * `CheckpointWriter`
  * `CheckpointReader`
  * `ReplayController` (replay WAL from checkpoint to tip)
* **FailureFencing**

  * `TermFence` (reject stale primaries)
  * `IdempotencyTable` (request_id / last-seen version)

---

### 4) Indexing pipeline

* **DocumentIngestor**

  * `Tokenizer` → produces `TokenStream`
  * `Inverter` → produces `InvertedTokens`
  * `IndexUpdater` → applies to `MemIndex` / doc table
* **Token / Term model**

  * `Token` (term, position, attributes)
  * `TermId` mapping (optional: `TermDictionary` / `TermEncoder`)

---

### 5) Index storage: segments and manifests

* **IndexStore** (per shard)

  * `MemIndex` (mutable inverted + doc tables)
  * `SegmentStore` (immutable segments on disk)
  * `ActiveView` (read snapshot over {MemIndex + active segments})
* **Segment**

  * `SegmentId`
  * `SegmentMetadata` (stats, min/max doc, created_at, delete ratio)
  * `PersistentSegment` (immutable on disk)
  * `MergedSegment` (builder output before publication)
* **Manifest**

  * `ManifestId` / `Generation`
  * `ManifestEntry` (segment list + checksums)
  * `ManifestPublisher` (atomic swap)
  * `SegmentGarbageCollector` (safe deletion after readers quiesce)
* **Flush / Merge**

  * `Flusher` (`flush_index()` → new segment)
  * `MergePlanner` (segment selection policy)
  * `Merger` (`merge(seg1, seg2)` → seg3)
  * `MergeScheduler` (throttle, IO budgets)

---

### 6) Postings and dictionary layer

* **TermDictionary**

  * `Lexicon` (term → posting list pointer)
  * `StatsTable` (df, cf, norms, etc.)
* **PostingList**

  * `PostingListReader` / `PostingListWriter`
  * `SkipList` / `SkipIndex`
  * `BlockCodec` (compression blocks; optional in MVP)
* **DocID model**

  * `DocId` (global) or `LocalDocId` + `ShardId` (for merge/dedupe)

---

### 7) Query planning and iterator execution

* **QueryEngine**

  * `QueryParser`
  * `QueryPlanner` (build logical plan)
  * `QueryExecutor` (execute on ActiveView)
* **Iterator hierarchy (core abstraction)**

  * `PostListIterator` (base)

    * `TermIterator` / `PostingListIterator` (for a term)
    * `AndIterator` (intersection)
    * `OrIterator` (union)
    * `NotIterator` (optional; if you support negation)
    * `PhraseIterator` / `ProximityIterator` (optional later)
  * `FacetIterator` (facets as iterator-like doc stream / filters)
  * `ScoredIterator` (wraps doc stream with scoring)
* **Optimization**

  * `MultiWayMerge` (k-way merge for OR/topK)
  * `CostModel` (term selectivity, choose AND order, etc.)
  * `EarlyTerminationPolicy` (topK cutoff, WAND/BlockMax later)

---

### 8) Facets / doc-values / stored fields

* **DocumentStore**

  * `StoredFieldsReader` (`doc(doc_id)` → doc)
  * `StoredFieldsWriter`
* **MetadataStore**

  * `DocMetadata` (version, timestamps, flags)
  * `MetadataReader` (`metadata(doc_id)` / `metadata()` → shard_status)
* **FacetStore (columnar)**

  * `FacetColumn` (per field)
  * `FacetReader` / `FacetWriter`
  * `FacetIndex` (optional: ordinals/dictionaries)

---

### 9) Distributed query (scatter-gather)

* **DistributedQueryCoordinator** (on eval node)

  * `ShardFanout` (broadcast)
  * `ShardResponseCollector` (gather with timeouts)
  * `ResultMerger` (merge topK, dedupe)
  * `ReRanker` (final scoring stage)
  * `PartialResultsPolicy` (timeouts, hedging)
* **ShardQueryService** (on data node)

  * `LocalSearchHandler` (executes QueryEngine)
  * `TopKCollector` (local topK + features)

---

### 10) Metadata and control plane

* **MetadataService**

  * `RoutingTable` (hash→replica group)
  * `ShardCatalog` (shard→active manifest + segment list)
  * `Watcher` / `Subscription` (push updates to nodes)
* **Admin / Ops**

  * `RebalanceController` (dual-read, cutover state machine)
  * `HealthMonitor` (liveness/readiness, shard-level health)
  * `ConfigManager` (feature flags, merge policy knobs)

---

### 11) Observability and SRE hooks

* **Metrics**

  * `MetricsRegistry` (counters, histograms)
  * `RefreshLagTracker` (`visible_at - durable_at`)
  * `ReplicaLagTracker`
  * `MergeBacklogTracker`
* **Tracing**

  * `TraceContext` propagation eval→data→subsystems
* **Logging**

  * `AuditLog` (admin operations)
  * `StructuredEventLog` (critical state transitions)

---

## Mapping to your MVP function list (sanity check)

* `token_stream` → `Tokenizer` / `TokenStream`
* `invert_tokens` → `Inverter`
* `update_index` → `IndexUpdater` / `MemIndex`
* `posting_lists` + skip lists → `PostingList*` + `SkipList`
* `multi_way_merge` → `MultiWayMerge`
* `iterator` + `postlist_iterator` → `PostListIterator` hierarchy
* facets as iterators → `FacetIterator` / `FacetStore`
* `flush_index()` → `Flusher` + `ManifestPublisher`
* `merge(seg1, seg2)` → `Merger` + `MergePlanner` + `ManifestPublisher`
* `query(...)` → `QueryEngine` / `QueryExecutor`
* `doc(doc_id)` → `StoredFieldsReader`
* `metadata(...)` → `MetadataStore` / `ShardStatus`

