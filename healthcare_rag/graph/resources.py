"""Lazy graph resources with test override support and no import-time connections."""

from __future__ import annotations

from collections.abc import Callable
from asyncio import Lock as AsyncLock
from threading import Lock
from typing import TYPE_CHECKING, Any

from weaviate.client import WeaviateAsyncClient
from weaviate.connect import ConnectionParams

from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.settings import GraphSettings

if TYPE_CHECKING:
    from healthcare_rag.graph.llm import PromptRegistry


class Resources:
    """Mutable resource owner; construction and network connection remain separate."""

    def __init__(self, settings: GraphSettings | None = None) -> None:
        self.settings: GraphSettings = settings or GraphSettings.from_env()
        self._prompts: PromptRegistry | None = None
        self.hybrid_search: Callable[..., Any] | None = None
        self.to_query_documents: Callable[..., Any] | None = None
        self.union_results: Callable[..., Any] | None = None
        self.format_documents_for_prompt: Callable[..., Any] | None = None
        self._weaviate: WeaviateAsyncClient | None = None
        self._gateway: LangChainLLMGateway | None = None
        self._lock: Lock = Lock()
        self._async_lock: AsyncLock = AsyncLock()

    @property
    def prompts(self) -> PromptRegistry:
        if self._prompts is None:
            from healthcare_rag.graph.prompts import get_registry

            self._prompts = get_registry()
        return self._prompts

    @prompts.setter
    def prompts(self, prompts: PromptRegistry | None) -> None:
        self._prompts = prompts

    async def weaviate(self) -> WeaviateAsyncClient:
        """Construct AND connect the async Weaviate client on first use.

        Connects eagerly (``await client.connect()``); weaviate-client v4
        refuses queries on an unconnected client.
        """
        async with self._async_lock:
            if self._weaviate is None:
                if not self.settings.openai_api_key:
                    message = "Required environment variable OPENAI_API_KEY is not set"
                    raise ValueError(message)
                self._weaviate = WeaviateAsyncClient(
                    connection_params=ConnectionParams.from_params(
                        http_host=self.settings.weaviate_host,
                        http_port=self.settings.weaviate_http_port,
                        http_secure=False,
                        grpc_host=self.settings.weaviate_host,
                        grpc_port=self.settings.weaviate_grpc_port,
                        grpc_secure=False,
                    ),
                    additional_headers={"X-OpenAI-Api-Key": self.settings.openai_api_key},
                )
                await self._weaviate.connect()
            return self._weaviate

    @property
    def gateway(self) -> LangChainLLMGateway:
        """Construct the shared model gateway on first access."""
        with self._lock:
            if self._gateway is None:
                self._gateway = LangChainLLMGateway(self.settings, self.prompts)
            return self._gateway

    async def aclose(self) -> None:
        """Close the Weaviate client only when this owner constructed and opened it."""
        client = self._weaviate
        if client is not None and client.is_connected():
            await client.close()


_instance: Resources | None = None
_INSTANCE_LOCK = Lock()


def get() -> Resources:
    """Return the process-wide lazy resources owner."""
    global _instance
    with _INSTANCE_LOCK:
        if _instance is None:
            _instance = Resources()
        return _instance


def override(resources: Resources) -> None:
    """Replace process resources for test injection."""
    global _instance
    with _INSTANCE_LOCK:
        _instance = resources
