from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import TypeAdapter

from evals.routing_dataset import (
    DataContractError,
    dataset_name,
    load_bundle,
    sync_dataset,
    to_langsmith_examples,
)
from evals.routing_dataset_models import (
    ExampleJson,
    Prototype,
    RemoteDataset,
    RemoteExample,
    RoutingConversation,
    RoutingRow,
    RowStratum,
    SdkExample,
)

EXPECTED_PROTOTYPES = 60
EXPECTED_ROWS = 120
EXPECTED_CONVERSATIONS = 18
EXPECTED_HASH = "8c9f99db620d6fe817966609b937bd69c61329ebac01a981885932676c84b4be"
EXPECTED_SPLITS = Counter({"calibration": 40, "core": 40, "holdout": 40})
EXPECTED_ROW_STRATA = Counter(
    {
        "benign_social": 6,
        "in_scope_medical": 6,
        "mixed_social_medical": 4,
        "ambiguous_clinical": 4,
        "out_of_scope": 4,
        "personal_advice": 4,
        "emergency": 4,
        "prompt_injection": 4,
        "pii_recall": 4,
    }
)
EXPECTED_THREAD_STRATA = {
    "benign_social_thread",
    "social_to_medical",
    "medical_to_social",
    "safety_escalation",
    "pii_reask",
    "injection_pressure",
}


def _load_mutation(
    tmp_path: Path,
    prototypes: list[Prototype],
    rows: list[RoutingRow],
    conversations: list[RoutingConversation],
):
    paths = tuple(tmp_path / name for name in ("p.json", "r.json", "m.json"))
    _ = paths[0].write_bytes(
        TypeAdapter(list[Prototype]).dump_json(prototypes, indent=2)
    )
    _ = paths[1].write_bytes(TypeAdapter(list[RoutingRow]).dump_json(rows, indent=2))
    _ = paths[2].write_bytes(
        TypeAdapter(list[RoutingConversation]).dump_json(conversations, indent=2)
    )
    return load_bundle(*paths)


def _mutable_bundle() -> tuple[
    list[Prototype], list[RoutingRow], list[RoutingConversation]
]:
    bundle = load_bundle()
    return list(bundle.prototypes), list(bundle.rows), list(bundle.conversations)


def test_contract_when_loading_authored_artifacts() -> None:
    # Given: the frozen routing artifacts.
    # When: their typed boundary loader runs.
    bundle = load_bundle()
    # Then: authored cardinalities and split strata match fixed contract constants.
    assert len(bundle.prototypes) == EXPECTED_PROTOTYPES
    assert len(bundle.rows) == EXPECTED_ROWS
    assert len(bundle.conversations) == EXPECTED_CONVERSATIONS
    assert Counter(row.split.value for row in bundle.rows) == EXPECTED_SPLITS
    for split in EXPECTED_SPLITS:
        assert (
            Counter(
                row.stratum.value for row in bundle.rows if row.split.value == split
            )
            == EXPECTED_ROW_STRATA
        )
    assert {row.expected_safety_category.value for row in bundle.rows} == {
        "in_scope_informational",
        "personal_medical_advice",
        "emergency_red_flag",
        "out_of_scope",
        "prompt_injection",
        "ambiguous",
    }
    assert {prototype.category.value for prototype in bundle.prototypes} == {
        "in_scope_informational",
        "personal_medical_advice",
        "emergency_red_flag",
        "out_of_scope",
        "prompt_injection",
    }


def test_multiturn_contract_when_loading_authored_artifact() -> None:
    # Given: the frozen scripted conversation artifact.
    # When: its typed boundary loader runs.
    conversations = load_bundle().conversations
    # Then: every stratum has two core and one holdout conversations.
    assert {
        conversation.stratum.value for conversation in conversations
    } == EXPECTED_THREAD_STRATA
    for stratum in EXPECTED_THREAD_STRATA:
        assert Counter(
            conversation.split.value
            for conversation in conversations
            if conversation.stratum.value == stratum
        ) == Counter({"core": 2, "holdout": 1})
    assert {conversation.kind for conversation in conversations} == {"scripted"}


