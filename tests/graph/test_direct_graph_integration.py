from __future__ import annotations

from dataclasses import replace

import pytest

from healthcare_rag.graph.engine import GraphEngine
from healthcare_rag.graph.engine_record import ResultContext, TurnTiming, build_result
from healthcare_rag.graph.llm import QueryOrRespondDecision
from healthcare_rag.graph.resources import get
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.models.safety import SafetyAssessment
from healthcare_rag.monitor import QueryMonitor
from healthcare_rag.processors.social_responses import social_response

from .conftest import FakeGateway, ResourceInstaller


def test_direct_result_projects_only_direct_channels() -> None:
    state = {
        "answer": "Hello.",
        "direct_response": "Hello.",
        "response_action": "direct",
        "follow_ups": [],
        "merged": QueryResultList(
            results=[
                QueryResult(
                    source="Lipitor",
                    query="stale",
                    docs=[
                        QueryDocument(
                            content="stale context",
                            score=0.1,
                            doc_id="stale",
                            source_name="Lipitor",
                        )
                    ],
                )
            ]
        ).model_dump(mode="json"),
        "branch_events": [{"branch": "stale", "status": "SUCCEEDED"}],
        "selected_branch_type": "stale",
        "selected_branch_query": "stale query",
    }
    context = ResultContext(TurnTiming(0.0, 0.1, 0.2, 0.3), get().settings, None)

    result, _used_history = build_result(state, [], context)

    assert result["answer"] == "Hello."
    assert result["raw_answer"] == "Hello."
    assert result["response_action"] == "direct"
    assert result["contexts"] == []
    assert result["n_branches"] == 0
    assert result["selected_branch_type"] is None
    assert result["selected_branch_query"] is None


def test_refusal_result_suppresses_conflicting_direct_and_medical_channels() -> None:
    state = {
        "answer": "DIRECT_MUST_NOT_PROJECT",
        "safety_response": "REFUSAL_WINS",
        "direct_response": "DIRECT_MUST_NOT_PROJECT",
        "response_action": "direct",
        "query_router": {"effective_action": "direct"},
        "validated": "MEDICAL_MUST_NOT_PROJECT",
        "follow_ups": ["STALE_FOLLOWUP"],
        "merged": QueryResultList(
            results=[
                QueryResult(
                    source="Lipitor",
                    query="stale",
                    docs=[
                        QueryDocument(
                            content="stale context",
                            score=0.1,
                            doc_id="stale",
                            source_name="Lipitor",
                        )
                    ],
                )
            ]
        ).model_dump(mode="json"),
        "branch_events": [{"branch": "stale", "status": "SUCCEEDED"}],
        "selected_branch_type": "stale",
        "selected_branch_query": "stale query",
    }
    context = ResultContext(TurnTiming(0.0, 0.1, 0.2, 0.3), get().settings, None)

    result, _used_history = build_result(state, [], context)

    assert result["answer"] == "REFUSAL_WINS"
    assert result["raw_answer"] is None
    assert "response_action" not in result
    assert result["query_router"] is None
    assert result["follow_ups"] == []
    assert result["contexts"] == []
    assert result["n_branches"] == 0
    assert result["selected_branch_type"] is None
    assert result["selected_branch_query"] is None


async def test_compiled_graph_refusal_clears_conflicting_direct_checkpoint_state(
    install_resources: ResourceInstaller,
) -> None:
    resources = install_resources(FakeGateway())
    engine = GraphEngine(resources.settings)
    await engine.__aenter__()
    config = {"configurable": {"thread_id": "conflicting-safety-direct"}}
    resume_config = await engine.compiled.aupdate_state(
        config,
        {
            "scrubbed_question": "Should I change treatment?",
            "safety_response": "REFUSAL_WINS",
            "direct_response": "DIRECT_MUST_NOT_PERSIST",
            "response_action": "direct",
            "query_router": {"effective_action": "direct"},
            "validated": "MEDICAL_MUST_NOT_PERSIST",
            "follow_ups": ["STALE_FOLLOWUP"],
            "branch_events": [{"branch": "stale", "status": "SUCCEEDED"}],
            "selected_branch_type": "stale",
            "selected_branch_query": "stale query",
        },
        as_node="generate_follow_ups",
    )

    output = await engine.compiled.ainvoke(None, resume_config)
    snapshot = await engine.compiled.aget_state(config)

    assert output["selected_branch_type"] is None
    assert output["selected_branch_query"] is None
    assert snapshot.values["answer"] == "REFUSAL_WINS"
    assert snapshot.values["direct_response"] is None
    assert snapshot.values["response_action"] is None
    assert snapshot.values["query_router"] is None
    assert snapshot.values["selected_branch_type"] is None
    assert snapshot.values["selected_branch_query"] is None
    assert snapshot.values["follow_ups"] == []
    assert [message.content for message in snapshot.values["messages"]] == [
        "Should I change treatment?",
        "REFUSAL_WINS",
    ]
    await engine.aclose()


@pytest.mark.parametrize("arm", ["deterministic", "tool"])
async def test_direct_turn_completes_monitor_and_checkpoint_history(
    install_resources: ResourceInstaller,
    arm: str,
) -> None:
    assessment = SafetyAssessment(
        category="out_of_scope",
        benign_social=True,
        social_intent="greeting",
    )
    gateway = FakeGateway(
        structured_results={"safety_gate": assessment},
        query_decision=QueryOrRespondDecision(
            action="direct",
            direct_content="Hello from the router.",
            tool_query=None,
            fallback_reason=None,
            tool_call_count=0,
        ),
    )
    resources = install_resources(gateway)
    resources.settings = replace(resources.settings, query_response_arm=arm)
    engine = GraphEngine(resources.settings)
    monitor = QueryMonitor()

    result = await engine.run_turn(f"direct-{arm}", "Hello", monitor)
    snapshot = await engine.compiled.aget_state(
        {"configurable": {"thread_id": f"direct-{arm}"}}
    )

    expected = (
        social_response("greeting")
        if arm == "deterministic"
        else "Hello from the router."
    )
    assert result["answer"] == expected
    assert result["raw_answer"] == expected
    assert result["response_action"] == "direct"
    assert result["follow_ups"] == []
    assert result["contexts"] == []
    assert result["n_branches"] == 0
    assert monitor.raw_answer == expected
    assert monitor.final_answer == expected
    assert monitor.raw_answer_event.is_set()
    assert monitor.final_answer_event.is_set()
    assert [message.content for message in snapshot.values["messages"]] == [
        "Hello",
        expected,
    ]
    assert not any(call.get("method") == "route_tools" for call in gateway.calls)
    assert sum(call.get("method") == "query_or_respond" for call in gateway.calls) == (
        arm == "tool"
    )
    await engine.aclose()
