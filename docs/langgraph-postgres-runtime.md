# LangGraph Postgres Runtime — Reverse-Engineered Reference

> **Scope notice.** This file is characterization for reference only. It was produced by
> inspecting the pinned oracle virtual environment at
> `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api==0.12.6` and,
> where that venv has no visibility, by inspecting the OSS packages
> `langgraph-checkpoint-postgres` / `langgraph.store.postgres` and public docs.
> **Nothing from the oracle venv is copied into `server/`.** The clean-room boundary
> (`tests/server/test_license_boundary.py`, `tests/server/test_scaffold.py`) forbids
> importing or vendoring `langgraph_api` code into `server/` — this document
> describes and cites, it does not vendor.
>
> **WARNING: oracle internals characterize `langgraph-api==0.12.6` specifically
> and must NOT be read as proof of the OSS `langgraph-checkpoint-postgres` /
> `langgraph.store.postgres` API versions actually being installed in this repo.
> Todos 6 through 8 will verify the *actually installed* packages against the
> live environment, not this doc.** See `tests/server/oracle/README.md` for the
> pinning caveat: the repo dev venv resolves `langgraph-api==0.13.0` via
> `pyproject.toml` `constraint-dependencies` and is NOT the characterization source;
> only the oracle venv (`tests/server/oracle/requirements.txt` with `--no-config`)
> pins `0.12.6`.

*Generated: 2026-08-23. Oracle version: `langgraph-api==0.12.6`
(pinned pair `langgraph-cli[inmem]==0.4.31` declares `langgraph-api>=0.5.35,<1.0.0`,
so `0.12.6` is in range — `tests/server/oracle/README.md:7-15`).*

---

## How this document is organized

Three clearly separated layers, each with its own source of truth:

| Layer | Package / location | What it owns |
|-------|--------------------|--------------|
| **(a)** | OSS `langgraph-checkpoint-postgres` (`langgraph.checkpoint.postgres`) | Checkpoint saver tables + migrations + psycopg connection contract |
| **(b)** | OSS `langgraph.store.postgres` (`langgraph.store.postgres`) | `store` + `store_vectors` tables + `store_migrations`/`vector_migrations` + pgvector + `PoolConfig` |
| **(c)** | Platform server `langgraph-api==0.12.6` (oracle venv: `tests/server/oracle/.venv/.../langgraph_api/`) | `assistant`/`assistant_versions`/`threads`/`runs`/`crons` ownership, `DATABASE_URI`, pool sizing |

Every factual claim cites either an exact `tests/server/oracle/.venv/...` file and line,
or a public-docs URL. Anything the oracle venv could not confirm is marked
**`UNVERIFIED`** and cites its public source instead.

---

## (a) Checkpoint saver — `langgraph-checkpoint-postgres`

> **Provenance: `UNVERIFIED` against the oracle venv.**
> The oracle venv (`langgraph-api==0.12.6`) does not vendor the OSS checkpointer.
> `grep -rn "psycopg\|checkpoint_migrations" tests/server/oracle/.venv --include="*.py"`
> returns only `langgraph_api/schema.py:391` (a comment about `checkpoint_blobs`),
> no DDL. Postgres persistence in this version is delegated to the Go core over
> gRPC (see layer (c)). Facts below are from the separately installed OSS package
> `langgraph-checkpoint-postgres==3.1.2` (`/tmp/pgdl/langgraph/checkpoint/postgres/base.py`)
> and the canonical GitHub source, cited as `UNVERIFIED` with public URLs.

### Package identity

* Import path `langgraph.checkpoint.postgres` — public docs: `https://langchain-ai.github.io/langgraph/reference/checkpoints/#postgres`
* GitHub source: `https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py`
* OSS package inspected for this section: `langgraph-checkpoint-postgres==3.1.2`
  (`/tmp/pgdl/langgraph/checkpoint/postgres/base.py:1-92`, `__init__.py:40-84`).
  **`UNVERIFIED` — not present in oracle venv; version 3.1.2 is the latest OSS at
  time of writing and may differ from whatever version todos 6-8 find installed.**

### Table set

