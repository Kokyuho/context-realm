# Setup Guide

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Local Development](#local-development)
3. [Configuration Reference](#configuration-reference)
4. [Memory Tuning](#memory-tuning)
5. [MCP Configuration](#mcp-configuration)
6. [Production Deployment](#production-deployment)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Requirement    | Minimum version | Notes                                                 |
| -------------- | --------------- | ----------------------------------------------------- |
| Docker Engine  | 24.0+           |                                                       |
| Docker Compose | v2.20+          | Bundled with Docker Desktop                           |
| RAM            | 4 GB            | 8 GB recommended; see [Memory Tuning](#memory-tuning) |
| Disk           | 10 GB free      | Ollama models + database volumes                      |
| API key        | One provider    | Anthropic, OpenAI, Google, or xAI                     |

---

## Local Development

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/context-realm.git
cd context-realm
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and set the following at minimum:

```bash
LITELLM_MASTER_KEY=sk-<random>        # openssl rand -hex 32
WEBUI_SECRET_KEY=<random>             # openssl rand -hex 32
PIPELINES_API_KEY=<random>            # openssl rand -hex 32
POSTGRES_PASSWORD=<strong-password>
NEO4J_PASSWORD=<strong-password>
MEM0_VERSION=v0.1.40                  # pin to a specific upstream tag
```

Add at least one AI provider key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).

### 3. Build custom images

The Mem0 server and Pipeline images are built locally. Pull and build:

```bash
docker compose build
```

This clones the pinned Mem0 upstream release inside the Docker build context — nothing is cloned into this repository.

### 4. Start all services

```bash
docker compose up -d
```

Check that all containers are healthy:

```bash
docker compose ps
```

All services should show `healthy` or `running` within ~60 seconds. Neo4j takes the longest to initialise.

### 5. Run initial setup

Pull the embedding model and enable the pgvector extension:

```bash
bash scripts/setup.sh
```

This script:

- Pulls `qwen3-embedding:0.6b` into Ollama (~450 MB, one-time download)
- Optionally pulls a local LLM (see `OLLAMA_LLM_MODEL` in `.env`)
- Enables `CREATE EXTENSION IF NOT EXISTS vector` in Postgres

### 6. Open the UI

Navigate to [http://localhost:3000](http://localhost:3000) and create an admin account on first launch.

### 7. Register the Pipeline

1. Go to **Admin → Settings → Pipelines**
2. Set the Pipelines URL to `http://pipelines:9099` (or `http://localhost:9099` from the host)
3. Enter the `PIPELINES_API_KEY` from your `.env`
4. The `mem0_filter` pipeline should appear — enable it

### 8. Configure Open WebUI embeddings

1. Go to **Admin → Settings → Documents**
2. Set the Embedding Model Provider to **Ollama**
3. Set the Ollama URL to `http://ollama:11434`
4. Select `qwen3-embedding:0.6b`

### Stopping and restarting

```bash
# Stop all services (data is preserved in volumes)
docker compose down

# Stop and remove all data volumes (destructive)
docker compose down -v
```

---

## Configuration Reference

All configuration is done via `.env`. See `.env.example` for the full list with descriptions. Key variables:

| Variable             | Description                                          |
| -------------------- | ---------------------------------------------------- |
| `LITELLM_MASTER_KEY` | API key used by Open WebUI to call LiteLLM           |
| `ANTHROPIC_API_KEY`  | Anthropic API key (for Claude models)                |
| `OPENAI_API_KEY`     | OpenAI API key                                       |
| `GEMINI_API_KEY`     | Google Gemini API key                                |
| `GROK_API_KEY`       | xAI Grok API key                                     |
| `WEBUI_SECRET_KEY`   | Open WebUI session secret                            |
| `PIPELINES_API_KEY`  | Authentication between Open WebUI and Pipelines      |
| `MEM0_USER_ID`       | Default user ID for memory operations                |
| `MEM0_VERSION`       | Upstream Mem0 tag to build from                      |
| `POSTGRES_PASSWORD`  | Postgres admin password                              |
| `NEO4J_PASSWORD`     | Neo4j admin password                                 |
| `OLLAMA_BASE_URL`    | Override Ollama URL (for external VM/server in prod) |
| `REALM_NAME`         | Instance name for Helm/K8s multi-realm deployments   |

### Model configuration

Models are defined in `config/litellm_config.yaml`. Add, remove, or rename models freely — Open WebUI will pick up the list automatically. See the inline comments in that file for syntax.

---

## Memory Tuning

### Running on a 4 GB VPS

The default Neo4j configuration targets large servers and will exhaust a 4 GB machine. The `.env.example` includes tuned defaults that cap Neo4j at approximately 1 GB total:

```bash
NEO4J_server_memory_heap_initial__size=256m
NEO4J_server_memory_heap_max__size=512m
NEO4J_server_memory_pagecache__size=256m
NEO4J_server_memory_off__heap_transaction_max__size=128m
```

Approximate memory budget for 4 GB:

| Service              | Typical usage |
| -------------------- | ------------- |
| Neo4j (tuned)        | ~900 MB       |
| Postgres + pgvector  | ~300 MB       |
| Ollama (CPU, no GPU) | ~600 MB       |
| Mem0 server          | ~200 MB       |
| Open WebUI           | ~200 MB       |
| LiteLLM              | ~150 MB       |
| Pipelines            | ~100 MB       |
| **Total**            | **~2.5 GB**   |

This leaves ~1.5 GB for the OS and any local LLM you choose to run.

### Running a local LLM on 4 GB

A 7B parameter model quantised to Q4 requires approximately 4–5 GB of RAM. On a 4 GB VPS, this is not feasible alongside the other services.

Options:

- Use only frontier API models (Claude, GPT-4o, etc.) — no local LLM needed
- Run Ollama on a separate, larger VM and point `OLLAMA_BASE_URL` at it

---

## MCP Configuration

The OpenMemory MCP service exposes ContextRealm memories to Claude Desktop and Claude Code via the [Model Context Protocol](https://modelcontextprotocol.io/).

### Enable in Docker Compose

Uncomment the `openmemory-mcp` service in `docker-compose.yml`.

### Configure Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "contextrealm": {
      "url": "http://localhost:8765/sse"
    }
  }
}
```

Restart Claude Desktop. You should see "contextrealm" in the MCP servers list.

### Verify the connection

Ask Claude: _"What do you know about me?"_ — it should query your Mem0 store and return memories.

---

## Production Deployment

### Prerequisites

- A Kubernetes cluster (any cloud provider or self-hosted)
- `kubectl` and `helm` 3.x installed locally
- A container registry (any OCI-compatible registry)
- `cert-manager` installed on the cluster for TLS
- An ingress controller (e.g., ingress-nginx)

### 1. Push images to registry

```bash
# Build and push Mem0 server image
docker build \
  --build-arg MEM0_VERSION=$(grep MEM0_VERSION .env | cut -d= -f2) \
  -t <your-registry>/contextrealm-mem0:latest \
  ./docker/mem0-server
docker push <your-registry>/contextrealm-mem0:latest

# Build and push Pipeline image
docker build \
  -t <your-registry>/contextrealm-pipeline:latest \
  ./pipeline
docker push <your-registry>/contextrealm-pipeline:latest
```

### 2. Create Kubernetes secrets

```bash
kubectl create secret generic contextrealm-secrets \
  --from-env-file=.env \
  --namespace=contextrealm
```

Never put secrets in `helm/values.yaml`.

### 3. Deploy with Helm

```bash
# Personal realm
helm upgrade --install realm-personal ./helm \
  --namespace contextrealm \
  --create-namespace \
  --set realm.name=personal \
  --set openwebui.ingress.host=personal.contextrealm.yourdomain.com \
  --set mem0.image=<your-registry>/contextrealm-mem0:latest \
  --set pipeline.image=<your-registry>/contextrealm-pipeline:latest
```

### 4. Verify TLS

```bash
kubectl get certificate -n contextrealm
```

Certificates should reach `Ready: True` within a few minutes via cert-manager.

### 5. Post-deploy setup

```bash
# Pull Ollama models on the Ollama VM
ssh root@<ollama-server-ip> "ollama pull qwen3-embedding:0.6b"

# Enable pgvector on the managed Postgres instance
# (Run once per realm database)
kubectl exec -n contextrealm deploy/postgres -- \
  psql -U postgres -d mem0_personal -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### CI/CD

A GitHub Actions workflow in `.github/workflows/deploy.yml` automates image builds and Helm upgrades on push to `main`. See the workflow file for configuration.

---

## Troubleshooting

### Services fail to start

```bash
# Check logs for a specific service
docker compose logs mem0 --tail=50
docker compose logs neo4j --tail=50
```

### Neo4j OOM on low-RAM machine

Verify the memory env vars are set in `.env` and that `docker-compose.yml` passes them to the Neo4j container. Restart Neo4j:

```bash
docker compose restart neo4j
```

### Pipeline not appearing in Open WebUI

1. Verify the Pipelines container is running: `docker compose ps pipelines`
2. Verify the `PIPELINES_API_KEY` in `.env` matches what is entered in Open WebUI admin
3. Check Pipelines logs: `docker compose logs pipelines`

### Mem0 connection errors in pipeline

The pipeline fails gracefully — if Mem0 is down, chat still works without memory. To diagnose:

```bash
# Check Mem0 health
curl http://localhost:8000/health

# Check Mem0 logs
docker compose logs mem0 --tail=50
```

### Embedding model not found

Run `bash scripts/setup.sh` to pull the embedding model. Verify it is present:

```bash
docker exec contextrealm-ollama-1 ollama list
```

### pgvector extension not enabled

```bash
docker exec contextrealm-postgres-1 \
  psql -U postgres -d mem0 \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```
