from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from jinja2 import Template
from langchain_core.messages import HumanMessage, SystemMessage

from healthcare_rag.graph.prompts import PromptRegistry, get_registry
from healthcare_rag.graph.resources import Resources
from healthcare_rag.pipeline.medical_rag import MedicalRAG
from healthcare_rag.processors.base import PromptManager


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "legacy_renders"
FIXTURE_PATHS = tuple(sorted(FIXTURES_DIR.glob("*.json")))


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=lambda path: path.stem)
def test_registry_matches_frozen_legacy_render(fixture_path: Path) -> None:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    messages = PromptRegistry().format_messages(fixture["stage"], **fixture["vars"])

    actual = [
        {
            "role": "system" if isinstance(message, SystemMessage) else "user",
            "content": message.content,
        }
        for message in messages
    ]
    assert actual == fixture["messages"]


def test_registry_resolves_templates_independently_of_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/")

    messages = PromptRegistry().format_messages(
        "decompose_query",
        user_query="What's Lipitor's use?",
    )

    assert [type(message) for message in messages] == [SystemMessage, HumanMessage]


def test_registry_rejects_an_unknown_stage() -> None:
    with pytest.raises(KeyError):
        PromptRegistry().format_messages("unknown_stage")


def test_registry_rejects_an_unknown_message_role() -> None:
    registry = PromptRegistry()
    registry._templates["decompose_query"] = Template(
        "- role: assistant\n  content: invalid"
    )

    with pytest.raises(ValueError):
        registry.format_messages("decompose_query", user_query="ignored")


def test_graph_resources_lazily_resolve_the_prompt_registry() -> None:
    resources = Resources()

    assert resources.prompts is get_registry()


def test_medical_rag_default_prompt_path_renders_a_legacy_prompt() -> None:
    prompts_dir = inspect.signature(MedicalRAG).parameters["prompts_dir"].default
    prompt_manager = PromptManager(prompts_dir)

    messages = prompt_manager.messages(
        "safety_gate",
        user_query="What is Lipitor?",
        conversation_context="",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