| Table | Purpose | DDL citation |
|-------|---------|--------------|
| `checkpoint_migrations` | Single-column version counter | `base.py:44-46` — `CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)` — [`UNVERIFIED` • OSS `base.py:43-46`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py#L44-L46) |
| `checkpoints` | One row per checkpoint | `base.py:47-56` — columns `thread_id TEXT NOT NULL`, `checkpoint_ns TEXT NOT NULL DEFAULT ''`, `checkpoint_id TEXT NOT NULL`, `parent_checkpoint_id TEXT`, `type TEXT`, `checkpoint JSONB NOT NULL`, `metadata JSONB NOT NULL DEFAULT '{}'`; `PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)` — [`UNVERIFIED` • OSS `base.py:47-56`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py#L47-L56) |
| `checkpoint_blobs` | Non-primitive channel values (and `_DeltaSnapshot`) | `base.py:57-65` — columns `thread_id`, `checkpoint_ns`, `channel`, `version`, `type`, `blob BYTEA`; `PRIMARY KEY (thread_id, checkpoint_ns, channel, version)` — [`UNVERIFIED` • OSS `base.py:57-65`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py#L57-L65) |
| `checkpoint_writes` | Pending writes per checkpoint+task | `base.py:66-77` — columns `thread_id`, `checkpoint_ns`, `checkpoint_id`, `task_id`, `idx INTEGER NOT NULL`, `channel`, `type`, `blob BYTEA NOT NULL`; `PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)` — [`UNVERIFIED` • OSS `base.py:66-77`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py#L66-L77) |

### MIGRATIONS list (versioned, position = version)

`MIGRATIONS` is a plain Python list whose index is the migration version; `setup()`
reads `SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1` and applies
`MIGRATIONS[version+1:]` (`__init__.py:92-108` — [`UNVERIFIED` • OSS `__init__.py:92-108`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py#L92-L108)).

Current OSS list (`base.py:43-91` — [`UNVERIFIED` • OSS `base.py:43-91`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py#L43-L91)):

| v | SQL | Notes |
|---|-----|-------|
| 0 | `CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY);` | Bootstrap |
| 1 | `CREATE TABLE IF NOT EXISTS checkpoints (...);` | PK `(thread_id, checkpoint_ns, checkpoint_id)` |
| 2 | `CREATE TABLE IF NOT EXISTS checkpoint_blobs (...);` | PK `(thread_id, checkpoint_ns, channel, version)` |
| 3 | `CREATE TABLE IF NOT EXISTS checkpoint_writes (...);` | PK `(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)` |
| 4 | `ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;` | Allows `empty` sentinel rows |
| 5 | `SELECT 1;` | No-op placeholder (keeps version numbers stable after an empty migration was removed) |
| 6 | `CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);` | **CONCURRENTLY** — online index build |
| 7 | `CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);` | **CONCURRENTLY** |
| 8 | `CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);` | **CONCURRENTLY** |
| 9 | `ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';` | `task_path` addition (`UNVERIFIED` • OSS `base.py:90`) |

All three `CONCURRENTLY` index migrations and the `task_path` column are part of the
`UNVERIFIED` OSS MIGRATIONS list — the oracle venv's Go core migrations live at
`MIGRATIONS_PATH=/storage/migrations` (`config/__init__.py:56`) and are not visible
from Python.

### Connection requirements

`UNVERIFIED` — psycopg contract from the OSS saver (`__init__.py:76-78`,
`base.py` pool `kwargs`):

* `Connection.connect(conn_string, autocommit=True, prepare_threshold=0, row_factory=dict_row)`
  — [`UNVERIFIED` • OSS `__init__.py:76-78`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py#L76-L78)
  and `store/postgres/base.py:813-819` for the pool variant.
* `autocommit=True` — required so the stateless `setup()`/`put`/`delete_thread` calls
  do not require an explicit transaction.
* `prepare_threshold=0` — disables server-side prepared statements; avoids
  `"prepared statement already exists"` errors when multiple pooled connections
  share the same query text.
* `row_factory=dict_row` (i.e. `psycopg.rows.dict_row`) — rows are returned as
  `dict`s, expected by `SELECT_SQL` consumers (`base.py:93-118`).

Pool variant (`store/postgres/base.py:808-822` shows the canonical pool kwargs):

```python
ConnectionPool(
    conn_string,
    min_size=pc.pop("min_size", 1),
    max_size=pc.pop("max_size", None),
    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row, ...},
)
```

[`UNVERIFIED` • OSS `store/postgres/base.py:808-822`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py) /
[`UNVERIFIED` • OSS `store/postgres/base.py:812-819`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L808-L822)

---

## (b) Store — `langgraph.store.postgres` (`langgraph.store.postgres`)

> **Provenance: `UNVERIFIED` against the oracle venv** — same reason as (a).
> `grep -rn "store_migrations\|store_vectors" tests/server/oracle/.venv` returns
> nothing; the platform `langgraph_api/store.py:78-80` only notes that a custom
> `path` replaces *"the default postgres + pgvector store"*.
> Facts below are from `langgraph-store-postgres` (co-distributed with
> `langgraph-checkpoint-postgres`, inspected at `/tmp/pgdl/langgraph/store/postgres/base.py`).

### Package identity

* Import path `langgraph.store.postgres` — public docs: `https://langchain-ai.github.io/langgraph/reference/store/#postgres`
* GitHub source: `https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py`
* Inspected package: `langgraph-checkpoint-postgres==3.1.2` bundle at
  `/tmp/pgdl/langgraph/store/postgres/base.py:1-146`.

### Table set

| Table | Purpose | DDL citation |
|-------|---------|--------------|
| `store` | Key/value JSONB store, `prefix` = dot-joined namespace | `base.py:64-74` — `prefix text NOT NULL, key text NOT NULL, value jsonb NOT NULL, created_at/updated_at TIMESTAMPTZ, expires_at TIMESTAMPTZ, ttl_minutes INT`; `PRIMARY KEY (prefix, key)` — [`UNVERIFIED` • OSS `base.py:64-74`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L64-L74) |
| `store_vectors` | pgvector embeddings for semantic search | `base.py:104-115` — `prefix text NOT NULL, key text NOT NULL, field_name text NOT NULL, embedding %(vector_type)s(%(dims)s), created_at/updated_at`; `PRIMARY KEY (prefix, key, field_name)`, `FOREIGN KEY (prefix, key) REFERENCES store(prefix, key) ON DELETE CASCADE` — [`UNVERIFIED` • OSS `base.py:104-115`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L104-L115) |

Migrations tables (created lazily in `setup()` via `_get_version` helper,
`base.py:1123-1150`):

* `store_migrations (v INTEGER PRIMARY KEY)` — [`UNVERIFIED` • OSS `base.py:1123-1130`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L1123-L1130)
* `vector_migrations (v INTEGER PRIMARY KEY)` — [`UNVERIFIED` • OSS `base.py:1151-1153`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L1151-L1153)

### MIGRATIONS / VECTOR_MIGRATIONS

`MIGRATIONS: Sequence[str]` (`base.py:63-90` — [`UNVERIFIED` • OSS `base.py:63-90`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L63-L90)):

| v | SQL |
|---|-----|
| 0 | `CREATE TABLE IF NOT EXISTS store (...)` |
| 1 | `CREATE INDEX CONCURRENTLY IF NOT EXISTS store_prefix_idx ON store USING btree (prefix text_pattern_ops)` |
| 2 | `ALTER TABLE store ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ, ADD COLUMN IF NOT EXISTS ttl_minutes INT` |
| 3 | `CREATE INDEX IF NOT EXISTS idx_store_expires_at ON store (expires_at) WHERE expires_at IS NOT NULL` |

`VECTOR_MIGRATIONS: Sequence[Migration]` (`base.py:92-145`):

| v | SQL | Condition |
|---|-----|-----------|
| 0 | `DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN CREATE EXTENSION vector; END IF; END $$;` | always |
| 1 | `CREATE TABLE IF NOT EXISTS store_vectors (...)` with `%(vector_type)s(%(dims)s)` templated from `index_config["dims"]` / `vector_type` | always |
| 2 | `CREATE INDEX CONCURRENTLY IF NOT EXISTS store_vectors_embedding_idx ON store_vectors USING %(index_type)s (embedding %(ops)s)%(index_params)s` | only if `index_config` and `kind != "flat"` (`base.py:130-131`) |

### pgvector requirement

* `store_vectors` requires the `vector` extension (`base.py:92-101`).
* When a `PostgresStore` is constructed with `index={"dims": 1536, "embed": ...}`,
  `dims` and `vector_type` are validated (`base.py:1158-1177`) and the `store_vectors`
  table and its ANN index are created on `setup()`.
* ANN index types: `hnsw` (default), `ivfflat`, or `flat` (`base.py:1237-1275`).
  No index is created for `flat`.
* Distance types: `l2`, `inner_product`, `cosine` (default `cosine`);
  operator class is derived in `_get_vector_type_ops` (`base.py:1205-1235`).

Public docs: `https://github.com/pgvector/pgvector` and
`https://langchain-ai.github.io/langgraph/reference/store/#postgres-vector-search`.

### PoolConfig

`PoolConfig` TypedDict (`base.py:151-176` — [`UNVERIFIED` • OSS `base.py:151-176`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L151-L176)):

```python
class PoolConfig(TypedDict, total=False):
    min_size: int        # defaults to 1 (from_conn_string: pc.pop("min_size", 1))
    max_size: int | None # None means unlimited
    kwargs: dict         # extra Psycopg connection kwargs, merged with
                         # {autocommit: True, prepare_threshold: 0, row_factory: dict_row}
```

Construction pattern (`base.py:782-832`):

```python
PostgresStore.from_conn_string(
    conn_string,
    pool_config={"min_size": 1, "max_size": 20, "kwargs": {...}},
    index={"dims": 1536, "embed": embeddings},
    ttl={"default_ttl": 60.0, "sweep_interval_minutes": 5},
)
```

[`UNVERIFIED` • OSS `base.py:783-822`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L783-L822)
and generic checkpointer pool at `langgraph-checkpoint-postgres==3.1.2`.

Note: the platform server's own pool knob (`LANGGRAPH_POSTGRES_POOL_MAX_SIZE`, below)
is a **separate** concern from this OSS `PoolConfig.max_size`; they operate at
different layers (platform replica pool vs. direct `PostgresStore` pool).

---

## (c) Platform server — `langgraph-api==0.12.6` (oracle venv)

All claims in this section are **verified** against the oracle venv.

### What the platform owns on top

The Python runtime in `langgraph-api==0.12.6` does **not** manage Postgres DDL
directly. Persistence is delegated to a Go core process over gRPC:

* `langgraph_api/config/__init__.py:42-49` — comment *"Optional: persistence can go
  through the Go core over gRPC, so the Python runtime can start without a direct
  DB connection string"* — `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:42-49`.
* `langgraph_api/store.py:37-39` — `from langgraph_runtime.store import Store`
  returning the Go-backed `Store()` when no custom path is configured —
  `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/store.py:37-39`
  (complemented by the note at `langgraph_api/store.py:76-80`: *"IN STEAD OF the
  default postgres + pgvector store"*).
* `langgraph_api/_checkpointer/__init__.py:10-18` — delegates to
  `langgraph_api._checkpointer._adapter` / `langgraph_runtime_inmem.checkpoint`
  in the inmem profile — `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/_checkpointer/__init__.py:13-18`.

For **inmem** (`langgraph dev` without `--postgres`), the logical tables are
simulated in-process:

* `langgraph_runtime_inmem/database.py:82-97` — `GlobalStore(PersistentDict)` with
  keys `assistants`, `assistant_versions`, `threads`, `runs`, `crons` —
  `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/database.py:82-97`.
* The same store is populated at startup in `database.py:186-197` —
  `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/database.py:186-197`.

```
GlobalStore keys  →  platform concepts
  assistants         assistant (current version pointer)
  assistant_versions assistant_versions (version history, append-only)
  threads            threads (metadata, status, config, values)
  runs               runs (per-thread executions, statuses pending/running/success/error/interrupted)
  crons              crons (scheduled runs: schedule, payload, next run)
```

Type definitions (all `TypedDict` with `UUID` primary keys):

* `Assistant` / `AssistantVersion` / `Thread` / `Run` / `RunEvent` —
  `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/database.py:28-80`.

Operations are implemented in `langgraph_runtime_inmem/ops.py` as plain dict
manipulations (e.g. `Assistants.search` at `ops.py:159-237`,
`Threads.search` at `ops.py:818-926`) — see
`tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/ops.py:155-237`.

The **Postgres** DDL for these five concepts lives in the Go core, not the
Python package. Its migration files are mounted at:

* `MIGRATIONS_PATH=/storage/migrations` — `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:56`.

A Go-core–backed deployment applies those `*.sql` migrations at process start;
the Python runtime never issues `CREATE TABLE` for `assistants`/`threads`/`runs`.

> **`UNVERIFIED` fallback for readers expecting column-level DDL:**
> The column list for the real Postgres deployment (as distinct from the
> inmem `TypedDict`) is not visible in the Python venv. The public
> `langgraph-api` repository's Go sources are the source of truth:
> `https://github.com/langchain-ai/langgraph/tree/main/libs/langgraph-api` (Go
> core `storage_postgres/` migrations). Any column-level claim about the
> Postgres `threads`/`runs` tables that is not witnessed in the inmem ops
> should be treated as `UNVERIFIED` and re-checked against those Go files.

### How it consumes DATABASE_URI

```python
DATABASE_URI: str | None = env(
    "DATABASE_URI", cast=str, default=getenv("POSTGRES_URI", None)
)
```

`tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:49-51`

* Reads `DATABASE_URI` first, `POSTGRES_URI` as fallback (for `langgraph dev`
  ergonomics — `langgraph_api/graph.py:827` mentions *"please set the
  `POSTGRES_URI` environment variable"*).
* `DATABASE_URI=None` is valid — the runtime skips creating a psycopg pool and
  operates in gRPC-only mode; callers needing a direct connection will fail with
  *"Postgres pool not initialized"* (per the comment at `config/__init__.py:42-48`).
* In production the value is injected via `deploy/fly.weaviate-prod.toml` /
  `deploy/fly.prod.toml` secrets; locally it is read from `.env` (loaded in
  `healthcare_rag/__main__.py` before any model client imports).

### Pool sizing knob

```python
POSTGRES_POOL_MAX_SIZE = env("LANGGRAPH_POSTGRES_POOL_MAX_SIZE", cast=int, default=150)
CHECKPOINTER_POSTGRES_POOL_MIN_SIZE = env(
    "LANGGRAPH_CHECKPOINTER_POSTGRES_POOL_MIN_SIZE", cast=int, default=1,
)
CHECKPOINTER_POSTGRES_POOL_TIMEOUT_SECONDS = env(
    "LANGGRAPH_CHECKPOINTER_POSTGRES_POOL_TIMEOUT_SECONDS", cast=float, default=15.0,
)
```

`tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:57-67`

* **`LANGGRAPH_POSTGRES_POOL_MAX_SIZE` default 150 per replica — confirmed in the
  oracle venv** at `config/__init__.py:57`. This is the Go core's pgx pool size,
  not the OSS `PoolConfig.max_size`. Scale with replica count:
  `total_connections = replicas * 150` — size the database `max_connections`
  accordingly.
* The checkpointer sub-pool has its own knobs (`MIN_SIZE=1`, `TIMEOUT=15s`);
  these are wired through `database._startup_needs` / `database.start_pool`
  (Go core), not through the Python `ConnectionPool` constructor.

Public docs alternative for pool sizing (consistent with the value):
`https://docs.langchain.com/langgraph/platform/deployment` and
`https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph-api/README.md`
refer to `LANGGRAPH_POSTGRES_POOL_MAX_SIZE` without stating a default; the
oracle venv file is the source for the `150` value.

### Other knobs present in the venv (for context)

| Env var | Default | Location |
|---------|---------|----------|
| `CHECKPOINT_MAX_BATCH_SIZE` | `None` (Go default 1000, `core/config/config.go`) | `config/__init__.py:74-76` |
| `CHECKPOINT_BATCH_DELAY` | `0.0` (Go default 0.005) | `config/__init__.py:77` |
| `MIGRATIONS_PATH` | `/storage/migrations` | `config/__init__.py:56` |
| `LANGGRAPH_POSTGRES_EXTENSIONS` | `standard` | `config/__init__.py:441-446` |

---

## What we adopt / what we deliberately differ on

This section is the contract for **todo 10** (registries interface) and **todo 13**
(run redaction). It is **not** a live implementation — it records the design
choices this doc's characterization implies.

### Adopt

* **`DATABASE_URI` name and `POSTGRES_URI` fallback** — matches the platform
  (`config/__init__.py:49-51`). `server/storage.py` / `server/config.py` will
  read the same two names in the same precedence.
* **`LANGGRAPH_POSTGRES_POOL_MAX_SIZE=150` semantics** — per-replica default;
  our `server/storage.py` will expose this knob with the same name and default.
* **Psycopg connection contract** (`autocommit=True`, `prepare_threshold=0`,
  `dict_row`) — adopted from the OSS saver/store (`__init__.py:76-78`) for any
  direct `psycopg` usage; required for stateless `setup()` and for prepared-
  statement safety under a pool.
* **Migration versioning pattern** — `MIGRATIONS` list with `v` primary-key table
  and `SELECT v FROM <migrations> ORDER BY v DESC LIMIT 1` / `INSERT INTO
  <migrations> (v) VALUES (%s)` — reused for our own registries migrations,
  but in separate `hc_*_migrations` tables.

### Deliberately differ

* **Whole-record JSONB persistence for our own registries** (assistant/threads/
  runs/crons registry tables owned by `server/`). The platform maps fields into
  many Postgres columns (witnessed indirectly via the inmem `TypedDict` typed
  fields and the `select` parameter in `threads.py:129`). We store the entire
  registry record as a single `JSONB` column plus a `UUID` primary key and a
  small number of indexed columns (e.g. `created_at`). Rationale: fewer
  migrations when the wire shape evolves, and the record is always read as a
  whole by `server/storage.py`.

* **`hc_` table-name prefix** to avoid any collision if the real `langgraph-api`
  were ever pointed at the same database. Our DDL will create `hc_threads`,
  `hc_runs`, `hc_crons`, `hc_store`, `hc_store_vectors`, `hc_checkpoint_migrations`,
  etc. The platform's tables (`threads`, `runs`, `crons`, `assistants`,
  `checkpoints`, `store`, ...) remain disjoint by name even on a shared cluster.
  This also means our `MIGRATIONS_PATH` / Go migrations never interfere.

* **No `assistant` / `assistant_versions` table** — `server/assistants.py`
  synthesizes assistants from `graphs.py`'s loaded `langgraph.json` graph IDs.
  This is existing behavior (see `server/AGENTS.md:32` and
  `server/manifest.py:27-33`: assistants routes are not backed by a writable
  table). If the platform then creates an `assistant_id` that is really a
  `graph_id`, we accept either form (matching `graph.py:144`:
  *"Validate an assistant ID is either a graph_id or a valid UUID"*).

* **In-memory parity target semantics are unchanged** — until `SERVER_STORAGE=postgres`
  is set, the entire `server/storage.py` seam remains `InMemorySaver` /
  `InMemoryStore` (`server/AGENTS.md:50`). The Postgres path is additive and
  behind a feature flag (`config.STORE_CONFIG`, `config.STORAGE_KIND`), not a
  replacement of the default.

* **Run redaction (todo 13) is registry-level, not column-level** — rather than
  scrubbing individual `checkpoint`/`store.value` JSONB keys, the `runs` registry
  will redact on read (and optionally store a redacted projection). The
  checkpoint/store raw rows are never mutated for redaction; this keeps the
  checkpointer/store semantics identical to the OSS contracts above.

---

## Verification

```bash
# Clean-room invariant — only the shim may reference langgraph_api
rg -n "from langgraph_api|import langgraph_api" server/
# expected: server/_compat.py only

# Oracle version pin
tests/server/oracle/.venv/bin/python -c "import langgraph_api; print(langgraph_api.__version__)"
# → 0.12.6

# Postgres packages are NOT in the oracle venv's Python surface (Go core holds DDL)
grep -rn psycopg tests/server/oracle/.venv --include="*.py" | grep -c langgraph_api
# → 0  (no psycopg references in the platform Python package, by design)
```

---

## Citations index

| Fact | Citation |
|------|----------|
| `DATABASE_URI` reads `DATABASE_URI` then `POSTGRES_URI` | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:49-51` |
| `POSTGRES_POOL_MAX_SIZE` default `150` per replica | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:57` |
| `CHECKPOINTER_POSTGRES_POOL_MIN_SIZE=1` / `TIMEOUT=15.0` | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:58-67` |
| `MIGRATIONS_PATH=/storage/migrations` | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:56` |
| Platform persistence may go through Go core over gRPC; `DATABASE_URI` may be `None` | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/config/__init__.py:42-49` |
| Default store is *"default postgres + pgvector store"* unless `store.path` is set | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_api/store.py:76-80` |
| Inmem `GlobalStore` keys `assistants`/`assistant_versions`/`threads`/`runs`/`crons` | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/database.py:82-97` |
| `Assistant`/`AssistantVersion`/`Thread`/`Run` TypedDicts | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/database.py:28-80` |
| Assistants ops are dict scans (`Assistants.search` etc.) | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/ops.py:155-237` |
| Threads ops are dict scans (`Threads.search` etc.) | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/ops.py:818-926` |
| Checkpointer delegate via `langgraph_runtime_inmem.checkpoint` | `tests/server/oracle/.venv/lib/python3.12/site-packages/langgraph_runtime_inmem/checkpoint.py:52-104` |
| `langgraph_api==0.12.6` pinned with `langgraph-cli[inmem]==0.4.31` | `tests/server/oracle/README.md:7-15` and `tests/server/oracle/requirements.txt:6-7` |
| `checkpoint_migrations` / `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` DDL — `UNVERIFIED` | `UNVERIFIED` • [`langgraph-checkpoint-postgres==3.1.2` `base.py:44-77`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py#L44-L77) |
| `MIGRATIONS` list + `CONCURRENTLY` indexes + `task_path` column — `UNVERIFIED` | `UNVERIFIED` • [`base.py:43-91`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py#L43-L91) |
| Checkpoint `setup()` reads `checkpoint_migrations` and applies `MIGRATIONS[version+1:]` — `UNVERIFIED` | `UNVERIFIED` • [`__init__.py:92-108`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py#L92-L108) |
| Connection contract `autocommit=True, prepare_threshold=0, dict_row` — `UNVERIFIED` | `UNVERIFIED` • [`__init__.py:76-78`](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py#L76-L78) / [`store/postgres/base.py:812-819`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L812-L819) |
| `store` / `store_vectors` DDL + `store_migrations`/`vector_migrations` — `UNVERIFIED` | `UNVERIFIED` • [`store/postgres/base.py:63-115`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L63-L115) |
| `MIGRATIONS` / `VECTOR_MIGRATIONS` for store — `UNVERIFIED` | `UNVERIFIED` • [`store/postgres/base.py:63-145`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L63-L145) |
| `PoolConfig` (`min_size`, `max_size`, `kwargs`) — `UNVERIFIED` | `UNVERIFIED` • [`store/postgres/base.py:151-176`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L151-L176) |
| pgvector extension + ANN index (`hnsw`/`ivfflat`) — `UNVERIFIED` | `UNVERIFIED` • [`store/postgres/base.py:92-145,1205-1275`](https://github.com/langchain-ai/langgraph/blob/main/libs/store-postgres/langgraph/store/postgres/base.py#L92-L145) |

---

## What remains `UNVERIFIED` (needs Go-core source or live DB)

* Exact Postgres DDL for the **platform** `assistants`/`assistant_versions`/`threads`/`runs`/`crons` tables as created by the Go core. The Python inmem `TypedDict`s (`database.py:28-80`) are the only Python-visible shape; the Postgres column list lives under `MIGRATIONS_PATH=/storage/migrations` in the Go image. Treat any column-level claim about those tables that is not traced to `ops.py` or `schema.py` as `UNVERIFIED` and re-derive from the Go `storage_postgres/` migrations before writing production DDL.
* Whether the OSS `langgraph-checkpoint-postgres==3.1.2` DDL/migration list is identical to whatever version is actually installed in this repo's dev venv (todo 6-8 will check). Do not assume this doc's OSS version matches the installed version.
