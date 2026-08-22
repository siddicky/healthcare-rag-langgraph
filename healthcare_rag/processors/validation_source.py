import logging
import re
from dataclasses import dataclass
from typing import Final

from ..models.answers import Citation, CitedAnswerResult, StatementWithCitations

logger = logging.getLogger("MedicalRAG")

_CITATION_GROUP_PATTERN: Final = re.compile(r"(?:[ \t]*\[doc_\d+\])+")
_CITATION_ID_PATTERN: Final = re.compile(r"\[(doc_\d+)\]")
_PUNCTUATION_PATTERN: Final = re.compile(r"[.,;:!?]+")
_SOURCE_SCAFFOLD_PREFIX_PATTERN: Final = re.compile(
    r"\A(?:Documents for citation context:|Document ID:[ \t]*(?:\r?\n|$)|SYSTEM(?:[ :].*)?(?:\r?\n|$))"
)


@dataclass(frozen=True, slots=True)
class SourceCitations:
    groups: tuple[re.Match[str], ...]
    expected_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnsafeSourceScaffold:
    pass


def find_source_citations(
    plain_answer: str,
) -> SourceCitations | UnsafeSourceScaffold | None:
    if _SOURCE_SCAFFOLD_PREFIX_PATTERN.match(plain_answer):
        return UnsafeSourceScaffold()
    groups = tuple(_CITATION_GROUP_PATTERN.finditer(plain_answer))
    if not groups:
        return None
    return SourceCitations(
        groups=groups,
        expected_ids=tuple(
            citation_id
            for group in groups
            for citation_id in _citation_ids(group.group())
        ),
    )


def _citation_ids(citation_group: str) -> tuple[str, ...]:
    return tuple(
        match.group(1) for match in _CITATION_ID_PATTERN.finditer(citation_group)
    )


def reconstruct_source_answer(
    plain_answer: str,
    structured_answer: CitedAnswerResult,
    source_citations: SourceCitations,
) -> CitedAnswerResult | None:
    citations_by_id: dict[str, Citation] = {
        citation.doc_id.strip("[]"): citation
        for statement in structured_answer.statements
        for citation in statement.citations
    }
    if set(citations_by_id) != set(source_citations.expected_ids):
        logger.warning(
            "Structured answer did not preserve the generated citation structure."
        )
        return None

    source_statements: list[StatementWithCitations] = []
    cursor = 0
    for group in source_citations.groups:
        citation_ids = _citation_ids(group.group())
        punctuation = _PUNCTUATION_PATTERN.match(plain_answer[group.end() :])
        punctuation_text = punctuation.group() if punctuation else ""
        source_statements.append(
            StatementWithCitations(
                text=plain_answer[cursor : group.start()].rstrip(" \t")
                + punctuation_text,
                citations=[
                    citations_by_id[citation_id].model_copy(deep=True)
                    for citation_id in citation_ids
                ],
                linebreaks="",
            )
        )
        cursor = group.end() + len(punctuation_text)
    return CitedAnswerResult(statements=source_statements)
