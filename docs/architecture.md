# Architecture

ContextRealm is a composition of off-the-shelf open-source services bound together by a thin custom pipeline layer. Every component is replaceable.

## System Diagram

```
You ──▶ Open WebUI ──▶ Pipeline (mem0_filter) ──▶ LiteLLM ──▶ Claude / GPT / Gemini / Grok
          │   ▲               │
  upload  │   │ RAG context   │ REST
          ▼   │               ▼
        [RAG]           ContextRealm Mem0 Server
    (Chroma + Ollama)         │
                     ┌────────┼────────┐
                     ▼        ▼        ▼
                 Postgres   Neo4j   Ollama
                (vectors)  (graph)  (embed)
```

## Components

### Open WebUI

**Role:** Chat interface, document RAG, pipeline host.

Open WebUI provides the user-facing chat interface and the **Pipelines** plugin system — a hook architecture that intercepts messages before they reach the model (`inlet`) and after the model responds (`outlet`). ContextRealm's core logic lives entirely within a Pipeline plugin, keeping the UI layer unmodified and upgradeable independently.

Built-in RAG capabilities (Chroma + Ollama embedder) handle document uploads separately from Mem0. This gives two distinct retrieval paths:

| Path     | Engine           | Scope                     | Best for                              |
| -------- | ---------------- | ------------------------- | ------------------------------------- |
| **RAG**  | Chroma           | Per-conversation          | Ad-hoc document reference             |
| **Mem0** | Postgres + Neo4j | Persistent, cross-session | Facts, preferences, project knowledge |

### LiteLLM

**Role:** Model router — unified OpenAI-compatible API across all frontier providers.

LiteLLM proxies requests from Open WebUI to Claude (Anthropic), GPT-4o (OpenAI), Gemini (Google), Grok (xAI), and any Ollama-hosted local model. Configuration lives in `config/litellm_config.yaml`. Switching or adding models requires only a config change — no pipeline code changes.

LiteLLM also enforces spend limits, provides an admin UI at `:4000/ui`, and logs all requests.

### Pipeline (`mem0_filter`)

**Role:** Memory injection (inlet) and memory extraction (outlet).

This is the meaningful custom code in ContextRealm. On every message exchange:

1. **`inlet`:** Query Mem0 for the top-N memories relevant to the user's latest message. Prepend those memories to the system prompt before the request reaches the model.
2. **`outlet`:** After the model responds, store the user+assistant turn in Mem0 for future retrieval.

Failures in both directions are caught silently — the chat is never blocked by a memory error.

→ Source: `pipeline/mem0_filter.py`

### ContextRealm Mem0 Server

**Role:** Memory engine — stores, deduplicates, updates, and retrieves memories.

Mem0's open-source server is built **from upstream source inside the Docker image** — this repository contains no fork, no copy, and no vendored code from the Mem0 repository. The `Dockerfile` clones a pinned upstream tag at build time and applies two targeted patches:

1. Add `ollama` to pip requirements
2. Register `ollama` as a bundled embedder provider

This approach means:

- Upstream updates are deliberate (change `MEM0_VERSION` in `.env`)
- The Mem0 codebase stays at arm's length — no merge conflicts, no upstream drift
- The patches are minimal and reviewable in one place

→ Source: `docker/mem0-server/Dockerfile`

#### Memory storage backends

| Backend                 | Role                                                           |
| ----------------------- | -------------------------------------------------------------- |
| **Postgres + pgvector** | Stores memory vectors; cosine similarity search for retrieval  |
| **Neo4j**               | Knowledge graph: entities, relationships, and temporal context |
| **Ollama**              | Embedding model (`qwen3-embedding:0.6b` by default)            |

### Postgres + pgvector

**Role:** Primary vector store for Mem0.

Standard PostgreSQL with the `pgvector` extension enabled via `CREATE EXTENSION vector`. The `ankane/pgvector` Docker image includes pgvector precompiled.

In production, use a managed PostgreSQL service with pgvector enabled. This eliminates backup management and provides point-in-time recovery. Each Realm gets its own database on the shared cluster.

### Neo4j

**Role:** Knowledge graph — captures _who knows what about whom_.

While pgvector retrieves memories by semantic similarity, Neo4j stores the structured relationships between entities extracted from conversations (people, projects, preferences, facts). This enables queries that span multiple disconnected memory chunks.

**Memory tuning for 4 GB VPS:** Neo4j's default configuration targets large servers and will OOM a small VPS. The following settings cap Neo4j at approximately 1 GB total, leaving headroom for the other services:

```
NEO4J_server_memory_heap_initial__size=256m
NEO4J_server_memory_heap_max__size=512m
NEO4J_server_memory_pagecache__size=256m
NEO4J_server_memory_off__heap_transaction_max__size=128m
```

These are set via `.env` and picked up by `docker-compose.yml`. Increase the values proportionally if more RAM is available.

### Ollama

**Role:** Local embedding model; optional local LLM.

