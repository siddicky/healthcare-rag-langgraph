"""
Orchestrator tests for decomposition capping + sub-branch synthesis.

Background (docs/journey.json):
  * F06 — decomposition created one branch per sub-query and ``_select_best_answer``
    returned a *single* sub-branch's validated answer, so "I take metformin, is it safe
    to add Lipitor?" was answered with "What is metformin used for?".
  * F07 — gpt-5.6-luna decomposes aggressively (up to 8 sub-queries, even for
    out-of-scope questions) and every sub-branch paid retrieve+evaluate+answer+validate.

The fix: cap the fan-out (``HC_RAG_MAX_SUBQUERIES``), only decompose queries the
decomposer called "complex" (``HC_RAG_DECOMPOSE_ONLY_COMPLEX``), and merge the
sub-branches' documents into one "synthesized" branch that answers the *original*
query exactly once (``HC_RAG_SYNTHESIS``).

These tests drive the real orchestrator with a fake MedicalRAG: no network, no
Weaviate, no OpenAI. They assert on branch types and on how often each stage is called.
"""

from __future__ import annotations

import asyncio

import pytest

from healthcare_rag.models.answers import (
    AnswerGenerationResult,
    Citation,
    CitedAnswerResult,
    RelevantHistoryContext,
    StatementWithCitations,
)
from healthcare_rag.models.misc import FollowUpQuestions
from healthcare_rag.models.queries import ClarifiedQuery, DecomposedQuery
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.orch.branch import BranchStatus
from healthcare_rag.orch.orchestrator import RefactoredOrchestrator


# --------------------------------------------------------------------------- #
# Fixtures / fakes                                                             #
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """Pin the settings the tests care about so a developer's .env cannot change them."""
    monkeypatch.setenv("HC_RAG_DISABLE_STAGES", "")
    # These tests exercise the retrieval/answer pipeline, not the safety gate; the fake
    # RAG has no gate to call. Gate behaviour lives in tests/test_safety_gate.py.
    monkeypatch.setenv("HC_RAG_SAFETY_GATE", "false")
    monkeypatch.setenv("HC_RAG_SYNTHESIS", "true")
    monkeypatch.setenv("HC_RAG_DECOMPOSE_ONLY_COMPLEX", "true")
    monkeypatch.setenv("HC_RAG_MAX_SUBQUERIES", "3")


def doc(doc_id: str, source: str) -> QueryDocument:
    return QueryDocument(
        content=f"content of {doc_id}",
        score=0.9,
        doc_id=doc_id,
        source_name=source,
        metadata={"id_": doc_id},
    )


def results_for(query: str, source: str, docs: list[QueryDocument]) -> QueryResultList:
    return QueryResultList(results=[QueryResult(source=source, query=query, docs=docs)])


class FakeHistory:
    def __init__(self):
        self.entries: list[tuple[str, str, str]] = []

    def get_history(self, user_id: str, limit: int = 5):
        return []

    def get_context_from_history(self, user_id: str, limit: int = 3) -> str:
        return ""

    def add_entry(self, user_id: str, query: str, answer: str) -> None:
        self.entries.append((user_id, query, answer))


class FakePreprocessor:
    def __init__(self, rag: "FakeRag"):
        self.rag = rag

    async def clarify_query_async(self, user_query: str, conversation_context: str = ""):
        self.rag.calls["clarify"].append(user_query)
        return ClarifiedQuery(
            original_query=user_query,
            ambiguity_level="clear and specific",
            clarified_query=user_query,
        )

    async def decompose_query_async(self, query: str):
        self.rag.calls["decompose"].append(query)
        complexity, sub_qs = self.rag.decomposition
        # model_construct bypasses DecomposedQuery's validator on purpose: it lets a test
        # feed the handler a raw ("simple", [many sub-queries]) shape and check the gate.
        return DecomposedQuery.model_construct(
            original_query=query,
            query_complexity=complexity,
            decomposed_query=list(sub_qs),
        )


class FakeRouter:
    def __init__(self, rag: "FakeRag"):
        self.rag = rag

    async def route_query_async(self, query: str) -> QueryResultList:
        self.rag.calls["retrieve"].append(query)
        if query in self.rag.retrieval_errors:
            raise RuntimeError(f"retrieval blew up for {query!r}")
        source, doc_ids = self.rag.docs_by_query.get(query, (None, []))
        if not doc_ids:
            return QueryResultList(results=[])
        return results_for(query, source, [doc(d, source) for d in doc_ids])


class FakeEvaluator:
    def __init__(self, rag: "FakeRag"):
        self.rag = rag

    async def evaluate_retrieval(self, original_query, clarified_query, retrieval_results, router):
        self.rag.calls["evaluate"].append(original_query)
        return retrieval_results  # no gap-filling in the fake


