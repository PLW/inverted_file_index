## Distributed Inverted File Index System

High-level design + ADR/NFR/SLO/SRE (no C++ implementation)

### 0) Executive summary

We are building a distributed, document-partitioned inverted index where:

* **Routing**: `shard = hash(url or doc_id)`; eval nodes route writes and scatter queries.
* **Storage layout**: each shard is a set of **immutable segments** plus a small **in-memory mutable index** that is periodically flushed.
* **Query**: scatter to one replica per shard → local top-K → gather + merge → re-rank on eval nodes.
* **Replication**: 3+ replicas per shard (1 primary, 2+ secondaries), with WAL + replicated commit log, checkpoint/replay, atomic manifest swap for merges.
* **Metadata**: service tracks (hash→host mapping) and (shard→active segments manifest). Eval nodes cache this mapping.

This document records key architectural choices and tradeoffs, defines NFRs/SLOs, and lays out correctness invariants, reliability engineering, and a test strategy aligned with “default mode is failure.”

---

## 1) Goals, non-goals, and scope

### Goals

1. **Correctness under concurrency and failure**

   * Enforce document version monotonicity; idempotent writes; bounded refresh visibility.
2. **High-throughput ingestion**

   * Sustained writes with periodic flush and background merge, without blocking queries.
3. **Low-latency query serving**

   * Efficient posting-list iteration, skip lists, multi-way merge, composable iterators.
4. **Operational reliability**

   * Predictable failure modes, fast recovery, safe rebalancing, observability.

### Non-goals (MVP)

* Cross-shard joins beyond scatter-gather ranking.
* Arbitrary transactions across shards.
* Full-text ranking sophistication beyond baseline BM25/TF-IDF + optional rerank.
* Real-time global consistency (we target bounded eventual consistency, not linearizability for query results).

---

## 2) Terminology and core abstractions

### Shard / replica / primary

* **Shard**: the unit of partitioning by doc hash.
* **Replica group**: 3+ nodes storing one shard.
* **Primary**: the only replica that accepts writes for the shard at a time (enforced by consensus/lease).

### Segment model

* **MemIndex**: mutable in-memory inverted index (plus doc store metadata).
* **Persistent segment**: immutable on-disk files for postings, dictionaries, doc values/facets, stored fields, etc.
* **Manifest**: immutable file list + metadata describing the active segment set. Updates via **atomic swap**.

### Query iterators (logical plan)

* `postlist_iterator`: base iterator over docIDs (with skip/advance).
* `and_iterator`, `or_iterator`: composable iterators.
* `columnar_facets`: doc-values or facet columns exposed via iterator-like accessors.
* `multi_way_merge`: k-way merge of postings streams (esp. OR / top-K retrieval).

---

## 3) NFRs and SLOs

### Non-functional requirements

**Performance**

* High QPS with predictable tail latency (p95/p99).
* Write throughput stable under merge pressure.

**Reliability**

* Tolerate node loss, partial network partitions, disk full conditions.
* Avoid split-brain (single primary invariant).

**Consistency**

* Per-document monotonic versioning; idempotent writes; bounded refresh `T_refresh`.

**Scalability**

* Horizontal scale by shard count and replica groups; linear-ish scale for reads/writes.

**Operability**

* Clear oncall playbooks, dashboards, alarms, controlled rollouts, safe rebalances.

### Proposed SLOs (starting points; tune with load tests)

Queries (end-to-end, eval node perspective):

* **Availability**: 99.9% monthly (MVP); 99.95% later.
* **Latency**: p50 < 30ms, p95 < 120ms, p99 < 300ms (depends on corpus, K, rerank cost).
* **Correctness**: bounded staleness on acknowledged writes: **`T_refresh ≤ 4s`**.

Writes (ingest API):

* **Durability**: acknowledged write survives single-node failure in replica group (quorum commit).
* **Latency**: p95 < 200ms (including replication), p99 < 500ms.
* **Throughput**: target is workload-dependent; define per-shard sustained writes and burst.

Maintenance:

* **Recovery time**: RTO per shard replica loss < 10 minutes to restore redundancy (initial target).
* **Data loss**: RPO = 0 for acknowledged writes (quorum WAL replication).

---

## 4) Architecture overview

### Components

**Eval nodes**

* Hold cached (hash→shard/replica group) mapping.
* Route writes to shard primary.
* For queries: pick one healthy replica per shard (or small fanout) → gather top-K → merge + rerank.
* Enforce request shaping (timeouts, budgets) and partial results policy.

**Data nodes**

