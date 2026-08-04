# Concepts

How Sill is shaped, and why. Read this before you start customizing.

---

## What's in the database

The squashed schema at `backend/schema.sql` creates 43 tables on a
Postgres 16 instance with `pgvector` and Apache AGE installed. They
fall into seven groups.

### Memories (the core)

- `memories` — the parent table. Every memory has an `id`, `type`,
  `content`, `embedding` (vector), `importance`, `created_at`,
  `last_reused`, `reuse_count`, and `archived_at`.
- `episodic_memories`, `semantic_memories`, `procedural_memories`,
  `strategic_memories` — type-specific extensions, joined by id.
- `working_memory` — short-lived staging for in-progress thought.
- `ingestion_receipts` — provenance for batch imports (seed pack,
  external ingest jobs).

The four type tables exist because different memory shapes carry
different metadata (a procedural memory has a `recipe`, a strategic
memory has a `decision_context`, etc.). For most read paths you query
`memories` directly and join only when you need the type-specific
fields.

### Concepts and edges

- `concepts` — a vocabulary of named concepts (`"verification"`,
  `"attribution"`, …) plus their embeddings.
- `memory_concepts` — many-to-many between memories and concepts.

This is the routing layer that lets a query for "what do we have
about attribution?" find memories that don't textually contain the
word "attribution".

### Identity / worldview

- `worldview_primitives` — the agent's load-bearing positions
  ("memory is a routing layer, not a knowledge layer").
- `identity_aspects` — durable shape-of-self entries.
- `worldview_memory_influences`, `identity_memory_resonance` —
  link tables tying memories to the worldview/identity rows they
  reinforce or contradict.

Most installs won't touch these tables directly. They exist because
the upstream agi-memory project uses Sill as a research substrate;
you can ignore them for normal recall.

### Goals and drives

- `goals` — work items with `priority` (`active` / `queued`
  / `completed` / `abandoned`), `title`, `description`, timestamps.
- `drives` — longer-arc orientations with a `current_focus` field.
  `precompact-snapshot` and `goodnight-checkpoint` read/write this.
- `goal_memory_links` — ties memories to the goal they came out of.

### Maintenance config and state

- `maintenance_config`, `config` — knobs.
- `maintenance_state` — worker state machine.
- `external_calls`, `outbox_messages` — for the RabbitMQ message bus.

The `maintenance_worker` runs by default and handles importance decay,
drift tracking, and other housekeeping. Reflective processing ships as the beat 
worker (see `docs/beats.md`), which is off until you turn it on.

### Embedding cache

- `embedding_cache` — content-hash → vector, so re-importing the same
  text doesn't re-call the embeddings service.
- `embedding_config` — records the locked model + dimension at
  install time.

### Clusters, episodes, and the rest

- `memory_clusters`, `memory_cluster_members`, `cluster_relationships`,
  `memory_neighborhoods` — graph-of-memories structures used by synthesis.
- `episodes`, `episode_memories` — temporal groupings.
- `memory_changes`, `importance_updates`, `boundaries`,
  `emotional_states`, `relationship_discoveries`,
  `health_snapshots`, `synthesis_attempts`,
  `synthesis_attempt_concepts`, `quality_pattern_suggestions`,
  `exploration_requests`, `review_queue` — bookkeeping for the
  longer-loop research features. Safe to ignore for a basic recall
  install.

If you only ever call `recall` and `remember`, you'll touch maybe
four of these tables. The rest are there for the maintenance and
research workflows to grow into.

---

## Memory shape: the two-shape rule

Sill memories should fit one of two shapes:

### Source Card

A verbatim quote with provenance, plus why it mattered.

```
"Memory retrieval is in-context programming." — William, 2026-02-01,
sill discussion. Frames recall as pathway-steering, not just lookup.
```

