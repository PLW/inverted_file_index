
1. **Define the core interfaces precisely** (pure abstract base classes only):

   * `IShardReplica`
   * `ISegment`
   * `IPostListIterator`
   * `IQueryExecutor`
   * `IWAL`
   * `IManifestStore`
   * `IReplicationProtocol`
   * `IDistributedQueryCoordinator`

2. **Freeze the state machine for `ShardReplica`**

   * `Follower`
   * `Primary`
   * `ReadOnly`
   * `Draining`
   * `Recovering`

3. **Write invariant-driven interface contracts**

   * What must be true before/after `flush()`
   * What must be true before/after `merge()`
   * What must be true before/after `applyWalRecord()`
   * What is guaranteed after `ackWrite()`

4. **Define ownership + lifetime model**

   * Who owns segments?
   * How readers hold a stable view (RCU-style generation pinning?)
   * How manifest swaps avoid use-after-free.

* Draft a **minimal C++-style interface skeleton (no implementation)**
* Or design the **ShardReplica state machine formally**
* Or design the **ActiveView / generation model** (this is usually the most subtle and error-prone part)
* Or define the **replication + WAL consistency contract** in detail

