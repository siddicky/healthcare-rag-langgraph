"""
Query monitoring for the healthcare RAG orchestrator.

This module provides utilities for tracking and displaying query processing progress.
"""

import asyncio
import logging
import re
from typing import List, Optional

from .processors.safety import scrub_phi

logger = logging.getLogger(__name__)

class QueryMonitor:
    """Monitor for tracking query processing and displaying live updates."""
    
    def __init__(self):
        """Initialize a new query monitor."""
        self.current_step: str = "initializing"
        self.status_message: str = "Starting query processing..."
        self.steps_completed: List[str] = []
        self.raw_answer: Optional[str] = None
        self.raw_answer_event = asyncio.Event()
        self.final_answer: Optional[str] = None
        self.final_answer_event = asyncio.Event()
        self.follow_up_questions: Optional[List[str]] = None
        self.error: Optional[str] = None

    def update_status(self, step: str, message: Optional[str] = None) -> None:
        """Update the current status of the query processing."""
        self.current_step = step
        if message:
            self.status_message = message
        else:
            self.status_message = f"Processing: {step.replace('_', ' ').title()}..."
        
        self.steps_completed.append(step)
        logger.debug(f"QueryMonitor: {self.status_message}")

    def set_raw_answer(self, answer: str | None) -> None:
        self.raw_answer = scrub_phi(answer or "")[0] or None
        self.raw_answer_event.set()

    def set_final_answer(self, answer: str | None) -> None:
        self.final_answer = scrub_phi(answer or "")[0] or None
        self.final_answer_event.set()

    def set_follow_up_questions(self, questions: list[str] | None) -> None:
        self.follow_up_questions = [scrub_phi(question)[0] for question in questions or []]

    def set_error(self, code: str) -> None:
        self.error = code if re.fullmatch(r"[A-Z][A-Z0-9_]*", code) else "PIPELINE_EXECUTION_FAILED"
        self.raw_answer_event.set()
        self.final_answer_event.set()

    async def display_progress(self) -> None:
        """Display a live progress indicator for the query processing."""
        spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        
        steps_display = {
            "initializing": "Initializing...",
            "retrieving": "Retrieving relevant information...",
            "processing": "Processing query...",
            "generating": "Generating answer...",
            "validating": "Validating and structuring answer...",
            "decomposing": "Breaking question into parts...",
            "clarifying": "Clarifying ambiguous terms...",
            "refining": "Refining the answer...",
            "completed": "Completed!",
        }
        
        last_message = ""
        
        while not self.final_answer_event.is_set():
            # Get the current step message or use a default message
            step_key = next((k for k in steps_display if k in self.current_step.lower()), None)
            display_msg = steps_display.get(step_key, self.status_message) if step_key else self.status_message
            
            # Only update if the message changed
            if display_msg != last_message:
                print(f"\r\033[K{display_msg}", end="", flush=True)
                last_message = display_msg
            else:
                # Just update the spinner
                spinner = spinner_chars[i % len(spinner_chars)]
                print(f"\r\033[K{display_msg} {spinner}", end="", flush=True)
            
            i += 1
            await asyncio.sleep(0.1)
        
        # Clear the line when done
        print("\r\033[K", end="", flush=True)
