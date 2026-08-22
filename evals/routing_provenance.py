from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import ClassVar, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

Lane = Literal["query_response", "safety_classifier"]
LANE_ADAPTER: Final[TypeAdapter[Lane]] = TypeAdapter(Lane)


class ProvenanceModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ArtifactHashes(ProvenanceModel):
    code: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset: str = Field(pattern=r"^[0-9a-f]{64}$")
    multiturn: str = Field(pattern=r"^[0-9a-f]{64}$")
    prototypes: str = Field(pattern=r"^[0-9a-f]{64}$")
    thresholds: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluators: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompts: str = Field(pattern=r"^[0-9a-f]{64}$")
    uv_lock: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArmEnvironment(ProvenanceModel):
    HC_RAG_QUERY_RESPONSE_ARM: Literal["current", "deterministic", "tool"]
    HC_RAG_SAFETY_CLASSIFIER: Literal["llm", "semantic_router"]


class ExperimentRows(ProvenanceModel):
    local_row_count: int = Field(ge=1)
    local_row_ids: tuple[str, ...] = Field(min_length=1)
    langsmith_row_count: int = Field(ge=1)
    langsmith_row_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("local_row_ids", "langsmith_row_ids")
    @classmethod
    def canonicalize_row_ids(cls, row_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(row_ids))

    @model_validator(mode="after")
    def require_exact_remote_population(self) -> ExperimentRows:
        local = self.local_row_ids
        remote = self.langsmith_row_ids
        if len(set(local)) != len(local) or self.local_row_count != len(local):
            msg = "local row count and unique IDs must match"
            raise ValueError(msg)
        if len(set(remote)) != len(remote) or self.langsmith_row_count != len(remote):
            msg = "LangSmith row count and unique IDs must match"
            raise ValueError(msg)
        if local != remote:
            msg = "LangSmith row IDs must exactly match local row IDs"
            raise ValueError(msg)
        return self


class RoutingProvenance(ProvenanceModel):
    git_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    git_dirty: Literal[False]
    arm_env: ArmEnvironment
    rows: ExperimentRows
    experiment_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    experiment_url: str = Field(pattern=r"^https://[^\s]+$")
    hashes: ArtifactHashes
    semantic_router_version: str = Field(min_length=1)
    encoder_model: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    repetitions: int = Field(ge=1)
    concurrency: int = Field(ge=1)


class ProvenanceError(ValueError):
    pass


def sha256_paths(paths: Iterable[Path], *, root: Path) -> str:
    digest = hashlib.sha256()
    resolved_root = root.resolve()
    ordered = sorted(
        (path.resolve() for path in paths),
        key=lambda path: path.relative_to(resolved_root).as_posix(),
    )
    for path in ordered:
        relative = path.relative_to(resolved_root).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _check_lane(manifest: RoutingProvenance, lane: Lane) -> None:
    arm = manifest.arm_env
    valid_by_lane: dict[Lane, bool] = {
        "query_response": arm.HC_RAG_SAFETY_CLASSIFIER == "llm",
        "safety_classifier": arm.HC_RAG_QUERY_RESPONSE_ARM == "current",
    }
    valid = valid_by_lane[lane]
    if not valid:
        raise ProvenanceError(f"arm_env is mixed for {lane} lane")


def compare_manifests(manifests: Sequence[RoutingProvenance], *, lane: str) -> None:
    if len(manifests) < 2:
        raise ProvenanceError("at least two manifests are required")
    parsed_lane: Lane = LANE_ADAPTER.validate_python(lane)
    reference = manifests[0]
    for manifest in manifests:
        _check_lane(manifest, parsed_lane)
        if manifest.git_sha != reference.git_sha:
            raise ProvenanceError("git_sha mismatch between arms")
        if manifest.hashes != reference.hashes:
            raise ProvenanceError("hashes mismatch between arms")
        reference_rows = (
            frozenset(reference.rows.local_row_ids),
            frozenset(reference.rows.langsmith_row_ids),
        )
        manifest_rows = (
            frozenset(manifest.rows.local_row_ids),
            frozenset(manifest.rows.langsmith_row_ids),
        )
        if manifest_rows != reference_rows:
            raise ProvenanceError("row binding mismatch between arms")
        if (
            manifest.repetitions != reference.repetitions
            or manifest.concurrency != reference.concurrency
            or manifest.judge_model != reference.judge_model
            or manifest.semantic_router_version != reference.semantic_router_version
            or manifest.encoder_model != reference.encoder_model
        ):
            raise ProvenanceError("measurement settings mismatch between arms")
