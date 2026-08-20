import logging
from typing import Any
from uuid import uuid4

from ..models.retrieval import QueryResultList

logger = logging.getLogger("MedicalRAG")


def format_documents_for_prompt(
    results: QueryResultList | dict[str, Any],
) -> tuple[str, dict[str, str]]:
    if isinstance(results, dict):
        from ..graph.state import load_results

        retrieval_results = load_results(results)
    else:
        retrieval_results = results
    doc_context = ""
    prompt_id_to_original_id_map: dict[str, str] = {}
    doc_index = 0

    if not retrieval_results or not retrieval_results.results:
        return "", {}

    for result in retrieval_results.results:
        for doc in result.docs:
            original_doc_id = doc.doc_id or f"missing_id_{uuid4()}"
            prompt_doc_id = f"doc_{doc_index + 1}"
            prompt_id_to_original_id_map[prompt_doc_id] = original_doc_id

            doc_context += f"Document ID: [{prompt_doc_id}]\n"
            doc_context += f"Content: {doc.content}\n"
            doc_context += f"Source: {doc.source_name}\n"
            if doc.page_numbers:
                doc_context += f"Page Numbers: {doc.page_numbers}\n"
            doc_context += "---\n"
            doc_index += 1

    return doc_context.strip(), prompt_id_to_original_id_map