class FakeGenerator:
    def __init__(self, rag: "FakeRag"):
        self.rag = rag

    async def generate_answer_async(self, user_question, retrieval_results, conversation_context):
        self.rag.calls["answer"].append((user_question, retrieval_results))
        return AnswerGenerationResult(
            plain_answer=f"answer to: {user_question}",
            retrieval_results=retrieval_results,
            formatted_docs="formatted",
            prompt_id_map={},
            user_question=user_question,
            conversation_context=conversation_context,
        )


class FakeValidator:
    def __init__(self, rag: "FakeRag"):
        self.rag = rag

    async def structure_and_validate_async(self, plain_answer, retrieval_results, formatted_docs, prompt_id_map):
        self.rag.calls["validate"].append(plain_answer)
        structured = CitedAnswerResult(
            statements=[
                StatementWithCitations(
                    text=plain_answer,
                    citations=[Citation(doc_id="d", source_name="s", quote="q")],
                    linebreaks="",
                )
            ]
        )
        return structured, f"validated: {plain_answer}"


class FakeContextProcessor:
    def __init__(self, rag: "FakeRag"):
        self.rag = rag

    async def extract_relevant_context(self, query, conversation_history):
        if self.rag.summary_delay_s:
            await asyncio.sleep(self.rag.summary_delay_s)
        self.rag.calls["summary"].append(query)
        if self.rag.summary_fails:
            raise RuntimeError("history summarisation blew up")
        return RelevantHistoryContext(
            explanation="", required_context=False, relevant_snippets=""
        )


class FakeFollowUps:
    def __init__(self, rag: "FakeRag"):
        self.rag = rag

    async def generate_follow_up_questions(self, query, answer, conversation_history):
        self.rag.calls["followups"].append(query)
        return FollowUpQuestions(questions=["q1"])


class FakeRag:
    """Stands in for MedicalRAG: only the attributes orch/tasks.py touches."""

    def __init__(
        self,
        decomposition,
        docs_by_query,
        retrieval_errors=(),
        summary_delay_s=0.0,
        summary_fails=False,
    ):
        self.decomposition = decomposition          # (complexity, [sub-queries])
        self.docs_by_query = docs_by_query          # query -> (source, [doc_ids])
        self.retrieval_errors = set(retrieval_errors)
        self.summary_delay_s = summary_delay_s      # makes the summary land *after* synthesis
        self.summary_fails = summary_fails
        self.calls: dict[str, list] = {
            "clarify": [], "decompose": [], "retrieve": [], "evaluate": [],
            "answer": [], "validate": [], "summary": [], "followups": [],
        }
        self.preprocessor = FakePreprocessor(self)
        self.router = FakeRouter(self)
        self.evaluator = FakeEvaluator(self)
        self.generator = FakeGenerator(self)
        self.validator = FakeValidator(self)
        self.context_processor = FakeContextProcessor(self)
        self.follow_up_generator = FakeFollowUps(self)
        self.conversation_history = FakeHistory()


ORIGINAL = "I take metformin for my diabetes. Is it safe to add Lipitor?"
SUB_A = "What is metformin used for?"
SUB_B = "Does Lipitor interact with metformin?"
SUB_C = "What are the side effects of Lipitor?"
SUB_D = "What is the dose of Lipitor?"
SUB_E = "Who should not take Lipitor?"


def two_part_rag(**kwargs) -> FakeRag:
    return FakeRag(
        decomposition=("complex", [SUB_A, SUB_B]),
        docs_by_query={
            ORIGINAL: ("Metformin", ["shared-1"]),
            SUB_A: ("Metformin", ["shared-1", "met-2"]),
            SUB_B: ("Lipitor", ["lip-1"]),
        },
        **kwargs,
    )


def doc_ids_of(results: QueryResultList) -> list[str]:
    return [d.doc_id for r in results.results for d in r.docs]


def branch_types(orch: RefactoredOrchestrator) -> list[str]:
    return [b.branch_type for b in orch.branches.values()]


# --------------------------------------------------------------------------- #
# (a) complex query -> 2 sub-queries -> one synthesis                          #
# --------------------------------------------------------------------------- #