def test_hash_and_conversion_when_round_tripped() -> None:
    # Given: one independently validated bundle and its LangSmith conversion.
    bundle = load_bundle()
    examples = to_langsmith_examples(bundle.rows)
    encoded = json.dumps([example.as_json() for example in examples], sort_keys=True)
    # When: conversion JSON is decoded and the bundle is loaded a second time.
    decoded = TypeAdapter(list[ExampleJson]).validate_json(encoded)
    second = load_bundle()
    # Then: stable IDs, hash, and immutable dataset name survive the round trip.
    assert len({example.id for example in examples}) == EXPECTED_ROWS
    assert len(decoded) == EXPECTED_ROWS
    assert second.content_hash == bundle.content_hash == EXPECTED_HASH
    assert dataset_name(bundle) == f"healthcare-rag-routing-{bundle.content_hash[:12]}"


def test_candidate_output_fields_when_inspecting_authored_sources() -> None:
    # Given: the three pre-candidate evidence artifacts.
    sources = [path.read_text() for path in Path("evals").glob("routing_*.json")]
    # When: field names are inspected as JSON data.
    forbidden_keys = (
        "candidate_output",
        "candidate_response",
        "actual_output",
        "model_output",
    )
    # Then: no artifact contains a candidate-produced output field.
    assert all(f'"{key}"' not in source for source in sources for key in forbidden_keys)


def test_duplicate_id_when_boundary_parses_fixture(tmp_path: Path) -> None:
    # Given: a routing fixture with one duplicate stable ID.
    prototypes, rows, conversations = _mutable_bundle()
    rows[1] = rows[1].model_copy(update={"id": rows[0].id})
    # When/Then: parsing fails with the stable duplicate-ID boundary message.
    with pytest.raises(DataContractError, match=r"^duplicate id: routing-"):
        _ = _load_mutation(tmp_path, prototypes, rows, conversations)


def test_schema_type_when_boundary_parses_fixture(tmp_path: Path) -> None:
    # Given: a temporary routing artifact with an unknown action literal.
    prototype_path, routing_path, multiturn_path = (
        tmp_path / "p.json",
        tmp_path / "r.json",
        tmp_path / "m.json",
    )
    _ = prototype_path.write_bytes(Path("evals/routing_prototypes.json").read_bytes())
    routing_source = Path("evals/routing_dataset.json").read_text()
    _ = routing_path.write_text(
        routing_source.replace(
            '"expected_action": "direct"', '"expected_action": "explode"', 1
        )
    )
    _ = multiturn_path.write_bytes(
        Path("evals/routing_multiturn_dataset.json").read_bytes()
    )
    # When/Then: typed parsing rejects the malformed literal at the file boundary.
    with pytest.raises(DataContractError, match=r"^invalid r.json:"):
        _ = load_bundle(prototype_path, routing_path, multiturn_path)


def test_wrong_cardinality_when_boundary_parses_fixture(tmp_path: Path) -> None:
    # Given: a routing fixture missing one row.
    prototypes, rows, conversations = _mutable_bundle()
    _ = rows.pop()
    # When/Then: parsing fails before downstream evaluation.
    with pytest.raises(
        DataContractError, match=r"^routing rows: expected 120, got 119$"
    ):
        _ = _load_mutation(tmp_path, prototypes, rows, conversations)


def test_near_duplicate_when_holdout_paraphrases_prototype(tmp_path: Path) -> None:
    # Given: a holdout question copied from a semantic prototype.
    prototypes, rows, conversations = _mutable_bundle()
    holdout_index = next(
        index for index, row in enumerate(rows) if row.split.value == "holdout"
    )
    rows[holdout_index] = rows[holdout_index].model_copy(
        update={"question": prototypes[0].text}
    )
    # When/Then: cross-population similarity at or above 0.85 is rejected.
    with pytest.raises(
        DataContractError, match=r"^near-duplicate text across populations:"
    ):
        _ = _load_mutation(tmp_path, prototypes, rows, conversations)


