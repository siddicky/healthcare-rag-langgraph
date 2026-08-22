from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from evals.routing_provenance import (
    ArmEnvironment,
    ArtifactHashes,
    ExperimentRows,
    ProvenanceError,
    RoutingProvenance,
    compare_manifests,
    sha256_paths,
)
from evals.seal_clean import is_clean_status


def _hashes(seed: str = "a") -> ArtifactHashes:
    digest = seed * 64
    return ArtifactHashes(
        code=digest,
        dataset=digest,
        multiturn=digest,
        prototypes=digest,
        thresholds=digest,
        evaluators=digest,
        prompts=digest,
        uv_lock=digest,
    )


def _manifest(
    *,
    response_arm: Literal["current", "deterministic", "tool"] = "current",
    classifier: Literal["llm", "semantic_router"] = "llm",
    hashes: ArtifactHashes | None = None,
    local_ids: tuple[str, ...] = ("row-1", "row-2"),
    remote_ids: tuple[str, ...] = ("row-1", "row-2"),
    url: str = "https://smith.langchain.com/o/example/datasets/run",
) -> RoutingProvenance:
    return RoutingProvenance(
        git_sha="1" * 40,
        git_dirty=False,
        arm_env=ArmEnvironment(
            HC_RAG_QUERY_RESPONSE_ARM=response_arm,
            HC_RAG_SAFETY_CLASSIFIER=classifier,
        ),
        rows=ExperimentRows(
            local_row_count=2,
            local_row_ids=local_ids,
            langsmith_row_count=2,
            langsmith_row_ids=remote_ids,
        ),
        experiment_name=f"routing-{response_arm}-{classifier}",
        experiment_url=url,
        hashes=hashes or _hashes(),
        semantic_router_version="0.1.16",
        encoder_model="text-embedding-3-small",
        judge_model="gpt-5.4-mini",
        repetitions=2,
        concurrency=1,
    )


@pytest.mark.parametrize(
    "path",
    [".DS_Store", "Nymble Health Design System/token.json", "tmp/cache.json"],
)
def test_seal_when_protected_user_path_is_dirty_rejects(path: str) -> None:
    # Given / When
    clean = is_clean_status(f"?? {path}\n")

    # Then
    assert clean is False


def test_hash_paths_when_fixture_order_changes_is_deterministic(tmp_path: Path) -> None:
    # Given
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    _ = first.write_bytes(b"alpha")
    _ = second.write_bytes(b"beta")

    # When
    observed = sha256_paths((second, first), root=tmp_path)

    # Then
    import hashlib

    expected = hashlib.sha256(
        b"first.txt\0" + b"alpha" + b"\0second.txt\0" + b"beta" + b"\0"
    ).hexdigest()
    assert observed == expected
    assert sha256_paths((first, second), root=tmp_path) == expected


def test_manifest_comparison_when_sha_differs_rejects() -> None:
    # Given
    reference = _manifest()
    candidate = _manifest().model_copy(update={"git_sha": "2" * 40})

    # When / Then
    with pytest.raises(ProvenanceError, match="git_sha"):
        compare_manifests((reference, candidate), lane="query_response")


@pytest.mark.parametrize("artifact", ["prompts", "thresholds"])
def test_manifest_comparison_when_hash_differs_rejects(artifact: str) -> None:
    # Given
    reference = _manifest()
    hashes = _hashes().model_copy(update={artifact: "b" * 64})
    candidate = _manifest(hashes=hashes)

    # When / Then
    with pytest.raises(ProvenanceError, match="hashes"):
        compare_manifests((reference, candidate), lane="query_response")


def test_manifest_comparison_when_row_population_differs_rejects() -> None:
    # Given
    reference = _manifest()
    candidate = _manifest(local_ids=("row-1", "stale"), remote_ids=("row-1", "stale"))

    # When / Then
    with pytest.raises(ProvenanceError, match="row"):
        compare_manifests((reference, candidate), lane="query_response")


def test_manifest_when_langsmith_rows_are_stale_rejects() -> None:
    # Given / When / Then
    with pytest.raises(ValueError, match="LangSmith row IDs"):
        _ = _manifest(remote_ids=("row-1", "stale"))


def test_manifest_when_row_order_differs_canonicalizes_population() -> None:
    # Given
    ordered = _manifest()
    permuted = _manifest(local_ids=("row-2", "row-1"), remote_ids=("row-2", "row-1"))

    # When / Then
    compare_manifests((ordered, permuted), lane="query_response")
    assert ordered.rows == permuted.rows
    assert permuted.rows.local_row_ids == ("row-1", "row-2")


def test_manifest_when_experiment_url_is_missing_rejects() -> None:
    # Given / When / Then
    with pytest.raises(ValueError, match="experiment_url"):
        _ = _manifest(url="")


@pytest.mark.parametrize(
    ("lane", "reference", "candidate"),
    [
        (
            "query_response",
            _manifest(),
            _manifest(response_arm="tool", classifier="semantic_router"),
        ),
        (
            "safety_classifier",
            _manifest(),
            _manifest(response_arm="tool", classifier="semantic_router"),
        ),
    ],
)
def test_manifest_comparison_when_lane_settings_are_mixed_rejects(
    lane: Literal["query_response", "safety_classifier"],
    reference: RoutingProvenance,
    candidate: RoutingProvenance,
) -> None:
    # Given / When / Then
    with pytest.raises(ProvenanceError, match="arm_env"):
        compare_manifests((reference, candidate), lane=lane)