async def test_complex_query_synthesises_one_answer_to_the_original_question():
    rag = two_part_rag()
    orch = RefactoredOrchestrator(rag)

    answer, follow_ups = await orch.process_query(ORIGINAL, "u1")

    types = branch_types(orch)
    assert types.count("synthesized") == 1, types
    assert types.count("decomposed_0") == 1 and types.count("decomposed_1") == 1, types

    # Exactly one answer + one validation for the whole query (F07: was 1 per sub-branch).
    assert len(rag.calls["answer"]) == 1, rag.calls["answer"]
    assert len(rag.calls["validate"]) == 1, rag.calls["validate"]

    # The answer is to the ORIGINAL question, not to a sub-question (F06).
    answered_question, answer_results = rag.calls["answer"][0]
    assert answered_question == ORIGINAL

    # ... over the union of both children's docs, de-duplicated by doc_id.
    ids = doc_ids_of(answer_results)
    assert sorted(ids) == ["lip-1", "met-2", "shared-1"], ids
    assert len(ids) == len(set(ids)), f"duplicate docs leaked into synthesis: {ids}"

    # Children never generate.
    assert answer == f"validated: answer to: {ORIGINAL}"
    assert orch.final_answer_source_branch_id in orch.synthesis_branch_ids
    synth = orch.branches[orch.final_answer_source_branch_id]
    assert synth.branch_type == "synthesized"
    assert synth.status == BranchStatus.COMPLETED

    # Sub-branches are retired, and each ran retrieve+evaluate only.
    children = [b for b in orch.branches.values() if b.branch_type.startswith("decomposed_")]
    assert all(c.status == BranchStatus.SUPERSEDED for c in children)
    assert all(c.raw_answer is None and c.validated_answer_str is None for c in children)
    # (the parent's own evaluate may or may not fire, depending on whether its
    # speculative retrieval lands before the decomposition supersedes it)
    assert {SUB_A, SUB_B} <= set(rag.calls["evaluate"])
    assert follow_ups == ["q1"]


# --------------------------------------------------------------------------- #
# (b) 'simple' complexity -> no decomposition                                  #
# --------------------------------------------------------------------------- #

async def test_simple_complexity_does_not_decompose_even_with_sub_queries():
    rag = FakeRag(
        decomposition=("simple", [SUB_A, SUB_B, SUB_C]),
        docs_by_query={ORIGINAL: ("Metformin", ["shared-1"])},
    )
    orch = RefactoredOrchestrator(rag)

    answer, _ = await orch.process_query(ORIGINAL, "u2")

    types = branch_types(orch)
    assert types == ["initial"], types
    assert not orch.decomposition_groups
    assert rag.calls["retrieve"] == [ORIGINAL]
    assert len(rag.calls["answer"]) == 1
    assert rag.calls["answer"][0][0] == ORIGINAL
    assert answer == f"validated: answer to: {ORIGINAL}"


async def test_decompose_only_complex_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("HC_RAG_DECOMPOSE_ONLY_COMPLEX", "false")
    rag = FakeRag(
        decomposition=("simple", [SUB_A, SUB_B]),
        docs_by_query={SUB_A: ("Metformin", ["met-2"]), SUB_B: ("Lipitor", ["lip-1"])},
    )
    orch = RefactoredOrchestrator(rag)

    await orch.process_query(ORIGINAL, "u2b")

    assert branch_types(orch).count("synthesized") == 1


# --------------------------------------------------------------------------- #
# (c) fan-out cap                                                              #
# --------------------------------------------------------------------------- #

async def test_five_sub_queries_are_truncated_to_max_subqueries():
    rag = FakeRag(
        decomposition=("complex", [SUB_A, SUB_B, SUB_C, SUB_D, SUB_E]),
        docs_by_query={
            SUB_A: ("Metformin", ["met-2"]),
            SUB_B: ("Lipitor", ["lip-1"]),
            SUB_C: ("Lipitor", ["lip-2"]),
            SUB_D: ("Lipitor", ["lip-3"]),
            SUB_E: ("Lipitor", ["lip-4"]),
        },
    )
    orch = RefactoredOrchestrator(rag)

    await orch.process_query(ORIGINAL, "u3")

    children = sorted(
        b.branch_type for b in orch.branches.values() if b.branch_type.startswith("decomposed_")
    )
    assert children == ["decomposed_0", "decomposed_1", "decomposed_2"], children
    # ORIGINAL is the initial branch's own (speculative) retrieval.
    assert sorted(rag.calls["retrieve"]) == sorted([ORIGINAL, SUB_A, SUB_B, SUB_C])
    assert SUB_D not in rag.calls["retrieve"] and SUB_E not in rag.calls["retrieve"]

    _, answer_results = rag.calls["answer"][0]
    assert sorted(doc_ids_of(answer_results)) == ["lip-1", "lip-2", "met-2"]


async def test_max_subqueries_is_configurable(monkeypatch):
    monkeypatch.setenv("HC_RAG_MAX_SUBQUERIES", "2")
    rag = FakeRag(
        decomposition=("complex", [SUB_A, SUB_B, SUB_C]),
        docs_by_query={
            SUB_A: ("Metformin", ["met-2"]),
            SUB_B: ("Lipitor", ["lip-1"]),
            SUB_C: ("Lipitor", ["lip-2"]),
        },
    )
    orch = RefactoredOrchestrator(rag)

    await orch.process_query(ORIGINAL, "u3b")

    children = [b for b in orch.branches.values() if b.branch_type.startswith("decomposed_")]
    assert len(children) == 2, [b.branch_type for b in children]


