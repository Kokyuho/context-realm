"""
Patch 3 — make embedder and LLM providers configurable via env vars.

Applied at Docker build time against the cloned upstream server/main.py.
See Dockerfile for the full rationale and the new env vars this introduces.
"""

import pathlib
import sys

path = pathlib.Path("/mem0/server/main.py")
src = path.read_text()

ANCHOR_ENV = 'OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")'
ANCHOR_EMBEDDER = '    "embedder": {"provider": "openai", "config": {"api_key": OPENAI_API_KEY, "model": DEFAULT_EMBEDDER_MODEL}},'
ANCHOR_LLM = '    "llm": {\n        "provider": "openai",\n        "config": {"api_key": OPENAI_API_KEY, "temperature": 0.2, "model": DEFAULT_LLM_MODEL},\n    },'
ANCHOR_VECTOR_STORE = '            "collection_name": POSTGRES_COLLECTION_NAME,\n        },'

for anchor, label in [
    (ANCHOR_ENV, "OPENAI_API_KEY env read"),
    (ANCHOR_EMBEDDER, "DEFAULT_CONFIG embedder block"),
    (ANCHOR_LLM, "DEFAULT_CONFIG llm block"),
    (ANCHOR_VECTOR_STORE, "DEFAULT_CONFIG vector_store block"),
]:
    if anchor not in src:
        sys.exit(
            f"BUILD ERROR: Patch 3 anchor not found: {label!r}\n"
            f"  The upstream format may have changed for this MEM0_VERSION.\n"
            f"  Review docker/mem0-server/patch3.py and update the anchor strings."
        )

# Inject env reads + provider/model config-builders. The LLM block mirrors the
# embedder block: when provider=="ollama", the Ollama Python client needs an
# ``ollama_base_url`` in its config dict (same as the embedder). Without it,
# Mem0 silently fails with "Failed to connect to Ollama" at extraction time.
src = src.replace(
    ANCHOR_ENV,
    ANCHOR_ENV
    + (
        '\nOLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")'
        '\nOLLAMA_EMBEDDING_MODEL = os.environ.get("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")'
        '\nDEFAULT_EMBEDDER_PROVIDER = os.environ.get("MEM0_DEFAULT_EMBEDDER_PROVIDER", "ollama")'
        '\n_embedder_cfg = ({"model": OLLAMA_EMBEDDING_MODEL, "ollama_base_url": OLLAMA_BASE_URL}'
        '\n                 if DEFAULT_EMBEDDER_PROVIDER == "ollama"'
        '\n                 else {"api_key": OPENAI_API_KEY, "model": DEFAULT_EMBEDDER_MODEL})'
        '\nDEFAULT_LLM_PROVIDER = os.environ.get("MEM0_DEFAULT_LLM_PROVIDER", "openai")'
        '\nMEM0_LLM_API_KEY = os.environ.get("MEM0_LLM_API_KEY") or OPENAI_API_KEY'
        '\nDEFAULT_LLM_MODEL = os.environ.get("MEM0_DEFAULT_LLM_MODEL", "gpt-4.1-nano-2025-04-14")'
        '\n_llm_cfg = ({"model": DEFAULT_LLM_MODEL, "ollama_base_url": OLLAMA_BASE_URL}'
        '\n           if DEFAULT_LLM_PROVIDER == "ollama"'
        '\n           else {"api_key": MEM0_LLM_API_KEY, "temperature": 0.2, "model": DEFAULT_LLM_MODEL})'
    ),
    1,
)

src = src.replace(
    ANCHOR_EMBEDDER,
    '    "embedder": {"provider": DEFAULT_EMBEDDER_PROVIDER, "config": _embedder_cfg},',
    1,
)

src = src.replace(
    ANCHOR_LLM,
    '    "llm": {\n        "provider": DEFAULT_LLM_PROVIDER,\n        "config": _llm_cfg,\n    },',
    1,
)

# Inject embedding_model_dims into the pgvector config. Mem0's PGVectorConfig
# defaults to 1536, but our Ollama embedder (qwen3-embedding:0.6b) produces
# 1024-dim vectors. With a mismatch every insert silently fails with
# `expected 1536 dimensions, not 1024`, and `list` returns empty.
src = src.replace(
    ANCHOR_VECTOR_STORE,
    (
        '            "collection_name": POSTGRES_COLLECTION_NAME,\n'
        '            "embedding_model_dims": int(os.environ.get("MEM0_EMBEDDING_MODEL_DIMS", "1024")),'
        "\n        },"
    ),
    1,
)

path.write_text(src)
print(
    "Patch 3 OK: embedder defaults to ollama, LLM provider is configurable, "
    "LLM gets ollama_base_url when MEM0_DEFAULT_LLM_PROVIDER=ollama, "
    "pgvector embedding_model_dims is configurable (default 1024 for qwen3-embedding:0.6b)"
)
