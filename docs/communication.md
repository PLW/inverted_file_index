## Reference architecture: Top-level communication nodes for an orchestrated TCP/HTTP microservice cluster

You can treat **every node in the system as a specialization of a single “Communication Node” (CN) reference architecture**. Specialization happens by swapping in domain services (storage, compute, metadata) while keeping the same **top-level planes, ports, and operational contracts**.

---

## 1) The Communication Node (CN) baseline

Every node implements the same top-level “shell”, regardless of whether it’s a storage node, processing node, or metadata node.

### Planes (separation of concerns)

**A. North–South plane (Client/Edge)**

* Purpose: ingress/egress for user traffic and external integrations
* Protocols: HTTP/1.1, HTTP/2, gRPC (over HTTP/2), optional raw TCP for specialized data paths
* Typical components:

  * **Edge/API Gateway** (cluster-level) or **Node Gateway** (per-node)
  * AuthN/AuthZ enforcement, request shaping, routing, API versioning

**B. East–West plane (Service-to-service)**

* Purpose: internal RPC between microservices
* Protocols: gRPC/HTTP2 preferred; HTTP/1.1 acceptable; TCP where needed
* Typical components:

  * **Sidecar proxy** (service mesh style) or embedded proxy module
  * mTLS, retries/timeouts, load balancing, circuit breaking, telemetry

**C. Control plane (Orchestration + membership + configuration)**

* Purpose: lifecycle, placement, configuration distribution, leadership/coordination
* Protocols: HTTP/gRPC; plus a consensus protocol where required (Raft-style) for strongly consistent control state
* Typical components:

  * Node agent (kubelet-like) or orchestrator client
  * Membership/health reporting
  * Dynamic config/watch

**D. Data plane (Bulk transfer / streaming)**

* Purpose: large object transfer, replication, shuffle, snapshotting
* Protocols: dedicated gRPC streaming, HTTP range requests, QUIC optional, raw TCP optional
* Typical components:

  * Transfer manager with congestion control/backpressure
  * Checksums, chunking, resumable transfers

**E. Observability plane**

* Purpose: logs, metrics, traces, profiling, audit
* Protocols: OTLP/gRPC or HTTP; syslog optional
* Typical components:

  * Telemetry SDK + exporter/agent

### The CN internal modules (common to all nodes)

Think of these as the “top-level communication nodes” you standardize:

1. **Ingress Router**

* Terminates TLS (or passes through), parses L7 headers, routes by service/API, enforces rate limits/quotas.

2. **Egress/Client**

* A standard outbound stack: service discovery → load balancer → retries/timeouts → mTLS → tracing headers.

3. **Service Discovery + Naming**

* Logical service names resolve to endpoints (VIP/DNS/registry).
* Supports locality hints (rack/zone/region), canary subsets, and health-based endpoint removal.

4. **Policy & Identity**

* Node identity (SPIFFE-like model or your PKI)
* AuthZ checks (RBAC/ABAC), network policies, request signing for internal control APIs.

5. **Reliability Layer**

* Timeouts everywhere, bounded retries with jitter, hedging where appropriate
* Circuit breakers and bulkheads (per dependency)
* Admission control under load (load shedding)

6. **Config + Feature Flags**

* Dynamic config store/watch
* Rollouts with safety rails

7. **Telemetry**

* Structured logs, metrics, traces
* Correlation IDs enforced at ingress

8. **Health + Lifecycle**

* Liveness/readiness endpoints
* Draining on shutdown and pre-stop hooks for graceful removal from discovery

This CN shell is what “all other nodes are specializations” of.

---

## 2) Cluster top-level communication nodes (canonical roles)

Even if every node has the CN shell, a distributed system typically needs a few **cluster-level communication “front doors”**. These are also CNs, but with specific responsibilities.

### 2.1 Edge / API Gateway CN (north–south entry)

**Responsibilities**

* Public entrypoint; API routing; WAF; auth; quotas; versioning
* Optional protocol translation (REST ↔ gRPC)
* Multi-tenant isolation (headers/claims → policy)

**Patterns**

* Active-active replicas behind L4 load balancer
* Stateless, horizontal scale

### 2.2 Control Gateway CN (operators + automation entry)

**Responsibilities**

* Entry for admin/ops: deployment APIs, control actions, debug endpoints (locked down)
* Separates “operator traffic” from user traffic

**Patterns**

* Strongly authenticated, often restricted network access
* Rate limited and audited