# --------------------------------------------------------------------------- #
# (d) one child fails -> synthesis still happens                               #
# --------------------------------------------------------------------------- #

async def test_synthesis_survives_a_failing_sub_branch():
    # No docs mapped for ORIGINAL: whether the parent's own speculative retrieval lands
    # before the decomposition supersedes it is a race, so keep it out of this assertion.
    rag = FakeRag(
        decomposition=("complex", [SUB_A, SUB_B]),
        docs_by_query={SUB_B: ("Lipitor", ["lip-1"])},
        retrieval_errors=[SUB_A],
    )
    orch = RefactoredOrchestrator(rag)

    answer, _ = await orch.process_query(ORIGINAL, "u4")

    assert branch_types(orch).count("synthesized") == 1
    failed = [b for b in orch.branches.values() if b.status == BranchStatus.FAILED]
    assert [b.branch_type for b in failed] == ["decomposed_0"]

    assert len(rag.calls["answer"]) == 1
    answered_question, answer_results = rag.calls["answer"][0]
    assert answered_question == ORIGINAL
    assert doc_ids_of(answer_results) == ["lip-1"]
    assert answer == f"validated: answer to: {ORIGINAL}"


async def test_all_sub_branches_empty_gives_no_answer():
    rag = FakeRag(
        decomposition=("complex", [SUB_A, SUB_B]),
        docs_by_query={},  # every retrieval comes back empty
    )
    orch = RefactoredOrchestrator(rag)

    answer, _ = await orch.process_query(ORIGINAL, "u5")

    synth = [b for b in orch.branches.values() if b.branch_type == "synthesized"]
    assert len(synth) == 1
    assert synth[0].status == BranchStatus.FAILED
    assert rag.calls["answer"] == []
    assert answer is None


# --------------------------------------------------------------------------- #
# (e) synthesis off -> old behaviour                                           #
# --------------------------------------------------------------------------- #

async def test_synthesis_disabled_restores_per_sub_branch_answers(monkeypatch):
    monkeypatch.setenv("HC_RAG_SYNTHESIS", "false")
    rag = two_part_rag()
    orch = RefactoredOrchestrator(rag)

    answer, _ = await orch.process_query(ORIGINAL, "u6")

    types = branch_types(orch)
    assert "synthesized" not in types, types
    # Each sub-branch answers and validates on its own (the expensive old behaviour).
    assert sorted(q for q, _ in rag.calls["answer"]) == sorted([SUB_A, SUB_B])
    assert len(rag.calls["validate"]) == 2
    # ... and a decomposed child's answer wins.
    winner = orch.branches[orch.final_answer_source_branch_id]
    assert winner.branch_type.startswith("decomposed_")
    assert answer in {f"validated: answer to: {SUB_A}", f"validated: answer to: {SUB_B}"}


# --------------------------------------------------------------------------- #
# Branch-trait ordering                                                        #
# --------------------------------------------------------------------------- #

def test_synthesized_outranks_every_other_trait():
    traits = RefactoredOrchestrator.BranchTraits
    synthesized = traits(synthesized=True, clarified=False, decomposed=False, gap_filled=False)
    everything_else = traits(synthesized=False, clarified=True, decomposed=True, gap_filled=True)
    assert synthesized.to_priority_tuple() > everything_else.to_priority_tuple()


# --------------------------------------------------------------------------- #
# Summary timing                                                               #
# --------------------------------------------------------------------------- #

async def test_synthesis_waits_for_a_late_summary():
    """The synthesis branch is created before the history summary lands."""
    rag = two_part_rag(summary_delay_s=0.15)
    orch = RefactoredOrchestrator(rag)

    answer, _ = await orch.process_query(ORIGINAL, "u7")

    assert branch_types(orch).count("synthesized") == 1
    assert len(rag.calls["answer"]) == 1
    assert rag.calls["answer"][0][0] == ORIGINAL
    assert answer == f"validated: answer to: {ORIGINAL}"


async def test_synthesis_still_answers_when_the_summary_task_fails():
    rag = two_part_rag(summary_fails=True)
    orch = RefactoredOrchestrator(rag)

    answer, _ = await orch.process_query(ORIGINAL, "u8")

    assert orch.summary_result is None
    assert branch_types(orch).count("synthesized") == 1
    assert len(rag.calls["answer"]) == 1
    assert answer == f"validated: answer to: {ORIGINAL}"
