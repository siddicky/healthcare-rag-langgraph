"""
Legacy-free configuration helpers.

Model selection and sampling live in ``healthcare_rag/services/models.py``;
Weaviate construction for the graph engine lives in
``healthcare_rag/graph/resources.py``.
"""

import os
import logging
from typing import Optional

from dotenv import load_dotenv

# Load .env at import time so both this module and the OpenAI SDK (which
# reads OPENAI_API_KEY directly) pick up local development credentials.
load_dotenv()

logger = logging.getLogger("MedicalRAG")

def get_env_var(name: str, default: Optional[str] = None, required: bool = False) -> str:
    """
    Get an environment variable, with optional default and requirement flag.
    
    Args:
        name: Name of the environment variable
        default: Default value if not found
        required: Whether to raise an error if not found
        
    Returns:
        The value of the environment variable, or the default
        
    Raises:
        ValueError: If required is True and the environment variable is not set
    """
    value = os.environ.get(name, default)
    if value is None:
        if required:
            raise ValueError(f"Required environment variable {name} is not set")
        return ""  # Return empty string for None to fix type issues
    return value

