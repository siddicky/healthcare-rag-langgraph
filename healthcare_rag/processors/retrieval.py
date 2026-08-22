import logging

from ..models.retrieval import QueryDocument, QueryResult, QueryResultList
from openai.types.chat import ChatCompletionToolParam

# For type hints
from weaviate.client import WeaviateAsyncClient
from weaviate.classes.query import MetadataQuery, HybridFusion

logger = logging.getLogger("MedicalRAG")


def build_routing_tools(collection_names: list[str]) -> list[ChatCompletionToolParam]:
    tools: list[ChatCompletionToolParam] = []
    for collection_name in collection_names:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"query_{collection_name.lower()}",
                    "description": f"Get information about {collection_name} from the Weaviate database",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": f"The query from the user (verbatim) that should pertain to {collection_name}",
                            }
                        },
                        "required": ["query"],
                    },
                },
            }
        )
    return tools


def to_query_documents(search_results, collection_name: str) -> list[QueryDocument]:
    query_docs = []
    for obj in search_results:
        content = obj.properties.get("contextualized", "")
        score = obj.metadata.score if obj.metadata else 1.0
        doc_id = str(obj.uuid)
        page_numbers = obj.properties.get("page_numbers", None)
        metadata = {
            k: v for k, v in obj.properties.items() if k != "contextualized"
        }
        query_docs.append(
            QueryDocument(
                content=content,
                score=score,
                doc_id=doc_id,
                metadata=metadata,
                source_name=collection_name,
                page_numbers=page_numbers,
            )
        )
    return query_docs


DEFAULT_SEARCH_LIMIT = 4


async def hybrid_search(
    weaviate_client: WeaviateAsyncClient,
    collection_name: str,
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> QueryResultList:
    """Weaviate hybrid search. ``limit`` is only raised when a reranker will trim it back."""
    collection = weaviate_client.collections.get(collection_name)
    response = await collection.query.hybrid(
        query=query,
        query_properties=["contextualized"],
        limit=limit,
        alpha=0.65,
        fusion_type=HybridFusion.RELATIVE_SCORE,
        return_metadata=MetadataQuery(score=True),
    )
    query_docs = to_query_documents(response.objects, collection_name)
    return QueryResultList(
        results=[QueryResult(source=collection_name, query=query, docs=query_docs)]
    )


def union_results(
    lists: list[QueryResultList | None] | None,
) -> QueryResultList:
    seen_doc_ids: set[str] = set()
    by_source: dict[str, QueryResult] = {}
    source_order: list[str] = []

    for result_list in lists or []:
        if result_list is None or not result_list.results:
            continue
        for result in result_list.results:
            for doc in result.docs or []:
                if doc.doc_id in seen_doc_ids:
                    continue
                seen_doc_ids.add(doc.doc_id)
                if result.source not in by_source:
                    by_source[result.source] = QueryResult(
                        source=result.source, query=result.query, docs=[]
                    )
                    source_order.append(result.source)
                by_source[result.source].docs.append(doc)

    return QueryResultList(results=[by_source[source] for source in source_order])
