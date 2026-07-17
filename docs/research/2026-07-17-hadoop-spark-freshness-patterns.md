# Hadoop And Spark Freshness Patterns For LLMWiki Serve

Date: 2026-07-17

Status: research note for future design. This is not a product commitment.

## Question

Can older Hadoop/Spark research and later lakehouse systems inform how
`llmwiki-serve` should detect wiki/source changes without rescanning a large
source tree on every request?

Short answer: yes. The ecosystem moved from listing directories and watching
events toward explicit commit logs, manifests, snapshots, and transaction
timelines. That maps strongly to `llmwiki-serve` producer manifest mode.

## Design Lineage

### HDFS inotify

HDFS inotify streams namespace edit-log events from the NameNode. It avoids full
namespace scans by letting clients consume ordered event batches.

Useful ideas:

- use high-watermarks or transaction ids;
- expose gaps or missed events explicitly;
- force a full resync when the consumer is too far behind.

Limitations:

- this is an event stream, not a durable content manifest;
- consumers must handle missing-event conditions and restart uncertainty.

Mapping:

- Filesystem watchers in `llmwiki-serve` should be dirty signals only.
- On watcher overflow, health failure, or restart uncertainty, fall back to
  strict source signature scan or producer manifest validation.

Sources:

- https://hadoop.apache.org/docs/current/api/org/apache/hadoop/hdfs/client/HdfsAdmin.html
- https://hadoop.apache.org/docs/r3.4.1/api/org/apache/hadoop/hdfs/inotify/package-summary.html
- https://hadoop.apache.org/docs/current/api/org/apache/hadoop/hdfs/DFSInotifyEventInputStream.html

### HDFS snapshots and snapshotDiff

HDFS snapshots are read-only point-in-time copies. The docs describe snapshot
creation as O(1) excluding inode lookup, with additional memory proportional to
modified files/directories. `snapshotDiff` reports creates, deletes, modifies,
and renames between snapshots or between a snapshot and current state.

Useful ideas:

- immutable generation points;
- diff from generation N to generation N+1;
- changes can be summarized without scanning all current files.

Limitations:

- snapshot diff is HDFS-specific;
- diff output is not guaranteed to be the exact operation sequence;
- rename edge cases matter.

Mapping:

- A producer manifest should carry `previous_generation`, `generation_id`, and
  optionally changed paths.
- Serve can validate or rebuild from a generation boundary rather than treating
  an arbitrary mutable directory as a stable source.

Source:

- https://hadoop.apache.org/docs/stable/hadoop-project-dist/hadoop-hdfs/HdfsSnapshots.html

### DistCp `-update` and `-diff`

DistCp supports incremental copy modes. `-update` compares existing target files,
while `-diff` uses HDFS snapshot differences.

Useful ideas:

- explicit source/target baseline;
- incremental operation only works when the previous baseline is known.

Limitations:

- update-by-comparison is weaker than a content manifest;
- object stores and clocks can complicate comparisons;
- snapshot diff requires a strict HDFS snapshot setup.

Mapping:

- Producer manifest mode should require a known baseline/generation.
- If a manifest says it is based on generation N, serve should trust it only if
  the current served generation is N or a safe full rebuild is performed.

Source:

- https://hadoop.apache.org/docs/current/hadoop-distcp/DistCp.html

### Hadoop committers and `_SUCCESS`

Classic Hadoop output commit uses temporary task output and a job commit step.
The old `_SUCCESS` marker only proves that the job commit finished; it does not
describe exact content.

Useful ideas:

- never expose task/staging output as committed state;
- publish a final marker after successful commit.

Limitations:

- a zero-byte `_SUCCESS` file is too weak for source freshness;
- rename-heavy commit protocols are unsafe/slow on object stores.

Mapping:

- A bare `.success` marker is not enough for `llmwiki-serve`.
- The marker must be a schema-versioned manifest with projection signature,
  producer identity, generation, file inventory, and status.

Sources:

- https://hadoop.apache.org/docs/current/api/org/apache/hadoop/mapreduce/OutputCommitter.html
- https://hadoop.apache.org/docs/current/api/org/apache/hadoop/mapreduce/lib/output/FileOutputCommitter.html

### Hadoop manifest committer

