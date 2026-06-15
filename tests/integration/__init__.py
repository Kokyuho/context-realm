"""Integration test fixtures.

These tests hit live services (Mem0, Ollama, Postgres, Neo4j). The guard
fixture below skips the entire suite cleanly when the stack is down — so
"run the test suite" never fails with a connection error instead of a real
assertion failure.
"""
