"""
Task wrappers for the healthcare RAG orchestrator.

This module provides thin async wrappers around MedicalRAG processor methods.
"""

import logging
from typing import List, Optional, Tuple

from ..models.queries import ClarifiedQuery, DecomposedQuery, RetrievalEvaluation
from ..models.retrieval import QueryResultList
from ..models.answers import CitedAnswerResult, AnswerGenerationResult, RelevantHistoryContext
from ..models.misc import ConversationEntry, FollowUpQuestions
from ..pipeline.medical_rag import MedicalRAG
from ..services.tracing import rag_stage, query_result_list_to_documents
from ..services.models import stage_enabled

logger = logging.getLogger("MedicalRAG")

# Task wrappers that call through to MedicalRAG components
@rag_stage("clarify_query")
async def clarify_query(
    rag: MedicalRAG, query: str, context: str = ""
) -> ClarifiedQuery:
    """
    Clarify an ambiguous query using conversation context.
    
    Args:
        rag: The MedicalRAG instance
        query: The user's query to clarify
        context: Optional conversation context
        
    Returns:
        A ClarifiedQuery object with the clarified query
    """
    if not stage_enabled("clarify"):
        return ClarifiedQuery(original_query=query, ambiguity_level="clear and specific", clarified_query=query)
    return await rag.preprocessor.clarify_query_async(
        user_query=query, conversation_context=context
    )

@rag_stage("decompose_query")
async def decompose_query(rag: MedicalRAG, query: str) -> DecomposedQuery:
    """
    Decompose a complex query into simpler subqueries.
    
    Args:
        rag: The MedicalRAG instance
        query: The user's query to decompose
        
    Returns:
        A DecomposedQuery object with the decomposed subqueries
    """
    if not stage_enabled("decompose"):
        return DecomposedQuery(original_query=query, query_complexity="simple", decomposed_query=[query])
    return await rag.preprocessor.decompose_query_async(query)

@rag_stage("retrieve_documents", run_type="retriever", process_outputs=query_result_list_to_documents)
async def retrieve_documents(rag: MedicalRAG, query: str) -> QueryResultList:
    """
    Retrieve documents relevant to the query.
    
    Args:
        rag: The MedicalRAG instance
        query: The query to retrieve documents for
        
    Returns:
        A QueryResultList containing retrieved documents
    """
    return await rag.router.route_query_async(query)

@rag_stage("evaluate_retrieval", process_outputs=query_result_list_to_documents)
async def evaluate_retrieval(
    rag: MedicalRAG, query: str, results: QueryResultList
) -> QueryResultList:
    """
    Evaluate the retrieval results and augment if needed.
    
    Args:
        rag: The MedicalRAG instance
        query: The user's query
        results: The initial retrieval results
        
    Returns:
        A potentially augmented QueryResultList
    """
    if not stage_enabled("evaluate"):
        return results
    return await rag.evaluator.evaluate_retrieval(
        original_query=query,
        clarified_query=query,
        retrieval_results=results,
        router=rag.router,
    )

@rag_stage("extract_conversation_context")
async def extract_conversation_context(
    rag: MedicalRAG, query: str, history: List[ConversationEntry]
) -> RelevantHistoryContext:
    """
    Extract relevant context from conversation history.
    
    Args:
        rag: The MedicalRAG instance
        query: The user's query
        history: List of conversation entries
        
    Returns:
        A RelevantHistoryContext object
    """
    return await rag.context_processor.extract_relevant_context(
        query=query, conversation_history=history
    )

@rag_stage("generate_answer")
async def generate_answer(
    rag: MedicalRAG, results: QueryResultList, query: str, summary: RelevantHistoryContext
) -> AnswerGenerationResult:
    """
    Generate an answer from the retrieved documents and conversation context.
    
    Args:
        rag: The MedicalRAG instance
        results: The retrieval results
        query: The user's query
        summary: The context from conversation history
        
    Returns:
        An AnswerGenerationResult object
    """
    return await rag.generator.generate_answer_async(
        user_question=query,
        retrieval_results=results,
        conversation_context=summary.relevant_snippets or "",
    )

@rag_stage("validate_answer")
async def validate_answer(
    rag: MedicalRAG, generation_result: AnswerGenerationResult
) -> Tuple[Optional[CitedAnswerResult], Optional[str]]:
    """
    Validate an answer by checking citations.
    
    Args:
        rag: The MedicalRAG instance
        generation_result: The result from answer generation
        
    Returns:
        A tuple of (structured_answer, validated_text)
    """
    if not stage_enabled("validate"):
        # Ablation: skip structuring/citation validation and pass the raw answer through.
        return None, generation_result.plain_answer
    return await rag.validator.structure_and_validate_async(
        plain_answer=generation_result.plain_answer,
        retrieval_results=generation_result.retrieval_results,
        formatted_docs=generation_result.formatted_docs,
        prompt_id_map=generation_result.prompt_id_map,
    )

@rag_stage("generate_follow_ups")
async def generate_follow_ups(
    rag: MedicalRAG, query: str, answer: str, history: List[ConversationEntry]
) -> FollowUpQuestions:
    """
    Generate follow-up questions based on the answer.
    
    Args:
        rag: The MedicalRAG instance
        query: The user's query
        answer: The answer provided to the user
        history: List of conversation entries
        
    Returns:
        A FollowUpQuestions object
    """
    if not stage_enabled("followups"):
        return FollowUpQuestions(questions=[])
    return await rag.follow_up_generator.generate_follow_up_questions(
        query=query,
        answer=answer,
        conversation_history=history,
    ) 