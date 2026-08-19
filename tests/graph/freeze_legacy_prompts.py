"""One-shot regeneration tool for freezing legacy prompt renders before migration.

Run from the repository checkout with:
    uv run python tests/graph/freeze_legacy_prompts.py

This intentionally reads the legacy top-level ``prompts/`` directory and must be
run before those templates are moved into the package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from healthcare_rag.processors.base import PromptManager


@dataclass(frozen=True, slots=True)
class RenderCase:
    stage: str
    case: str
    template: str
    variables: dict[str, str]


ROOT: Final = Path(__file__).parents[2]
FIXTURES_DIR: Final = Path(__file__).parent / "fixtures" / "legacy_renders"

FORMATTED_DOCUMENTS: Final = """[doc_1]
    Source: Lipitor monograph
    Content: First line of evidence.
        Indented continuation.

[doc_2]
    Source: Metformin monograph
    Content: Second line of evidence."""

CASES: Final = (
    RenderCase(
        stage="generate_answer",
        case="with_conversation_context",
        template="answer_generation",
        variables={
            "user_question": "What's stated about dosing?",
            "retrieval_results": FORMATTED_DOCUMENTS,
            "conversation_context": "Earlier, the user asked about Lipitor's use.",
        },
    ),
    RenderCase(
        stage="generate_answer",
        case="without_conversation_context",
        template="answer_generation",
        variables={
            "user_question": "What's stated about dosing?",
            "retrieval_results": FORMATTED_DOCUMENTS,
            "conversation_context": "",
        },
    ),
    RenderCase(
        stage="safety_gate",
        case="autoescape",
        template="safety_gate",
        variables={
            "user_query": "What's Lipitor < metformin & why?",
            "conversation_context": "The user's earlier question was informational.",
        },
    ),
    RenderCase(
        stage="clarify_query",
        case="conversation_context",
        template="clarify_query",
        variables={
            "user_query": "What's its main warning?",
            "conversation_context": "They asked about Lipitor's side effects.",
        },
    ),
    RenderCase(
        stage="decompose_query",
        case="apostrophe",
        template="decompose_query",
        variables={"user_query": "What's Lipitor's use and metformin's warning?"},
    ),
    RenderCase(
        stage="extract_conversation_context",
        case="multiline_history",
        template="context_extraction",
        variables={
            "current_query": "Does that warning apply?",
            "history_text": "User: What's Lipitor for?\nAssistant: It is described in the monograph.",
        },
    ),
    RenderCase(
        stage="evaluate_retrieval",
        case="multiple_documents",
        template="retrieval_evaluation",
        variables={
            "original_query": "Compare the drugs' warnings.",
            "clarified_query": "Compare Lipitor's and metformin's warnings.",
            "retrieved_information": FORMATTED_DOCUMENTS,
            "sources": "Lipitor monograph; Metformin monograph",
        },
    ),
    RenderCase(
        stage="validate_answer",
        case="formatted_documents",
        template="answer_structuring",
        variables={
            "answer": "Lipitor's warning is described here [doc_1].",
            "retrieval_results": FORMATTED_DOCUMENTS,
        },
    ),
    RenderCase(
        stage="generate_follow_ups",
        case="multiline_history",
        template="follow_up_questions",
        variables={
            "history_context": "User: What's Lipitor for?\nAssistant: The monograph describes its uses.",
            "original_query": "What's Lipitor's main use?",
            "answer": "It's used as described in the monograph.",
        },
    ),
)


def main() -> None:
    prompt_manager = PromptManager(ROOT / "prompts")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    for render_case in CASES:
        messages = prompt_manager.messages(
            render_case.template,
            **render_case.variables,
        )
        fixture = {
            "stage": render_case.stage,
            "case": render_case.case,
            "vars": render_case.variables,
            "messages": messages,
        }
        fixture_path = (
            FIXTURES_DIR / f"{render_case.stage}__{render_case.case}.json"
        )
        _ = fixture_path.write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