### 2.3 Service Mesh/Discovery CN (or embedded, but logically top-level)

You can implement as:

* a dedicated discovery service (registry) + node agents, or
* DNS + endpoint controller, or
* mesh control plane distributing xDS-like configs

**Responsibilities**

* Membership, endpoint publishing, traffic policy distribution
* Certificate issuance/rotation (if you own mTLS)

### 2.4 Eventing / Message Bus CN (optional but common)

If you have workflows, background tasks, replication events:

* Kafka/NATS/Pulsar-style cluster (or embedded queue service)

**Responsibilities**

* Async decoupling, fanout, buffering, replay
* Enables eventual consistency paths cleanly

---

## 3) Specializing CNs into your node types

All nodes share the CN shell; specialization is “which domain services plug into it” and which plane dominates.

### A) Storage Node (SN = CN + storage services)

**Typical services**

* Object/chunk service (data plane heavy)
* Replication service
* Local indexing/cache service
* Background compaction/scrubbers

**Communication characteristics**

* Large transfers, long streams, strict backpressure
* Strong emphasis on checksums, resumable transfers, locality-aware routing

### B) Processing Node (PN = CN + compute services)

**Typical services**

* Task execution service (RPC control + streaming I/O)
* Shuffle/streaming data service
* Worker management

**Communication characteristics**

* Mix of RPC + streaming
* Needs robust admission control and queueing

### C) Metadata Node (MN = CN + metadata/coordination services)

**Typical services**

* Metadata API (strong consistency or well-defined consistency model)
* Leader election/consensus group (if required)
* Schema/catalog service, placement, partition maps

**Communication characteristics**

* Control plane heavy
* Often low latency, high correctness; may require quorum protocols

---

## 4) Recommended inter-service contracts (make these uniform)

### 4.1 API surface conventions

* **Every service** exposes:

  * `/health/live`, `/health/ready`
  * `/metrics`
  * `/debug/*` gated
* **Every request** carries:

  * `traceparent` (or equivalent), `x-request-id`
  * auth context (JWT/mTLS identity), tenant/workspace id

### 4.2 Discovery + routing

* Logical names: `svc.<domain>.<cluster>` or similar
* Support subsets: `svc.storage.read`, `svc.storage.write`, `svc.metadata.leader`

### 4.3 Timeouts/retries standard

* Default deadlines (e.g., 1–3s for control RPC, larger for streams)
* Retries only on idempotent operations; never retry non-idempotent without tokens

### 4.4 Failure semantics

* Explicit error taxonomy: `UNAVAILABLE`, `TIMEOUT`, `OVERLOADED`, `NOT_LEADER`, `STALE_EPOCH`
* Versioned/epoch-based routing to avoid split-brain writes

---

## 5) Deployment topology (simple starting point)

**Minimal viable cluster**

* 2–3× Edge/API Gateway CN
* 3–5× Metadata Node CN (if using consensus, keep odd count)
* N× Storage Node CN
* M× Processing Node CN
* Optional: message bus cluster

**Traffic flow**

* Clients → Edge/API Gateway → internal services via discovery/mesh
* Admins/automation → Control Gateway → control plane + node agents

---

## 6) Key design choices to decide early (and bake into the CN shell)

1. **Service-to-service security model**

* Prefer mTLS everywhere internally; identity-based authZ at service boundaries.

2. **Consistency model for metadata**

* If strong consistency: consensus group, leader routing, `NOT_LEADER` redirects.
* If eventual: explicit version vectors/epochs and conflict rules.

3. **Data-plane protocol**

* gRPC streaming is usually the best “one protocol” answer; raw TCP only where you can justify it.

4. **Backpressure + load shedding**

* Mandatory for stability: bounded queues, concurrency limits, token-based admission for expensive ops.

---

## 7) A concrete “CN reference template” you can hand to teams

**Each node must implement:**

* Ingress Router (HTTP/gRPC + optional TCP listener)
* Egress Client (discovery + LB + retries + mTLS + tracing)
* Control Agent (orchestrator integration, config watch)
* Telemetry (logs/metrics/traces)
* Health/Lifecycle endpoints
* Policy/Identity module
* Reliability defaults (timeouts, circuit breakers, admission control)

**Each specialized node adds:**

* Storage services OR compute services OR metadata services
* Node-specific ports, but registered under the same discovery scheme
* Node-specific SLOs, but measured via the same telemetry contracts


