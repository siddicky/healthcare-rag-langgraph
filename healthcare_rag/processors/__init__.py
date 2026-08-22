"""
Processor components for the healthcare RAG system.

Each module in this package contains the code for a specific part of the RAG
pipeline (the LangGraph nodes in ``healthcare_rag/graph/`` are the callers):
- base: shared utilities (log_timing)
- retrieval: Weaviate hybrid search, routing tool schemas, document union
- generation: document formatting for prompts
- validation: citation validation
- safety: runtime safety gate (PHI scrubbing + policy routing)
"""

from .base import log_timing
from .validation import AnswerValidator
from .safety import SafetyGate, SafetyDecision, scrub_phi

__all__ = [
    # Base utilities
    "log_timing",

    # Validation
    "AnswerValidator",

    # Safety
    "SafetyGate",
    "SafetyDecision",
    "scrub_phi",
]
