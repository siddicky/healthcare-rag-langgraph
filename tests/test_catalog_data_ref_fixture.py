from __future__ import annotations

import json
from pathlib import Path
from typing import final

import pytest
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from healthcare_rag.agent.compose_ui import DataRef


@final
class FixtureCase(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    value: JsonValue
    accepted: bool


CASES = TypeAdapter(list[FixtureCase]).validate_python(
    json.loads(Path("tests/fixtures/catalog_data_refs.json").read_text())
)


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[case.id for case in CASES],
)
def test_backend_data_ref_acceptance_matches_shared_fixture(
    case: FixtureCase,
) -> None:
    # Given
    expected = case.accepted

    # When
    try:
        _ = DataRef.model_validate(case.value)
        accepted = True
    except ValidationError:
        accepted = False

    # Then
    assert accepted is expected