The manifest committer writes task manifests and a JSON `_SUCCESS` summary with
diagnostics and statistics. This was designed for cloud object storage where
directory listing and rename behavior can be expensive or unsafe.

Useful ideas:

- workers/producers write manifests;
- commit aggregates manifests;
- final summary is JSON and inspectable;
- operations include diagnostics and IO statistics.

Limitations:

- still depends on object-store semantics and commit protocol details;
- full file lists can be too large for a single summary.

Mapping:

- Best Hadoop-era precedent for `llmwiki-projection-manifest.json`.
- For very large wiki roots, support chunked manifests referenced by a small
  current pointer.

Source:

- https://hadoop.apache.org/docs/r3.4.1/hadoop-mapreduce-client/hadoop-mapreduce-client-core/manifest_committer.html

### S3A committers and object-store consistency

Hadoop S3A docs explain why classic rename-based commit is dangerous on S3-like
object stores. S3A committers avoid treating object stores as POSIX directories.

Useful ideas:

- do not rely on directory rename for commit if the backing store cannot make it
  atomic;
- publish immutable outputs and commit metadata;
- failed commits need explicit cleanup/rollback handling.

Limitations:

- object stores differ;
- S3-compatible does not always mean AWS S3 semantics.

Mapping:

- For object-store-backed LLMWiki variants, use immutable generation prefixes
  and a final current pointer/manifest.
- Do not infer completeness from directory listing alone.

Sources:

- https://hadoop.apache.org/docs/current/hadoop-aws/tools/hadoop-aws/committers.html
- https://hadoop.apache.org/docs/stable/hadoop-aws/tools/hadoop-aws/s3guard.html
- https://aws.amazon.com/s3/consistency/

## Spark And Lakehouse Patterns

### Spark Structured Streaming file source

Spark file streaming processes atomically placed files and keeps checkpoint
state about already processed files. It exposes controls such as
`maxFilesPerTrigger`, `maxBytesPerTrigger`, `latestFirst`, `maxFileAge`,
`maxCachedFiles`, and source cleanup options.

Useful ideas:

- files should be atomically placed into watched directories;
- checkpoints remember already processed input;
- cache/listing knobs trade off cost and latency;
- cleanup/archive can reduce future listing cost.

Limitations:

- checkpoint state is query-local, not a portable source manifest;
- direct file source still depends on directory discovery;
- processing order should not be treated as a correctness guarantee.

Mapping:

- Useful support for watcher/dirty-flag mode.
- Not sufficient as the canonical freshness authority for `llmwiki-serve`.

Sources:

- https://spark.apache.org/docs/latest/streaming/index.html
- https://spark.apache.org/docs/latest/streaming/apis-on-dataframes-and-datasets.html

### Databricks Auto Loader

Auto Loader separates directory listing from file notification mode and persists
discovered file metadata in RocksDB at the checkpoint location. Databricks says
it can process very large numbers of files, recommends notification mode for
many workloads, and notes that discovery/processing order is not guaranteed.

Useful ideas:

- scale comes from persisted discovery state;
- notifications reduce listing cost but do not define ordering;
- exactly-once depends on checkpoint state.

Limitations:

- Databricks-specific implementation;
- checkpoint state is ingestion pipeline state, not a generic wiki contract.

Mapping:

- Cloud notifications should only set dirty.
- Serve should still validate a manifest/generation before updating projection.
- A Redis projection cache should be keyed by source/generation/signature, not
  by notification sequence alone.

Source:

- https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/

### Delta Lake transaction log

Delta Lake uses a transaction log to provide ACID semantics over files. Readers
select data files using the log; writers write data files first, then commit a
new log entry that atomically records adds/removes and metadata changes.

Useful ideas:

- transaction log is source of truth;
- readers tail the log for incremental processing;
- checkpoints compact state;
- files are added/removed logically.

Limitations:

- Delta protocol is a full table format, more complex than needed for Markdown
  wiki projection.

Mapping:

- Strongest direct analogy for `llmwiki-serve`.
- Producer manifest should be a small transaction/checkpoint record:
  `source_id`, `generation`, `projection_signature`, changed files, producer
  version, timestamps, and status.

Source:

- https://github.com/delta-io/delta/blob/master/PROTOCOL.md

