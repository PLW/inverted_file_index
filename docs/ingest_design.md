Tokenization / HTML→text→tokens should live with the **shard’s write authority** (i.e., on the **data/index nodes**, specifically the **primary replica** for that shard), not on eval nodes.

A clean split that works well in practice:

## Recommended responsibility split

### Eval nodes (stateless-ish control + query)

* **Route writes**: `(doc_id/url) → shard primary`
* **Query scatter/gather + rerank**
* **Admission control**: timeouts, backpressure, load shedding, hedged reads
* **Optional**: lightweight validation (auth, size limits), but *no heavy parsing*

### Data / index nodes (authoritative write pipeline)

* **Own the WAL + replication** for the shard (primary orders writes; secondaries apply)
* **Store the canonical doc version metadata** (doc table: version/tombstone)
* **Do parsing + tokenization + inversion** and apply to MemIndex
* **Flush + merge + manifest publication**

This aligns the “what was indexed?” decision with the same component that:

* enforces **version monotonicity**
* defines **write ordering**
* guarantees **idempotency**
* measures and enforces **T_refresh**

## Why tokenization belongs on index nodes

1. **Determinism + replay**
   WAL replay must reproduce the exact indexed state. If eval nodes tokenize and you later replay raw docs on data nodes, you risk divergence (different tokenizer versions/configs). Putting tokenization at the shard primary ties it to the WAL ordering and the shard’s “truth.”

2. **Backpressure where it matters**
   Tokenization is CPU-heavy and bursty. If it happens on eval nodes, eval becomes a hot choke point and can collapse query latency. Keep eval nodes CPU-predictable.

3. **Scaling and isolation**
   Data nodes scale with shard count; parsing cost scales with ingestion volume. Eval nodes scale with query fanout/merge cost. Mixing them makes capacity planning harder.

4. **Security/sandboxing**
   HTML parsing is attack surface (weird encodings, decompression bombs, pathological markup). Better to sandbox this near the ingestion pipeline on data nodes, not on the query tier.

## But your note says: “eval nodes manage the WAL and write docs to the doc repo”

I’d strongly recommend **not** making eval nodes the WAL authority.

Instead:

### Best practice: shard-primary owns WAL; doc repo is a separate durable store

* **DocRepo** (object store / blob store / replicated KV) holds `{doc_id, version → raw_doc}`.
* Shard primary does:

  1. append WAL record `{doc_id, version, op, doc_ref}` (where `doc_ref` points to DocRepo)
  2. replicate WAL to quorum
  3. fetch raw doc if needed (or receive it inline), tokenize/invert, apply MemIndex
  4. ack

This gives you:

* **durability** (WAL quorum) and **retrievability** (DocRepo) without making eval nodes stateful.
* clean failure recovery: shard can replay WAL and re-fetch doc content by `doc_ref`.

### Alternative (also valid): WAL stores pre-tokenized payload

If you want faster replay and stable indexing semantics:

* shard primary tokenizes, then WAL stores `inverted_tokens` (or token stream) instead of raw doc.
* DocRepo still stores raw doc for snippet/highlighting/future re-indexing.
  This reduces replay CPU and avoids “tokenizer version drift,” at the cost of larger WAL.

## A good compromise architecture

* **Eval nodes**: route + auth + rate limits + query.
* **Ingest gateway** (optional separate tier): upload doc to DocRepo, compute checksum, return `doc_ref`.
* **Shard primary**: WAL/replication + tokenization/inversion + MemIndex update.
* **Secondaries**: apply WAL records (either inverted payload or doc_ref-based with deterministic tokenizer version pinned by shard term).

## If you insist on tokenizing on eval nodes…

Only do it if:

* you can guarantee tokenizer version/config is identical and pinned,
* you WAL the *tokenized result* (so replay is identical),
* you accept eval nodes becoming CPU-heavy and potentially impacting query latency.

Even then, I’d usually split “eval” into two services: **QueryCoordinator** and **IngestCoordinator**, so query isn’t dragged down by ingestion parsing.

---

