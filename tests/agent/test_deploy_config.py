from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Final

import httpx
import pytest
from starlette.requests import Request

PROJECT_ID: Final = "00000000-0000-4000-8000-000000000fee"


def test_deploy_config_pins_runtime_and_privacy_dependencies() -> None:
    config = json.loads(Path("langgraph.json").read_text())

    assert config["api_version"] == "0.12.6"
    assert config["store"]["index"] == {
        "embed": "openai:text-embedding-3-small",
        "dims": 1536,
        "fields": ["$"],
    }
    assert config["auth"]["path"] == "./healthcare_rag/agent/auth.py:auth"
    assert config["http"]["app"] == "./healthcare_rag/agent/http_app.py:app"
    assert config["http"]["disable_mcp"] is True
    assert config["http"]["disable_a2a"] is True
    docker = "\n".join(config["dockerfile_lines"])
    assert "presidio-analyzer==2.2.364" in docker
    assert "spacy==3.8.15" in docker
    assert "en_core_web_sm-3.8.0" in docker


def test_example_environment_declares_deployment_and_feedback_projects() -> None:
    values = {
        line.split("=", 1)[0]
        for line in Path(".env.example").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert "LANGGRAPH_DEPLOYMENT_URL" in values
    assert "LANGSMITH_FEEDBACK_PROJECT_ID" in values


@pytest.mark.parametrize("value", [None, "", "not-a-uuid"])
def test_feedback_startup_validation_names_missing_or_invalid_var(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    from healthcare_rag.agent.http_app import (
        FeedbackProjectConfigurationError,
        validate_feedback_project,
    )

    if value is None:
        monkeypatch.delenv("LANGSMITH_FEEDBACK_PROJECT_ID", raising=False)
    else:
        monkeypatch.setenv("LANGSMITH_FEEDBACK_PROJECT_ID", value)

    with pytest.raises(
        FeedbackProjectConfigurationError, match="LANGSMITH_FEEDBACK_PROJECT_ID"
    ):
        validate_feedback_project(lambda _project_id: None)


def test_feedback_startup_validation_probes_dedicated_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from healthcare_rag.agent.http_app import validate_feedback_project

    calls: list[dict[str, list[str]]] = []
    monkeypatch.setenv("LANGSMITH_FEEDBACK_PROJECT_ID", PROJECT_ID)

    def probe(project_id: str) -> None:
        calls.append({"feedback_key": ["member_feedback"], "session": [project_id]})

    assert validate_feedback_project(probe) == PROJECT_ID
    assert calls == [{"feedback_key": ["member_feedback"], "session": [PROJECT_ID]}]


def test_feedback_startup_validation_skips_probe_without_platform_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No LANGSMITH_API_KEY means no client to probe with; shape check still runs."""
    from healthcare_rag.agent import http_app

    monkeypatch.setenv("LANGSMITH_FEEDBACK_PROJECT_ID", PROJECT_ID)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("built a LangSmith client without a platform key")

    monkeypatch.setattr(http_app, "Client", _forbidden)

    assert http_app.validate_feedback_project() == PROJECT_ID


def test_feedback_startup_validation_still_fails_closed_with_platform_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a credential configured the probe runs and a bad project is fatal."""
    from langsmith.utils import LangSmithError

    from healthcare_rag.agent import http_app

    monkeypatch.setenv("LANGSMITH_FEEDBACK_PROJECT_ID", PROJECT_ID)
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
    calls: list[dict[str, list[str]]] = []

    class _RejectingClient:
        def list_feedback(self, **kwargs: list[str]) -> object:
            calls.append(kwargs)
            raise LangSmithError("403 Forbidden")

    monkeypatch.setattr(http_app, "Client", _RejectingClient)

    with pytest.raises(
        http_app.FeedbackProjectConfigurationError,
        match="project existence probe failed",
    ):
        _ = http_app.validate_feedback_project()
    assert calls == [{"feedback_key": ["member_feedback"], "session": [PROJECT_ID]}]


@pytest.mark.anyio
async def test_internal_version_route_requires_internal_identity_and_role() -> None:
    from healthcare_rag.agent.http_app import internal_version

    async def invoke(identity: str, role: str | None) -> int:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/coach/internal/version",
                "headers": [],
                "user": SimpleNamespace(
                    identity=identity, get=lambda key: role if key == "role" else None
                ),
            }
        )
        return (await internal_version(request)).status_code

    assert await invoke("member-a", "member") == 403
    assert await invoke("internal", None) == 403
    assert await invoke("internal", "internal") == 200