* Host one or more shard replicas.
* For each shard replica:

  * Ingestion pipeline: tokenize → invert → update MemIndex → append WAL/commit log.
  * Flush: MemIndex → persistent immutable segment.
  * Merge: background compactor merges segments; atomic manifest swap.

**Metadata service**

* Authoritative mapping:

  * (hash range or consistent-hash ring → replica group endpoints)
  * (shard → active manifest pointer, segment list, generation numbers)
* Supports watch/notify for changes to reduce stale routing.

**Consensus / lease service**

* Per shard replica group: leader election / primary lease (Raft/Paxos or external lease store).
* Guarantees “single primary” split-brain guard.

---

## 5) ADR: Key decisions and tradeoffs

### ADR-001: Document-partitioned sharding by URL hash

**Decision**: shard documents by `hash(url/doc_id)`; query is scatter-gather.
**Pros**

* Simple routing; easy scale-out; locality for doc updates.
* Writes touch only one shard group.
  **Cons**
* Queries must hit all shards (or many), increasing tail latency.
* Global ranking requires merging top-K from many shards; sensitive to stragglers.
  **Mitigations**
* Dynamic shard selection for highly selective terms (optional later).
* Use **per-shard top-K + global merge**; aggressive timeouts + partial results policy.
* Replica selection: choose fastest healthy replica per shard (hedged requests).

### ADR-002: Immutable segments + background compaction

**Decision**: flush MemIndex to immutable segments; merge segments asynchronously; publish via atomic manifest swap.
**Pros**

* Simple correctness story: segments are immutable; readers are lock-free-ish.
* Crash safety: manifests provide stable “known-good” snapshots.
  **Cons**
* Merge amplification; storage bloat; background work can steal IO/CPU from queries.
  **Mitigations**
* Merge policy with budgets (IO throttling), tiered compaction, and query-aware scheduling.
* Segment temperature: keep small recent segments in cache; optimize for common terms.

### ADR-003: Replication with quorum WAL + replicated commit log

**Decision**: primary appends write to WAL and replicates to quorum before ack.
**Pros**

* RPO=0 for acknowledged writes under single failure.
* Fast recovery via replay; checkpoint reduces replay time.
  **Cons**
* Higher write latency; complexity of log replication and membership changes.
  **Mitigations**
* Batch + group commit; compress WAL; pipeline tokenization vs replication carefully.
* Make WAL the minimal durable record (tokens or doc + version), not expensive structures.

### ADR-004: Bounded refresh invariant (T_refresh ≤ 4s)

**Decision**: once write is durable, it becomes searchable within `T_refresh`.
**Pros**

* Clear user-facing freshness semantics; aligns with search expectations.
  **Cons**
* Requires near-real-time propagation of in-memory updates and replica visibility.
  **Mitigations**
* Separate “durable” from “search-visible” with explicit states and metrics.
* Ensure query path consults MemIndex + recent log tail if needed (optional design lever).

### ADR-005: Metadata service as source of truth for active manifests

**Decision**: metadata holds shard→manifest generation and segment lists; nodes watch it.
**Pros**

* Central coordination for routing + safe publication of segment sets.
  **Cons**
* Metadata becomes critical dependency; must be highly available.
  **Mitigations**
* Run metadata as replicated consensus-backed store; cache on eval nodes; degrade to cached mapping with TTL.

---

## 6) Data structures (conceptual)

### Per-shard logical state

* **Doc table**: `doc_id → {version, tombstone?, stored_fields_ptr, facet_ptrs…}`
* **Term dictionary** per segment: `term → posting_list_ptr`
* **Posting lists**: docID-ordered, skip lists for `advance(target)` and `next()`.
* **Facet columns**: columnar doc-values keyed by docID or dense ordinal.
* **Manifest**: `{generation, segments[], checksums, min/max docID, term stats, created_at, …}`

### Log & checkpoint

* **WAL** (per shard): append-only records: `{doc_id, version, op (upsert/delete), payload ref}`
* **Replicated commit log**: WAL replication stream with ordering; may be same artifact with replication semantics.
* **Checkpoint**: compact representation of latest durable state to bound replay time.

---

## 7) Control flow

### Write path (upsert/delete)

1. **Eval node** computes shard routing using cached mapping.
2. **Send to shard primary** (with request ID for idempotency).
3. Primary:

   * Validates version monotonicity (`incoming_version > stored_version`).
   * Appends to WAL; replicates to quorum; on quorum ack → “durable”.
   * Updates MemIndex and doc table (or queues for indexing) → “search-visible” within `T_refresh`.
4. Secondaries:

   * Apply WAL in order, update their MemIndex/doc table accordingly.
5. Reply to client with ack containing `{shard, primary_epoch, applied_version}`.