Runs `qwen3-embedding:0.6b` for all embeddings (Mem0 + Open WebUI RAG). This keeps embedding costs at zero and keeps all data local. The model is under 500 MB and fast on CPU.

Optionally run a local LLM (e.g., `qwen2.5:7b`) exposed through LiteLLM as a fallback or offline model.

In production (K8s), Ollama runs on a separate CPU VM rather than inside the cluster, shared across all Realms.

### OpenMemory MCP _(optional)_

**Role:** Expose ContextRealm memories as an MCP server.

A thin wrapper around the Mem0 REST API that implements the [Model Context Protocol](https://modelcontextprotocol.io/). Once running, Claude Desktop and Claude Code can query and update memories without going through Open WebUI.

→ Configuration: [docs/setup.md#mcp-configuration](setup.md#mcp-configuration)

---

## Data Flow

### Per-message flow (with memory)

```
1. User sends message in Open WebUI
2. Pipeline.inlet() intercepts:
   a. POST /v1/memories/search → Mem0 (top-N relevant memories, timeout 3s)
   b. Prepend memories as system prompt context
3. Augmented request forwarded to LiteLLM
4. LiteLLM routes to selected frontier model
5. Model response streamed back to Open WebUI
6. Pipeline.outlet() intercepts:
   a. POST /v1/memories → Mem0 (store user+assistant turn, timeout 5s)
7. Response displayed; memory stored asynchronously
```

If Mem0 is unreachable at step 2a or 6a, the pipeline catches the exception and continues — the user sees no interruption.

### Memory storage flow (Mem0 internals)

```
POST /v1/memories { messages, user_id }
  → LLM extracts discrete facts from messages
  → Embed each fact with Ollama
  → Store vectors in Postgres (pgvector)
  → Extract entities + relationships → Neo4j
  → Deduplicate: merge with existing memories where appropriate
```

### Document ingestion — Open WebUI RAG

```
User uploads document in Open WebUI
  → Chunked and embedded (Ollama)
  → Stored in Chroma (local vector DB bundled with Open WebUI)
  → Retrieved per-conversation via RAG (not persistent across sessions)
```

### Bulk knowledge import

```
python scripts/import_context.py --file doc.md --user default --tag projects
  → Read file (supports Markdown with YAML frontmatter)
  → Chunk text (500 tokens, 50-token overlap)
  → POST each chunk to Mem0 /v1/memories
  → Tagged with source file and custom tag for later filtering
```

---

## Network Topology

All services communicate on an internal Docker bridge network (`contextrealm`). The host machine exposes only two ports by default:

| Port   | Service        | Notes                                                     |
| ------ | -------------- | --------------------------------------------------------- |
| `3000` | Open WebUI     | Primary UI — put behind HTTPS reverse proxy in production |
| `8765` | OpenMemory MCP | MCP endpoint — expose only for local Claude Desktop use   |

All backend ports are internal only:

| Internal port | Service              |
| ------------- | -------------------- |
| `4000`        | LiteLLM              |
| `9099`        | Pipelines            |
| `8000`        | Mem0 Server          |
| `5432`        | Postgres             |
| `7687`        | Neo4j (Bolt)         |
| `7474`        | Neo4j (HTTP browser) |
| `11434`       | Ollama               |

---

## Multiple Realm Model

Each Realm is an independent deployment with its own isolated:

- Memory store (dedicated Postgres database, e.g., `mem0_personal`, `mem0_world`)
- Knowledge graph (dedicated Neo4j database)
- Document knowledge base (dedicated Chroma collection)
- Subdomain (`{realm}.contextrealm.yourdomain.com`)
- Open WebUI instance (separate user accounts, separate RAG)

Cost optimisation — Realms **share**:

- Ollama instance (one CPU VM, shared embedding)
- Postgres server (different databases on the same cluster)
- K8s cluster and ingress controller

This means a second Realm costs roughly the incremental memory and storage for Neo4j + Postgres data, not a full duplicate stack.

---

## Technology Choices

| Decision       | Choice                             | Rationale                                                                          |
| -------------- | ---------------------------------- | ---------------------------------------------------------------------------------- |
| Memory engine  | Mem0 (open-source server)          | Graph + vector hybrid; deduplication built-in; active development; REST API        |
| Model router   | LiteLLM                            | Unified OpenAI-compatible API; provider failover; spend controls; audit log        |
| Chat UI        | Open WebUI                         | Production-grade; RAG built-in; Pipelines plugin system; active community          |
| Vector store   | Postgres + pgvector                | Eliminates a separate Qdrant/Weaviate service; managed DB option in prod           |
| Graph store    | Neo4j                              | Best-in-class for relationship queries; official Docker image; Mem0 native support |
| Embedder       | Ollama + `qwen3-embedding:0.6b`    | Free, local, fast; no external embedding API; < 500 MB                             |
| Infrastructure | Docker Compose (dev) + Helm (prod) | Proven patterns; easily auditable; no vendor lock-in                               |
| MCP            | OpenMemory MCP                     | Turns Mem0 into an MCP server with zero custom code                                |
