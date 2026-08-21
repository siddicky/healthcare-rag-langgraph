"""Lazy graph resources with test override support and no import-time connections."""

from __future__ import annotations

import logging
from asyncio import Lock as AsyncLock
from collections.abc import Callable
from functools import partial
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from weaviate.client import WeaviateAsyncClient
from weaviate.connect import ConnectionParams

from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.processors.privacy import PrivacySanitizer

if TYPE_CHECKING:
    from healthcare_rag.graph.llm import PromptRegistry

logger = logging.getLogger("MedicalRAG")


@runtime_checkable
class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


class Resources:
    """Mutable resource owner; construction and network connection remain separate."""

    def __init__(
        self,
        settings: GraphSettings | None = None,
        privacy: PrivacySanitizer | None = None,
    ) -> None:
        self.settings: GraphSettings = settings or GraphSettings.from_env()
        self._prompts: PromptRegistry | None = None
        self.hybrid_search: Callable[..., Any] | None = None
        self.to_query_documents: Callable[..., Any] | None = None
        self.union_results: Callable[..., Any] | None = None
        self.format_documents_for_prompt: Callable[..., Any] | None = None
        self._weaviate: WeaviateAsyncClient | None = None
        self._pinecone_client: Any | None = None
        self._pinecone_index: Any | None = None
        self._gateway: LangChainLLMGateway | None = None
        self._owned_gateway: LangChainLLMGateway | None = None
        self._privacy: PrivacySanitizer = privacy or PrivacySanitizer()
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
        refuses queries on an unconnected client. The client is published to
        the singleton only after a successful connect, so a failed connect
        leaves no partial state and the next call retries cleanly.
        """
        async with self._async_lock:
            if self._weaviate is None:
                if not self.settings.openai_api_key:
                    message = "Required environment variable OPENAI_API_KEY is not set"
                    raise ValueError(message)
                client = WeaviateAsyncClient(
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
                await client.connect()
                self._weaviate = client
            return self._weaviate

    async def pinecone_client(self) -> Any:
        """Construct the (sync) Pinecone client on first use.

        Needed by both the ``pinecone`` retrieval arm and the reranker — the
        reranker runs on the Weaviate arm too, where no index handle exists, so
        the client and the index are separate lazy resources.

        The SDK is synchronous and thread-safe; every call site drives it
        through ``anyio.to_thread``. Missing credentials raise immediately
        rather than letting an unauthenticated request hang.
        """
        async with self._async_lock:
            if self._pinecone_client is None:
                from pinecone import Pinecone

                if not self.settings.pinecone_api_key:
                    message = "PINECONE_API_KEY is not set"
                    raise ValueError(message)
                self._pinecone_client = Pinecone(api_key=self.settings.pinecone_api_key)
            return self._pinecone_client

    async def pinecone(self) -> Any:
        """Resolve the index handle for the ``pinecone`` retrieval arm.

        ``Pinecone.Index`` resolves the index host over the network, so the
        lookup runs in a worker thread and is cached like the Weaviate client.
        """
        client = await self.pinecone_client()
        async with self._async_lock:
            if self._pinecone_index is None:
                import anyio

                name = self.settings.pinecone_index_name
                self._pinecone_index = await anyio.to_thread.run_sync(
                    partial(client.Index, name)
                )
            return self._pinecone_index

    @property
    def gateway(self) -> LangChainLLMGateway:
        """Construct the shared model gateway on first access."""
        with self._lock:
            if self._gateway is None:
                gateway = LangChainLLMGateway(self.settings, self.prompts)
                self._gateway = gateway
                self._owned_gateway = gateway
            return self._gateway

    @property
    def privacy(self) -> PrivacySanitizer:
        return self._privacy

    async def aclose(self) -> None:
        """Close the clients this owner constructed and opened, and nothing else."""
        gateway = self._owned_gateway
        self._gateway = None
        self._owned_gateway = None
        if isinstance(gateway, AsyncCloseable):
            await gateway.aclose()

        client = self._weaviate
        self._weaviate = None
        if client is not None and client.is_connected():
            await client.close()

        index, pinecone_client = self._pinecone_index, self._pinecone_client
        self._pinecone_index = self._pinecone_client = None
        for closeable in (index, pinecone_client):
            if closeable is None:
                continue
            try:
                closeable.close()
            except Exception:  # teardown must not mask the real error.
                logger.debug("PINECONE_CLOSE_FAILED", exc_info=True)


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
