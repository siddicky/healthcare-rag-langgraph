from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Any, Final, Literal

from langgraph.store.memory import InMemoryStore

API_VERSION: Final = "0.12.6"


@dataclass(frozen=True, slots=True)
class CompatStoreUnavailableError(RuntimeError):
    def __str__(self) -> str:
        return "Agent Server store is not initialized"


class _StoreCompat:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        index: Literal[False] | list[str] | None = None,
        *,
        ttl: float | None = None,
    ) -> None:
        del ttl
        await self._store.aput(namespace, key, value, index=index)

    async def aget(
        self,
        namespace: tuple[str, ...],
        key: str,
        *,
        refresh_ttl: bool | None = None,
    ) -> Any:
        del refresh_ttl
        return await self._store.aget(namespace, key)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        await self._store.adelete(namespace, key)

    async def asearch(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        refresh_ttl: bool | None = None,
    ) -> list[Any]:
        del refresh_ttl
        return await self._store.asearch(
            namespace_prefix,
            query=query,
            filter=filter,
            limit=limit,
            offset=offset,
        )


_shared_store: _StoreCompat | None = None


async def _get_store() -> _StoreCompat:
    if _shared_store is None:
        raise CompatStoreUnavailableError
    return _shared_store


def install_langgraph_api_compat(store: InMemoryStore) -> bool:
    """Install the original compatibility modules only when the real API is absent."""
    global _shared_store
    try:
        _ = import_module("langgraph_api")
    except ModuleNotFoundError as error:
        if error.name != "langgraph_api":
            raise
    else:
        return False

    _shared_store = _StoreCompat(store)
    api_module = ModuleType("langgraph_api")
    store_module = ModuleType("langgraph_api.store")
    api_module.__dict__["__version__"] = API_VERSION
    store_module.__dict__["get_store"] = _get_store
    api_module.__dict__["store"] = store_module
    sys.modules["langgraph_api"] = api_module
    sys.modules["langgraph_api.store"] = store_module
    return True


__all__ = ["API_VERSION", "install_langgraph_api_compat"]
