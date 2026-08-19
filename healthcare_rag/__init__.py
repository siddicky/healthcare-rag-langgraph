"""
Healthcare RAG - A healthcare-focused retrieval-augmented generation system.

This package provides components for sophisticated RAG workflows focused on healthcare data.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline.medical_rag import MedicalRAG

__version__ = "0.1.0"

__all__ = [
    "MedicalRAG",
    "setup_medical_rag",
]


def __getattr__(
    name: str,
) -> type[MedicalRAG] | Callable[..., Awaitable[MedicalRAG]]:
    """Load legacy convenience exports without creating graph import cycles."""
    if name == "MedicalRAG":
        from .pipeline.medical_rag import MedicalRAG

        return MedicalRAG
    if name == "setup_medical_rag":
        from .config import setup_medical_rag

        return setup_medical_rag
    message = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(message)
