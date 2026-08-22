from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("PRIVACY_SERVICE_TOKEN", "test-token")

from privacy_service.engine import (
    ANALYZER_VERSION,
    ENTITY_TYPES,
    MODEL_VERSION,
    SPACY_VERSION,
)
from privacy_service.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_ok_is_open_and_detail_free(client: TestClient) -> None:
    response = client.get("/ok")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_requires_bearer(client: TestClient) -> None:
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_health_reports_pinned_contract(client: TestClient) -> None:
    body = client.get("/health", headers=AUTH).json()

    assert body["status"] == "ready"
    assert body["analyzer_version"] == ANALYZER_VERSION
    assert body["spacy_version"] == SPACY_VERSION
    assert body["model_version"] == MODEL_VERSION
    assert set(ENTITY_TYPES) <= set(body["entities"])


def test_analyze_returns_presidio_spans(client: TestClient) -> None:
    text = "Alice Johnson used 192.168.10.42."

    body = client.post("/analyze", json={"text": text}, headers=AUTH).json()

    kinds = {span["entity_type"] for span in body["results"]}
    assert {"PERSON", "IP_ADDRESS"} <= kinds
    for span in body["results"]:
        assert 0 <= span["start"] < span["end"] <= len(text)
        assert 0.0 <= span["score"] <= 1.0


def test_analyze_requires_bearer(client: TestClient) -> None:
    assert client.post("/analyze", json={"text": "hi"}).status_code == 401


def test_analyze_rejects_oversized_and_unknown_entities(client: TestClient) -> None:
    oversized = "x" * (16 * 1024 + 1)

    too_large = client.post("/analyze", json={"text": oversized}, headers=AUTH)
    unknown = client.post("/analyze", json={"text": "hi", "entities": ["NOPE"]}, headers=AUTH)

    assert too_large.status_code in (413, 422)
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == "PRIVACY_UNKNOWN_ENTITY"