### Flush path

* Triggered by MemIndex size/time thresholds.
* Produces immutable segment files + a segment metadata record.
* Publishes by writing a new manifest generation and atomically swapping “active manifest pointer”.
* Old manifest remains valid for readers until they observe new generation (RCU-like).

### Merge path

* Select segments by policy (size tiers, delete ratio, age).
* Build merged segment off to the side.
* Atomically swap manifest to include merged segment and remove inputs.
* Garbage collect superseded segments after no readers remain (epoch-based cleanup).

### Query path

1. **Eval node** parses query, builds logical plan (AND/OR, facets).
2. Scatter to **one replica per shard** (prefer secondaries if safe to offload).
3. Each shard evaluates query over:

   * active immutable segments
   * MemIndex (and possibly recent log tail if used)
4. Shard returns top-K + scoring features.
5. Eval node merges, de-duplicates (docID includes shard prefix or global ID), reranks, returns results.
6. Partial results policy if some shards timeout (see SRE section).

---

## 8) Correctness invariants and how they’re enforced

You already listed the right invariants; here is how the design enforces them:

### 8.1 Reflexive consistency (doc↔term)

* **Write ordering**: apply a doc update as an atomic logical operation at the shard replica:

  * doc table updated with new version and content ref
  * inverted tokens applied to MemIndex for that version
* **Segment immutability**: once flushed, postings and dictionaries are fixed and checksummed.
* **Deletes**: represented as tombstones keyed by doc_id+version; query-time filters ensure stale postings don’t resurrect deleted docs.

### 8.2 Unique document identity

* Routing by hash ensures a single home shard for a docID at a time.
* During rebalancing, enforce “searchable in old OR new, never missing” via dual-read or forwarding (see §12).

### 8.3 Idempotency

* Every write has:

  * `doc_id`
  * `version`
  * `request_id` (optional but recommended)
* Store last applied version; reject or no-op if `incoming_version ≤ stored_version`.

### 8.4 Happens-before and ordering

* WAL defines the authoritative order per shard.
* Only the primary orders writes; secondaries apply in that order.
* Reject writes from stale primaries using **primary epoch/term** from consensus.

### 8.5 Visibility latency bound (`T_refresh`)

* Define explicit state:

  * `durable_at` (quorum WAL persisted)
  * `visible_at` (query path includes it)
* Monitor `visible_at - durable_at` and alarm on SLO breach.

### 8.6 Split-brain guard

* Consensus/lease per shard replica group:

  * at most one primary per shard term.
* All write requests include `(shard, primary_term)`; replicas reject if term mismatched.

---

## 9) Failure model and durability guarantees

### Failure cases

1. **Primary crash**

   * New primary elected.
   * Replays WAL to consistent point; resumes accepting writes.
2. **Secondary crash**

   * Catches up by log replay from last checkpoint offset.
3. **Network partition**

   * Minority partition cannot maintain primary lease; rejects writes.
4. **Disk full / IO errors**

   * Shard replica transitions to “degraded/unwritable”; triggers eviction, throttling, and alerting.
5. **Metadata service outage**

   * Eval nodes use cached mapping with TTL; restrict rebalancing/segment publication until metadata recovers.

### Durability levels (explicit)

* **Acked write durability**: persisted to quorum WAL → survives any single-node loss.
* **Search visibility**: bounded by `T_refresh`; may be delayed during severe overload (and should surface as SLO error budget burn).

---

## 10) SRE: Operational policies, observability, and incident response

### Golden signals

**Latency**

* end-to-end query latency (p50/p95/p99), plus per-shard breakdown and straggler attribution.
* write latency (client→primary ack), WAL fsync time, replication RTT.

**Traffic**

* QPS per endpoint, bytes in/out, query fanout counts.

**Errors**

* RPC errors by type (timeout, unavailable, rejected due to epoch mismatch, stale mapping).
* WAL append failures, manifest swap failures, checksum mismatches.

**Saturation**

* CPU utilization split by ingestion/query/merge.
* Disk IO (read/write iops, latency), disk usage by segment tiers.
* Memory: MemIndex size, cache hit rate, posting list cache, heap fragmentation.

### Key SLO instrumentation

* `T_refresh` distribution: `visible_at - durable_at` histogram.
* Replica lag: secondary applied log index vs primary.
* Merge backlog: pending bytes and estimated catch-up time.

### Load shedding + partial results

* Query deadlines: eval node enforces end-to-end budget; shards have sub-budgets.
* If some shards timeout:

  * return partial results with explicit `partial=true` and `missing_shards=N`.
  * optionally “hedge” to a second replica when a shard is slow (bounded fanout).