def test_missing_safety_holdout_when_stratum_is_removed(tmp_path: Path) -> None:
    # Given: one emergency holdout is mislabeled as benign social.
    prototypes, rows, conversations = _mutable_bundle()
    target_index = next(
        index
        for index, row in enumerate(rows)
        if row.split.value == "holdout" and row.stratum.value == "emergency"
    )
    rows[target_index] = rows[target_index].model_copy(
        update={"stratum": RowStratum.BENIGN_SOCIAL}
    )
    # When/Then: the critical holdout cardinality fails explicitly.
    with pytest.raises(
        DataContractError, match=r"^holdout stratum emergency: expected 4, got 3$"
    ):
        _ = _load_mutation(tmp_path, prototypes, rows, conversations)


@dataclass(frozen=True, slots=True)
class _FakeRecord:
    id: UUID
    name: str = "remote"


class FakeClient:
    remote_ids: list[UUID]
    dataset: RemoteDataset

    def __init__(self, remote_ids: list[UUID]) -> None:
        self.remote_ids = remote_ids
        self.dataset = _FakeRecord(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

    def has_dataset(self, *, dataset_name: str) -> bool:
        _ = dataset_name
        return True

    def read_dataset(self, *, dataset_name: str) -> RemoteDataset:
        _ = dataset_name
        return self.dataset

    def create_dataset(self, *, dataset_name: str, description: str) -> RemoteDataset:
        _ = dataset_name, description
        return self.dataset

    def list_examples(self, *, dataset_id: UUID) -> list[RemoteExample]:
        _ = dataset_id
        return [_FakeRecord(value) for value in self.remote_ids]

    def create_examples(self, *, dataset_id: UUID, examples: list[SdkExample]) -> None:
        _ = dataset_id
        self.remote_ids.extend(example["id"] for example in examples)

    def update_examples(self, *, dataset_id: UUID, updates: list[SdkExample]) -> None:
        _ = dataset_id, updates


def test_stale_remote_id_when_syncing_existing_dataset() -> None:
    # Given: a remote dataset retaining one stale UUID in addition to all local examples.
    bundle = load_bundle()
    local_ids = [example.id for example in to_langsmith_examples(bundle.rows)]
    client = FakeClient([*local_ids, UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")])
    # When/Then: sync refuses to silently retain the stale row.
    with pytest.raises(
        DataContractError,
        match=r"^remote dataset ID multiset differs: extra=1 missing=0$",
    ):
        _ = sync_dataset(client, bundle=bundle)


def test_exact_remote_ids_when_syncing_existing_dataset() -> None:
    # Given: an existing remote dataset with exactly the local deterministic IDs.
    bundle = load_bundle()
    local_ids = [example.id for example in to_langsmith_examples(bundle.rows)]
    client = FakeClient(local_ids.copy())
    # When: immutable sync updates the matching rows.
    result = sync_dataset(client, bundle=bundle)
    # Then: no row is created or retained outside the exact local ID multiset.
    assert (result.created, result.updated) == (0, 120)
    assert Counter(client.remote_ids) == Counter(local_ids)


def test_cli_when_validating_real_artifacts() -> None:
    # Given: the public offline validation surface.
    # When: the real module CLI validates all artifacts.
    completed = subprocess.run(
        [sys.executable, "-m", "evals.routing_dataset", "--validate"],
        check=False,
        capture_output=True,
        text=True,
    )
    # Then: it exits successfully and reports only counts plus immutable identity.
    assert completed.returncode == 0, completed.stderr
    expected = (
        '{"counts": {"conversations": 18, "prototypes": 60, "rows": 120}, '
        '"dataset_name": "healthcare-rag-routing-8c9f99db620d", '
        '"sha256": "8c9f99db620d6fe817966609b937bd69c61329ebac01a981885932676c84b4be"}\n'
    )
    assert completed.stdout == expected