def test_composed_version_route_requires_both_internal_credentials(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    from langgraph_api import __version__

    denied_headers = (
        {},
        member_headers,
        {"x-api-key": "platform-secret"},
        {"x-internal-token": "internal-secret"},
    )
    for headers in denied_headers:
        response = httpx.get(f"{agent_server}/coach/internal/version", headers=headers)
        assert response.status_code in {401, 403}

    accepted = httpx.get(
        f"{agent_server}/coach/internal/version",
        headers={
            "x-api-key": "platform-secret",
            "x-internal-token": "internal-secret",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"version": __version__}


def _smoke_environment() -> dict[str, str]:
    return {
        "LANGGRAPH_DEPLOYMENT_URL": "https://coach.example.test",
        "LANGGRAPH_U1_TOKEN": "u1-token",
        "LANGGRAPH_U2_TOKEN": "u2-token",
        "LANGSMITH_API_KEY": "platform-key",
        "COACH_INTERNAL_TOKEN": "internal-token",
        "LANGSMITH_FEEDBACK_PROJECT_ID": PROJECT_ID,
    }


def test_smoke_settings_reject_local_dev_and_missing_environment() -> None:
    from scripts.deployed_smoke import SmokeConfigurationError, SmokeSettings

    with pytest.raises(SmokeConfigurationError, match="deployed HTTPS"):
        SmokeSettings.from_environment(
            _smoke_environment() | {"LANGGRAPH_DEPLOYMENT_URL": "http://127.0.0.1:2024"}
        )
    with pytest.raises(SmokeConfigurationError, match="member credentials"):
        SmokeSettings.from_environment(
            {
                key: value
                for key, value in _smoke_environment().items()
                if key != "LANGGRAPH_U2_TOKEN"
            }
        )


def test_smoke_settings_use_static_tokens_when_provided() -> None:
    from scripts.deployed_smoke import SmokeSettings

    settings = SmokeSettings.from_environment(_smoke_environment())
    assert settings.u1_token == "u1-token"
    assert settings.u2_token == "u2-token"


def test_smoke_settings_mint_tokens_when_static_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.deployed_smoke import SmokeSettings

    import scripts.deployed_smoke as smoke

    minted: list[str] = []

    def fake_mint(supabase_url: str, service_key: str, email: str) -> str:
        minted.append(email)
        return f"minted-for-{email}"

    monkeypatch.setattr(smoke, "_mint_member_token", fake_mint)
    environment = {
        key: value
        for key, value in _smoke_environment().items()
        if not key.startswith("LANGGRAPH_U")
    } | {"SUPABASE_URL": "https://supabase.example.test", "SUPABASE_SERVICE_KEY": "sk"}
    settings = SmokeSettings.from_environment(environment)
    assert settings.u1_token == "minted-for-hcrag.smoke.u1@example.com"
    assert settings.u2_token == "minted-for-hcrag.smoke.u2@example.com"
    assert minted == [
        "hcrag.smoke.u1@example.com",
        "hcrag.smoke.u2@example.com",
    ]


def test_smoke_settings_fail_closed_when_minting_fails_without_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.deployed_smoke import SmokeConfigurationError, SmokeSettings

    import scripts.deployed_smoke as smoke

    def failing_mint(supabase_url: str, service_key: str, email: str) -> str:
        raise RuntimeError("supabase down")

    monkeypatch.setattr(smoke, "_mint_member_token", failing_mint)
    environment = {
        key: value
        for key, value in _smoke_environment().items()
        if not key.startswith("LANGGRAPH_U")
    } | {"SUPABASE_URL": "https://supabase.example.test", "SUPABASE_SERVICE_KEY": "sk"}
    with pytest.raises(SmokeConfigurationError, match="minting failed"):
        SmokeSettings.from_environment(environment)


@pytest.mark.anyio
async def test_smoke_version_gate_is_first_and_stops_mismatch() -> None:
    from scripts.deployed_smoke import DeployedSmoke, SmokeFailure, SmokeSettings

    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if (
            "x-api-key" not in request.headers
            or "x-internal-token" not in request.headers
        ):
            return httpx.Response(401, json={"detail": "Unauthorized"})
        assert request.headers["x-api-key"] == "platform-key"
        assert request.headers["x-internal-token"] == "internal-token"
        return httpx.Response(200, json={"version": "0.8.6"})

    settings = SmokeSettings.from_environment(_smoke_environment())
    async with httpx.AsyncClient(
        base_url=settings.url, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(SmokeFailure, match="0.12.6"):
            await DeployedSmoke(settings, client).run()

    assert requests == ["/coach/internal/version"] * 5


@pytest.mark.anyio
async def test_smoke_orchestrates_all_ten_checks_after_version_gate() -> None:
    from scripts.deployed_smoke import DeployedSmoke, SmokeSettings

    events: list[str] = []

    class FixtureSmoke(DeployedSmoke):
        async def verify_version_gate(self) -> None:
            events.append("version")

        async def check_memory(self) -> None:
            events.append("1")

        async def check_isolation(self) -> None:
            events.append("2")

        async def check_interrupts(self) -> None:
            events.append("3")

        async def check_projection(self) -> None:
            events.append("4")

        async def check_perimeter(self) -> None:
            events.append("5")

        async def check_route_a(self) -> None:
            events.append("6")

        async def check_erasure(self) -> None:
            events.append("7")

        async def check_disabled_protocols(self) -> None:
            events.append("8")

        async def check_reminders(self) -> None:
            events.append("9")

        async def check_documents_and_feedback(self) -> None:
            events.append("10")

    settings = SmokeSettings.from_environment(_smoke_environment())
    async with httpx.AsyncClient(base_url=settings.url) as client:
        await FixtureSmoke(settings, client).run()

    assert events == ["version", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