### Safe deployment

* Rolling restart with shard movement constraints:

  * never drop below 2 healthy replicas per shard.
  * drain/shard-unassign before stopping a node.
* Feature flags for query plan changes and merge policy changes.

### Oncall playbooks (minimum set)

* “Primary flapping” (lease instability) → check consensus quorum, network loss, clock skew.
* “Refresh SLO violation” → isolate whether WAL fsync, replication lag, MemIndex apply, or query visibility path.
* “Merge storm” → throttle compaction, raise segment size thresholds, apply IO budgets, temporarily increase cache.
* “Metadata outage” → freeze rebalances/manifest updates, serve queries on cached mapping.

---

## 11) Testing strategy aligned with invariants

### Step 5: Unit-testable components (gtest)

* Tokenization pipeline determinism (`token_stream`).
* Inversion correctness (`invert_tokens`) including positions/frequencies if used.
* Posting list encoding/decoding; skip list correctness (`advance`, `next`).
* Iterator algebra: `and_iterator`, `or_iterator` laws (commutativity where expected, idempotence of OR, etc.).
* Multi-way merge correctness against a reference evaluator.
* Manifest swap logic: atomicity and rollback behavior.
* Version monotonicity and idempotency rules.

### Step 6: Integration tests (single process + simulated cluster)

* Simulated eval + N shard replicas:

  * concurrent writers + readers with deterministic clock.
  * random faults: drop messages, delay replication, crash/restart nodes.
* Property-based checks:

  * after replay, state is functionally equivalent (same query results) to a reference model.
* “Crash at every line” style for critical state transitions:

  * during WAL append, after quorum, during flush, during manifest swap, during merge finalize.

### Step 7: Scale and chaos testing

* Soak tests with:

  * steady ingest + query mix; forced merge pressure.
  * injected failures: 1 node down, 2 nodes down (should degrade but not corrupt), network partition.
* Tail-latency drills:

  * slow disk injection (read/write latency), CPU steal, cache eviction storms.
* Correctness at scale:

  * sampled queries compared to a gold-standard offline index build.

---

## 12) Rebalancing / resharding strategy (routing stability)

### Requirements restated

During rebalance, a doc must be searchable in old or new shard, never missing, and avoid double-counting in final results.

### Proposed approach (practical MVP)

**Phase 1: Add new shard(s), dual-read**

* Metadata publishes new mapping but marks it “warming”.
* Eval nodes query both old and new for affected hash ranges, then **dedupe by doc_id** at merge.
* Writes go to the “new home” shard primary; old shard may receive forwarded deletes/tombstones.

**Phase 2: Backfill / migrate**

* Background job replays doc versions into new shard.
* Once lag is near zero, mark old shard range “read-only / draining”.

**Phase 3: Cutover**

* Metadata switches mapping to new only; disable dual-read.
* GC old shard segments after retention window.

**Tradeoffs**

* Dual-read increases query cost temporarily, but is the safest “never missing” mechanism.
* Dedupe is mandatory; embed shard identity into docIDs or keep global doc_id.

---

## 13) Security and compliance (brief MVP stance)

* mTLS between eval/data/metadata services.
* AuthZ on write APIs; rate limits per tenant.
* Audit logs for administrative operations (rebalance, force-merge, mapping changes).
* Encryption at rest for WAL and segment files if required.

---

## 14) Open questions / follow-on ADRs

1. **Query planning**: do we always scatter to all shards, or can we do term-driven shard pruning later?
2. **Indexing payload in WAL**: store raw doc, token stream, or inverted deltas? (performance vs replay speed tradeoff)
3. **Near-real-time visibility**: do we need “search the WAL tail” for strict `T_refresh` under heavy load?
4. **Facet storage format**: row-group vs per-column files; compression vs random access.
5. **Primary selection policy**: always query secondaries? (load isolation) vs query primary for freshest results.
6. **Segment GC safety**: epoch-based reclamation vs explicit reader leases.

---

## 15) Minimal “MVP-ready” definition of done

* End-to-end: route write → durable quorum ack → visible in queries within `T_refresh`.
* Query supports `q`, `q1 AND q2`, `q1 OR q2` with iterators + skip lists + multi-way merge.
* Flush produces immutable segment and publishes via manifest atomic swap.
* Merge merges segments and swaps manifest without blocking queries.
* Replica group enforces single-primary, WAL quorum durability, checkpoint/replay.
* Test suite covers invariants, crash/replay, concurrency, and basic chaos scenarios.
* Dashboards + alerts for refresh lag, replica lag, error rates, and merge backlog.

---

