import logging
import re
from typing import Final, Literal

logger = logging.getLogger("MedicalRAG")

FALLBACK_MESSAGE: Final = (
    "I'm sorry, I couldn't validate the information to answer your question."
)
Linebreak = Literal[
    "\\n",
    "\\n\\n",
    "\\n\\n\\n",
    "\n",
    "\n\n",
    "\n\n\n",
    "",
]


def format_statement(
    statement_text: str,
    valid_prompt_ids: list[str],
    linebreaks: Linebreak,
) -> str:
    cleaned_text = re.sub(r"[ \t]*\[doc_\d+\]", "", statement_text).strip(" \t")
    sorted_prompt_ids = sorted(
        set(valid_prompt_ids), key=lambda prompt_id: int(prompt_id.split("_")[1])
    )
    citations = " ".join(f"[{prompt_id}]" for prompt_id in sorted_prompt_ids)
    actual_linebreak = convert_linebreaks(linebreaks)
    parts = [part for part in (cleaned_text, citations) if part]
    return " ".join(parts) + actual_linebreak


def convert_linebreaks(linebreaks: str) -> str:
    if linebreaks in ("\\n", "\n"):
        return "\n"
    if linebreaks in ("\\n\\n", "\n\n"):
        return "\n\n"
    if linebreaks in ("\\n\\n\\n", "\n\n\n"):
        return "\n\n\n"
    if linebreaks:
        logger.warning(
            f"Unexpected linebreak value received: {linebreaks!r}. "
            + "Treating as no linebreak."
        )
    return ""


def join_statements(statements: list[str]) -> str:
    if not statements:
        return FALLBACK_MESSAGE
    answer = statements[0]
    for statement in statements[1:]:
        answer += (
            statement
            if answer.endswith("\n") or statement.startswith("\n")
            else f" {statement}"
        )
    return answer
