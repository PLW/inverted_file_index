
You are my C++ system architecture and design expert consultant.
We are designing a distributed inverted file index system.
We need a high-level design document that describes the system
but does not contain actual C++ implementations.  The document
should include tradeoff analysis, (i.e.) act as ADR (Architecture
Decision Record). It should address NFRs (Non-Functional Requirements),
SLOs (Service Level Objectives), and SRE (Site Reliability Engineering).

---

Following are details of the system objectives, requirements and
basic design elements.

---

**Basic system elements and functions**

* The system distributes the index into per-host `shards`, and
* within each shard partitions the index into `segments`.  
* Sharding is based on URL hash. 
* New documents are dipatched to the appropriate shard, where
* they are tokenized and inverted into an in-memory inverted index. 
* The in-memory index is periodically flushed to disk as an immutable
  persistent segment.
* Immutable segments are periodically merged.
* The set of `in-play` segments is maintained by a metadata service that
  contains the (hash -> host) mapping, and the (shard -> active segments) list.
* Queries are evaluated by scatter-gather broadcast to all shards,
  followed by merge results and re-rank.

The system consists of 
* some number of independent `eval nodes` and
* some number of `data nodes`.
* Eval nodes maintain a copy of the (URL -> shard) mapping and dispatch
  insert requests to the appropriate shard.
* Data nodes process insert/delete requests independently.
* Eval nodes process queries by broadcasting to one replica in every
  shard, then gathering, and re-ranking the results.

---

**MVP (minimum viable product) requirements**
  * `token_stream(doc, doc_id)`
  * `invert_tokens(token_stream, doc_id)`
  * `update_index(inverted_tokens)`
  * `posting_lists` with skip lists 
  * `multi_way_merge` of posting lists query optimization
  * `iterator` composable iterator interface for query results
  * `postlist_iterator`
  * `columnar_facets` as instances of `postlist_iterator`
  * `and_iterator` : public `postlist_iterator`
  * `or_iterator` : public `postlist_iterator`
  * `flush_index()` => `persistent_segment_id`
  * `merge(seg1, seg2)` => `seg3`
  * `query(q)` => `postlist_iterator`
  * `query(q1 AND q2)` => `and_iterator(it1(q1),it2(q2))`
  * `query(q1 OR q2)` => `or_iterator(it1(q1),it2(q2))`
  * `doc(doc_id)` => `doc`
  * `metadata(doc_id)` => `doc_metadata`
  * `metadata()` => `shard_status`

---

**Replication**

Each shard runs at least three replicas with one primary and at least two
secondaries.  Each shard:
  * Maintains a Write-Ahead Log (WAL)
  * Maintains replicated commit logs
  * Supports checkpoint + replay
  * Maintains an immutable file manifest + atomic swap (on merge)

---

**Invariants**
1. Term-Document Mapping Invariants
 The core integrity of the index depends on the bidirectional relationship
 between documents and the terms they contain.

* Reflexive Consistency: For every document $D$ containing term $T$, the
  posting list $L_T$ must contain a reference to $D$. Conversely, if $L_T$
  contains $D$, term $T$ must actually exist in the current version of document
  $D$.

* Unique Document Identity: A document $D$ must be mapped to exactly one
  primary shard at any given time (usually via $hash(docID) \pmod N$) to prevent
  duplicate entries in global result sets.

* Idempotency of Updates: Re-indexing the same document version $V_1$ must
  result in a state identical to a single indexing event. This is crucial for
  handling network retries.

2. Distributed State & Partitioning
Because the index is distributed, you must maintain invariants regarding how
data is sliced and moved.

* Shard Completeness: The union of all shards must equal the complete set of
  indexed documents. $\bigcup_{i=1}^{n} S_i = \mathcal{D}_{total}$.

* Term Locality (for Term-Partitioned): If using term-partitioning, all
  occurrences of a term $T$ across the entire corpus must reside on the same
  logical shard.

* Routing Stability: During a "rebalance" or "resharding" event, a document
  must be searchable in either the old shard or the new shard, but never
  "missing" or "double-counted" in a final merged result set.

3. Concurrency and Ordering
In a high-velocity system, the order of operations determines the "truth" of the
index.

* Happens-Before Consistency: If Update A (Delete $D$) is acknowledged before
  Update B (Re-add $D$), the index must not reflect $D$ in a stale state.

* Monotonic Versioning: Every document must have an associated version number
  or timestamp. The system must reject any write where $Version_{incoming} \le
  Version_{stored}$ to prevent "out-of-order" synchronization from overwriting
  newer data.

* Visibility Latency Bound: The "Refresh" invariant. Once a write is
  acknowledged as "durable," it must become "searchable" within a strictly
  defined time bound ($T_{refresh}$), ensuring eventual consistency across all
  replicas.

4. Durability and Replication
These invariants ensure the system survives node failures.

* Write-Ahead Integrity: No update is considered "committed" until it is
  persisted to a non-volatile Write-Ahead Log (WAL) or a quorum of replicas.

* Replica Convergence: In the absence of new updates, all replicas of Shard
  $S_i$ must eventually reach a bit-identical state (or functionally identical
  state regarding query results).

* The "Split-Brain" Guard: The system must never allow two nodes to believe
  they are the "Primary" for the same shard simultaneously. This is usually
  enforced via a consensus protocol (Raft/Paxos) or a lease mechanism.
      
---

**Design doc structure**

We address the task of constructing the distributed inverted index
by decomposing the problem into small, manageable steps:

Step 1: define the data structures;
Step 2: outline the system at a high level: modules, classes, methods;
Step 3: outline the data and control flow among the components;
Step 4: catalog the invariants which must hold for the system state;
Step 5: define the unit-testable (gtest) components which allow
  incremental testing for correctness with respect to invariants and
  gold-standard expected behavior;
Step 6: define a general integration testing strategy using gtest:
  worker simulations, crash testing, high concurrency, gold-standard
  behavioral checking.
Step 7: define a scale-testing strategy, using the "default mode is failure,
  correctness must be engineered" model.
Step 8: Durability guarantees that address:
  * Partial failure
  * Split-brain
  * Node loss
  * Rebalancing events
---
