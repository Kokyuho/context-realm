"""Test suite for ContextRealm.

Layout:

    tests/
    ├── unit/           # Pure Python, no external services. Runs on every PR.
    ├── integration/    # Hits live Mem0, Ollama, Postgres, Neo4j. Opt-in.
    ├── helpers/        # Shared utilities (REST clients, builders).
    └── conftest.py     # Root-level fixtures (env loading, config).

Run unit tests only (default):
    pytest

Run everything (requires the Docker stack):
    bash scripts/test.sh --integration
"""
