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
