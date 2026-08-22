from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from healthcare_rag.graph.nodes import query_or_respond
from healthcare_rag.processors.social_responses import social_response

from .query_or_respond_fakes import _install, _state


@pytest.mark.parametrize(
    "content",
    [
        "Metformin treats diabetes.",
        "Lipitor lowers cholesterol.",
        "Metformin is used for diabetes.",
        "Lipitor can cause muscle pain.",
        "Hello. Metformin treats diabetes.",
        "I can explain information about how Metformin treats diabetes.",
        "Diabetes is a chronic condition.",
        "Statins lower LDL cholesterol.",
        "This medicine treats high blood sugar.",
        "Aspirin relieves pain.",
        "Hello! Diabetes is a chronic condition.",
        "Hypertension damages blood vessels.",
        "Antibiotics treat bacterial infections.",
        "Thanks! Insulin lowers blood sugar.",
        "I can explain diabetes causes blindness.",
        "Happy to answer diabetes is a chronic condition.",
        "Feel free to ask metformin treats diabetes.",
        "I can answer insulin lowers glucose.",
        "Happy to answer atorvastatin uses metformin.",
        "Feel free to ask metformin uses atorvastatin.",
        "I can discuss lipitor interactions metformin.",
        "We could help with metformin uses atorvastatin.",
        "I'm happy to answer metformin treats diabetes.",
        "I'd be glad to discuss atorvastatin uses metformin.",
        "I can provide information about metformin treats diabetes.",
        "I'm here to help with metformin uses atorvastatin.",
        "I'm happy to help, metformin treats diabetes.",
        "We're happy to help — atorvastatin uses metformin.",
        "Medical prose must be discarded.",
        "Safe social response.",
        "That pillbox looks useful.",
        "The tabletops are clean.",
        "This is milligrammatical wordplay.",
        "Hello, metformin treats diabetes.",
        "Thanks, take metformin.",
        "Happy to help, metformin treats diabetes.",
        "Hello, thanks, metformin treats diabetes.",
        "Hello,take care.",
        "Hello, .",
        "Hello: take care.",
        "Hello; take care.",
        "Hello — take care.",
    ],
)
async def test_social_factual_medical_prose_uses_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    # Given: a benign-social turn whose model output contains a medical claim.
    _, model = _install(monkeypatch, AIMessage(content=content))

    # When: the real node maps the model decision onto public state channels.
    update = await query_or_respond.generate_query_or_respond(
        _state(benign_social=True)
    )

    # Then: the node exposes only the deterministic greeting and denial telemetry.
    assert update.get("direct_response") == social_response("greeting")
    assert update.get("response_action") == "direct"
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": "direct",
        "effective_action": "direct",
        "fallback": True,
        "error": True,
        "fallback_reason": "clinical_direct_content",
        "tool_call_count": 0,
    }
    assert content not in repr(update)
    assert "messages" not in update
    assert model.bound.messages[-1].content == "Hello"


@pytest.mark.parametrize(
    ("intent", "content"),
    [
        ("greeting", "Hello there."),
        ("thanks", "Happy to help."),
        ("goodbye", "Goodbye."),
        ("capability", "I can help with dosing in general from the monographs."),
        ("greeting", "Do not hesitate to ask another question."),
        ("capability", "You can ask about the monographs."),
        ("thanks", "Never mind, thanks."),
        ("greeting", "Consider asking another question."),
        ("capability", "I can discuss Lipitor and metformin monographs."),
        (
            "capability",
            "I can answer questions about Lipitor and metformin product monographs.",
        ),
        ("capability", "Happy to answer questions about Lipitor monographs."),
        ("capability", "Feel free to ask me about Metformin monographs."),
        (
            "capability",
            "Glad to help with questions about the product monographs.",
        ),
        ("capability", "Ask me anything about the Lipitor monograph."),
        ("capability", "I can answer questions about the Lipitor monograph."),
        ("capability", "Feel free to ask another question."),
        (
            "capability",
            "I am able to discuss any question about product monographs.",
        ),
        (
            "capability",
            "We could help with your questions on metformin interactions.",
        ),
        (
            "capability",
            "I'm happy to answer questions about metformin interactions.",
        ),
        ("capability", "I'd be glad to discuss the Lipitor product monograph."),
        ("capability", "I can provide information about the metformin monograph."),
        (
            "capability",
            "I'm here to help with questions grounded in the monographs.",
        ),
        (
            "capability",
            "We're glad to help with questions about Lipitor interactions.",
        ),
        (
            "capability",
            "We'd be happy to provide information from the product monographs.",
        ),
        (
            "capability",
            "I can discuss atorvastatin warnings from the monograph.",
        ),
        (
            "capability",
            "I can answer questions about metformin interactions from the monograph.",
        ),
        (
            "capability",
            "We're here to provide information about metformin side effects from the monograph.",
        ),
        (
            "capability",
            "I'm happy to help, with questions about metformin interactions.",
        ),
        (
            "capability",
            "We're happy to help — with questions about Lipitor warnings.",
        ),
        (
            "capability",
            "I'd be glad to assist, with questions on metformin warnings.",
        ),
        (
            "capability",
            "We're here to help — with questions about Lipitor interactions.",
        ),
        ("goodbye", "Goodbye, take care!"),
        ("thanks", "Thanks, happy to help."),
        ("greeting", "Hello, happy to help."),
        ("goodbye", "Bye for now, thanks again."),
        (
            "greeting",
            "Hello, I'm happy to help, with questions about metformin interactions.",
        ),
        (
            "thanks",
            "Thanks, we're here to provide information about metformin side effects from the monograph.",
        ),
    ],
)
async def test_social_direct_content_preserves_allowed_intents(
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    content: str,
) -> None:
    _install(monkeypatch, AIMessage(content=content))
    state = _state(benign_social=True)
    state["safety"] = {
        "category": "out_of_scope",
        "benign_social": True,
        "social_intent": intent,
    }

    update = await query_or_respond.generate_query_or_respond(state)

    assert update.get("direct_response") == content
    telemetry = update.get("query_router")
    assert isinstance(telemetry, dict)
    assert telemetry.get("fallback") is False
    assert telemetry.get("error") is False
