"""
ContextRealm — mem0_filter pipeline  (Phase 5 placeholder)
===========================================================
This file satisfies the Open WebUI Pipelines loader interface so the service
starts cleanly.  It is a transparent pass-through — all messages flow through
unmodified until the full implementation replaces this file in Phase 5.

Phase 5 will implement:
  inlet  — query Mem0 for relevant memories and inject them into the system prompt
  outlet — store the conversation turn in Mem0 after the model responds
"""

from pydantic import BaseModel


class Pipeline:
    class Valves(BaseModel):
        # Disabled by default until Phase 5 implementation is in place.
        enabled: bool = False

    def __init__(self):
        self.name = "ContextRealm Mem0 Filter"
        self.valves = self.Valves()

    async def inlet(self, body: dict, user: dict | None = None) -> dict:
        """Pre-LLM hook — will inject memories into the system prompt."""
        return body

    async def outlet(self, body: dict, user: dict | None = None) -> dict:
        """Post-LLM hook — will store the conversation turn in Mem0."""
        return body
