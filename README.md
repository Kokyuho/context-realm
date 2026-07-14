# ContextRealm

> A self-hosted, private AI knowledge engine. Each deployed instance is a **Realm** — a contained knowledge universe for a person, project, or fictional world.

Talk to any frontier AI model while it **remembers everything and knows your world**.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Status](https://img.shields.io/badge/status-alpha-orange)](https://github.com/yourusername/context-realm/releases)

---

## Overview

Every major AI product — ChatGPT, Claude, Gemini — is building memory and knowledge features designed to keep you inside their ecosystem. Your context, your history, your world: stored on their servers, queryable only through their interface, gone if you switch providers.

ContextRealm is the self-hosted alternative. It is a **web UI** backed by a **persistent knowledge store** that you own and control. You bring the models — Claude today, GPT-4o tomorrow, a local Qwen next week — and your memory travels with you. The knowledge base grows richer with every conversation regardless of which model you use, and nothing leaves your infrastructure.

In practice: open the UI, pick any frontier model, and talk. ContextRealm automatically retrieves relevant memories before each message and stores new ones after. Your projects, preferences, lore, and notes are always in context — without pasting them in every time.

The same knowledge store is also exposed as an **MCP server**, so any agent or IDE that speaks MCP — Claude Desktop, Claude Code, Cursor, or your own scripts — can read from and write to your Realm directly. One knowledge base, every interface.

**Core properties:**

- **Yours** — data stays on your infrastructure; zero telemetry, no vendor lock-in
- **Persistent** — memory survives across sessions, model changes, and provider switches
- **Structured** — graph memory captures relationships between facts, not just flat text chunks
- **Dual retrieval** — Mem0 for efficient persistent memory; RAG (Chroma) for ad-hoc document reference
- **Agent-accessible** — exposed as an MCP server so any MCP-compatible agent or tool can query your Realm
- **Composable** — swap any component: models, embedders, vector stores
- **Multi-realm** — isolated instances per person, project, or knowledge domain

## Architecture

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

| Component                    | Role                                                                 |
| ---------------------------- | -------------------------------------------------------------------- |
| **Open WebUI**               | Chat interface + document RAG                                        |
| **Pipeline** (`mem0_filter`) | Memory injection/extraction — this repo's core logic                 |
| **LiteLLM**                  | Model router — unified API across all frontier providers             |
| **Mem0 Server**              | Memory engine: stores, deduplicates, and retrieves memories          |
| **Postgres + pgvector**      | Vector similarity search for memory retrieval                        |
| **Neo4j**                    | Knowledge graph — captures entity relationships across conversations |
| **Ollama**                   | Local embedding model + optional local LLM                           |

→ Full details in [docs/architecture.md](docs/architecture.md)

## Quick Start

### Prerequisites

- Docker Engine 24+ and Docker Compose v2
- 8 GB RAM recommended (4 GB minimum — see [memory tuning](docs/setup.md#memory-tuning))
- API key for at least one frontier model (Anthropic, OpenAI, Google, or xAI)

### Setup

```bash
git clone https://github.com/yourusername/context-realm.git
cd context-realm

# Configure environment
cp .env.example .env
$EDITOR .env   # set passwords and add at least one AI provider API key

# Build custom images and start all services
docker compose build
docker compose up -d

# Pull embedding model and initialise the database
bash scripts/setup.sh

# Open the UI
open http://localhost:3000

# Run the test suite (unit tests by default; --integration for live Mem0 checks)
bash scripts/test.sh
```

→ Full walkthrough in [docs/setup.md](docs/setup.md)

## Multiple Realm Deployment

Deploy isolated instances per knowledge domain using the Helm chart:

```bash
# Personal realm
helm install realm-personal ./helm --set realm.name=personal

# Worldbuilding realm
helm install realm-world ./helm --set realm.name=world

# Project realm
helm install realm-project ./helm --set realm.name=project
```

Each realm gets its own subdomain (`personal.contextrealm.yourdomain.com`), isolated memory store, and document knowledge base. A shared Ollama instance and Postgres cluster (one database per realm) minimise infrastructure cost.

→ See [docs/setup.md#production-deployment](docs/setup.md#production-deployment)

## MCP — any MCP-compatible client

Expose your memory to Claude Desktop, Claude Code, Cursor, or any other MCP-compatible client via the in-tree MCP server (`mcp_server/`). TLS is handled by a Caddy sidecar in `docker-compose.yml`: set `REALM_DOMAIN=mcp.yourdomain.com` in `.env` and Let's Encrypt certs are issued automatically. Add to your MCP client config (replace `<REALM_DOMAIN>` and `<MEM0_ADMIN_API_KEY>`):

```json
{
  "mcpServers": {
    "contextrealm": {
      "url": "https://<REALM_DOMAIN>/mcp",
      "headers": {
        "Authorization": "Token <MEM0_ADMIN_API_KEY>"
      }
    }
  }
}
```

The MCP service exposes two tools: `search_memories(query)` and `add_memory(text)`. The admin token is the only access control — share it with collaborators you trust with the Realm's memories.

→ See [docs/setup.md#mcp-configuration](docs/setup.md#mcp-configuration)

## Importing Knowledge

Bulk-import documents, notes, and lore files into persistent memory:

```bash
python scripts/import_context.py --file my_projects.md --user default --tag projects
python scripts/import_context.py --dir ./notes/ --user default
```

## Project Structure

```
context-realm/
├── docker/
│   └── mem0-server/        # Mem0 server image — patched at build time from upstream
├── pipeline/
│   ├── mem0_filter.py      # Open WebUI ↔ Mem0 glue — the core custom logic
│   ├── requirements.txt
│   └── Dockerfile
├── config/
│   └── litellm_config.yaml # Model router configuration
├── scripts/
│   ├── setup.sh            # Pull Ollama models, enable pgvector
│   ├── test.sh             # Test runner wrapper around pytest
│   └── import_context.py   # Bulk knowledge import tool
├── tests/                  # Test suite (unit + integration)
├── helm/                   # Kubernetes Helm chart for multi-realm deployment
├── docs/
│   ├── architecture.md
│   ├── setup.md
│   └── contributing.md
├── docker-compose.yml      # Local development
├── docker-compose.prod.yml # Production overrides
├── pyproject.toml          # Test + lint configuration
├── requirements-dev.txt    # Dev-only Python deps (pytest, ruff, httpx)
└── .env.example
```

## Documentation

| Document                                     | Description                                       |
| -------------------------------------------- | ------------------------------------------------- |
| [docs/architecture.md](docs/architecture.md) | Component overview, data flow, technology choices |
| [docs/setup.md](docs/setup.md)               | Local and production deployment guide             |
| [docs/contributing.md](docs/contributing.md) | Development workflow and PR process               |

## Contributing

Contributions are welcome. Please read [docs/contributing.md](docs/contributing.md) before opening a pull request.

## License

Apache 2.0 — see [LICENSE](LICENSE).