### Apache Iceberg metadata, snapshots, and manifests

Iceberg tracks table state in metadata files. A current metadata pointer is
atomically swapped on commit. Snapshots reference manifest lists; manifests list
data/delete files and include metrics. The spec explicitly targets O(1) remote
calls for planning rather than O(n) partition/file listing.

Useful ideas:

- current pointer to immutable metadata;
- snapshot isolation;
- manifest list summaries;
- manifests can be reused across snapshots.

Limitations:

- full Iceberg semantics are beyond llmwiki-serve needs.

Mapping:

- Use `.llmwiki/current.json` as a current pointer.
- Reference immutable generation manifests.
- Add chunked manifests or summary fields if wiki roots become very large.

Source:

- https://iceberg.apache.org/spec/

### Apache Hudi timeline and metadata table

Hudi tracks table actions in a timeline with action states such as `REQUESTED`,
`INFLIGHT`, and `COMPLETED`. It also maintains table metadata to avoid expensive
cloud listing and footer/stat scans.

Useful ideas:

- explicit lifecycle states;
- only completed actions are readable as committed state;
- metadata table avoids expensive listing;
- rollback/cleaning are first-class table actions.

Limitations:

- Hudi is a full data lake storage layer.

Mapping:

- Producer output should have lifecycle states.
- Serve must ignore staging/inflight output.
- Manifest mode should require `status: committed` or equivalent.

Sources:

- https://hudi.apache.org/docs/timeline/
- https://hudi.apache.org/docs/next/metadata/

## Implications For `llmwiki-serve`

The mature direction is:

1. Keep strict source signature scan as the default.
2. Treat local filesystem watchers, HDFS inotify, cloud file notifications, and
   Watchman clocks as dirty hints, not correctness authorities.
3. Add a producer manifest mode for controlled ingest/compile pipelines.
4. Publish immutable generations and atomically update a current pointer.
5. Key memory/Redis projection cache by `namespace + source_id +
   projection_signature`.
6. Fall back to strict scan on malformed, stale, missing, unknown, or
   non-atomic manifest state.

## Recommended Manifest Model

Use a two-level shape:

```text
.llmwiki/
  current.json
  generations/
    <generation-id>/
      manifest.json
      files-manifest-000.json
      files-manifest-001.json
```

`current.json` should be small:

```json
{
  "schema_version": "llmwiki.current.v1",
  "source_id": "project-alpha",
  "generation_id": "2026-07-17T12-00-00Z-abc123",
  "sequence": 42,
  "manifest_digest": "sha256:..."
}
```

`manifest.json` should be the authority:

```json
{
  "schema_version": "llmwiki.projection-manifest.v1",
  "source_id": "project-alpha",
  "producer": {"name": "llm-wiki-compiler", "version": "x.y.z"},
  "generation": {
    "id": "2026-07-17T12-00-00Z-abc123",
    "sequence": 42,
    "previous_generation": "2026-07-17T11-00-00Z-def456",
    "status": "committed",
    "started_at": "2026-07-17T12:00:00Z",
    "completed_at": "2026-07-17T12:00:03Z"
  },
  "projection": {
    "signature": "sha256:...",
    "adapter": "llmwiki-markdown",
    "page_count": 123,
    "graph_node_count": 456,
    "graph_edge_count": 789
  },
  "inputs": {
    "strategy": "producer-verified",
    "tree_digest": "sha256:...",
    "watchman_clock": "c:123:456"
  },
  "outputs": {
    "file_count": 123,
    "byte_count": 456789,
    "manifest_files": [
      {"path": "files-manifest-000.json", "size": 12345, "digest": "sha256:..."}
    ]
  }
}
```

This is closer to Iceberg/Delta/Hudi than to a simple watcher. That is the right
direction if `llmwiki-serve` needs to serve large AX knowledge roots reliably.

## Practical Decision

For the current repo:

- `--refresh-interval-seconds`: useful ergonomic throttle, but intentionally
  allows bounded staleness.
- `--producer-manifest`: should evolve from a single marker into the manifest
  model above.
- `watchfiles`/`watchdog`: useful future dirty providers.
- Watchman: useful advanced backend where operators already run it.
- Redis: derived projection cache only; not source of truth.