Fields that make this load-bearing: the **verbatim quote**, the
**source pointer** (who said it / where / when), and a sentence
about **what discriminator it supports** ("if recalled memories
didn't change response shape, this frame would be wrong").

### Decision / Bridge

A mapping the agent has adopted, plus what would revise it.

```
Decision (2026-02-15): when recall returns >3 hits, default to
recall_preview + selective recall_batch. Revise if preview snippets
prove too short to discriminate on >30% of queries.
```

Fields: the **decision**, the **revision condition**, and (often)
a **bridge to the framework it came from**.

### The quality gate

Seed memory #7 of `seed/methodology.jsonl` encodes the rule:

> Before storing a memory, check the draft has at least one of: a
> verbatim quote with source, a falsifier ("this would be wrong
> if…"), or a concrete example. Stable preferences/decisions are OK
> without these but should name what they're a preference *over*.
> If the draft is just a restated observation with none of the
> above, ask before storing.

This is the single most important rule in the methodology pack. It
prevents the corpus from filling up with restated trivia.

---

## Embeddings (locked at install)

The Docker stack runs HuggingFace TEI with
`unsloth/embeddinggemma-300m` by default. That produces **768-dim**
vectors.

The Postgres schema is parameterized on `app.embedding_dimension` (a
GUC set from `EMBEDDING_DIMENSION` in `.env`), but the vector column
types in the schema are bound at table-creation time. Changing the
model after install means:

1. `docker compose -f backend/docker-compose.yml down -v` (drops the
   `postgres_data` volume — **data loss**).
2. Change `EMBEDDING_MODEL_ID` and `EMBEDDING_DIMENSION` in `.env`.
3. Bring the stack back up (re-creates the schema with new dims).
4. Re-seed and re-import any external memories.

In practice: pick your embedding model before first install and
don't change it. If you do change it, `./reset.sh` is the safer path
than hand-editing.

The default model is small (~300MB), CPU-friendly, and good enough for
the kinds of recall sill is doing. If you want to swap in a heavier
model later, do it on a fresh install in a separate `SILL_DB_CONTAINER`
and migrate by exporting/re-importing memories.

---

## Recall

The recommended pattern is **progressive recall**.

### Layer 1: preview

```python
# MCP tool: mcp__sill__recall_preview
{"query": "what do we know about attribution?", "limit": 10}
# Returns: [{id, type, importance, snippet}, …]  ~100 chars each.
```

This is cheap. You get the shape of the result set without spending
tokens on full content.

### Layer 2: batch fetch

```python
# MCP tool: mcp__sill__recall_batch
{"ids": ["uuid-1", "uuid-3"]}
# Returns full memory rows for the IDs you actually want.
```

Selectively pulling 2 of 10 saves roughly 75% of the tokens versus
calling full `recall`. Worst case (you end up wanting all 10) is
about 6% overhead.

When to skip preview: enumerative queries ("all memories about Z"),
or queries where you specifically want the full content and don't
want a second round trip. The seed pack memory #13 documents this:

> Default to recall_preview + selective recall_batch for open-ended
> queries. Switch to full recall when the query is enumerative or
> when preview snippets are too short to discriminate.

### The methodology pack

`sill seed import seed/methodology.jsonl` loads 22 memories that
encode the inquiry / verification / recall / storage discipline this
project has converged on. They're tagged so spontaneous-recall surfaces
them in relevant prompts. You don't have to use them as written — but
they're a tested starting point.

---

## Workers

### Maintenance (default-on)

Started by `docker compose up -d … maintenance_worker`. Runs
`sill-worker --mode maintenance`. Currently does importance decay
and drift tracking; the loop is conservative and idempotent.

Logs: `docker compose logs maintenance_worker`.

### Beat worker

Reflective processing ships as the beat worker (see `docs/beats.md`), which is off
until you turn it on.

---

## Apache AGE graph layer

Sill ships with Apache AGE installed and a `memory_graph` graph
created. Vertex labels: `MemoryNode`, `ConceptNode`, `SelfNode`,
`LifeChapterNode`, `TurningPointNode`, `NarrativeThreadNode`,
`RelationshipNode`, `ValueConflictNode`.

The MCP server exposes some graph-shaped queries (`find_causes`,
`follow_supersedes`, `relate_concepts`), but most recall paths use
the relational tables (`memories`, `memory_concepts`, `concepts`).

If you don't need graph queries, you can ignore AGE entirely. It's
installed because the schema requires the extension; it costs almost
nothing when unused.

If you do want to extend along the graph, the conventions are:

- `MemoryNode` and `ConceptNode` are kept in sync with the `memories`
  and `concepts` tables.
- Edges live in AGE only — there's no relational mirror of
  `(MemoryNode)-[:SUPERSEDES]->(MemoryNode)`. Query them with
  Cypher via the `cypher()` SQL function.

See `backend/schema.sql` for the graph-creation block and edge-type
enum.

---

## Where to look next

- `docs/hooks.md` — what each shipped hook does and how to disable
  any you don't want.
- `docs/extending.md` — writing good memories, the quality gate,
  adding your own hooks and rule files, the env-var cheat sheet.
- `backend/sill_mcp_server.py` — the full MCP tool surface (a few
  dozen tools, all under `mcp__sill__*`).
- `seed/methodology.jsonl` — the 22 seed memories, readable as
  plain JSONL.
