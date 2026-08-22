from __future__ import annotations

from dataclasses import dataclass, field

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from server.config import ServerConfig


@dataclass(slots=True)
class Storage:
    saver: InMemorySaver
    store: InMemoryStore
    threads: dict[str, dict[str, object]] = field(default_factory=dict)
    runs: dict[str, dict[str, object]] = field(default_factory=dict)
    crons: dict[str, dict[str, object]] = field(default_factory=dict)


def create_storage(config: ServerConfig) -> Storage:
    saver = InMemorySaver()
    # store index from config: default to openai text-embedding-3-small dims 1536
    index_cfg = config.store_index or {"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"]}
    # Ensure required keys
    if "embed" not in index_cfg:
        index_cfg = {"embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"], **index_cfg}
    try:
        store = InMemoryStore(index=index_cfg)  # type: ignore[arg-type]
    except Exception:
        # fallback without embeddings if env missing key - still functional for checkpointer
        store = InMemoryStore(index=None)
    return Storage(saver=saver, store=store)
