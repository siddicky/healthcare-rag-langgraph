from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Final, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from evals.routing_dataset_models import (
    DataContractError,
    LangSmithExample,
    Prototype,
    RoutingBundle,
    RoutingConversation,
    RoutingDatasetClient,
    RoutingRow,
    SyncResult,
)
from evals.routing_dataset_validation import validate_bundle

__all__ = [
    "DataContractError",
    "dataset_name",
    "load_bundle",
    "sync_dataset",
    "to_langsmith_examples",
]

ROOT: Final = Path(__file__).parent
PROTOTYPES_PATH: Final = ROOT / "routing_prototypes.json"
ROUTING_PATH: Final = ROOT / "routing_dataset.json"
MULTITURN_PATH: Final = ROOT / "routing_multiturn_dataset.json"
NAMESPACE: Final = uuid.UUID("97a148f9-c452-4e21-8743-03bc58fa8a66")


T = TypeVar("T", bound=BaseModel)


def _parse(path: Path, model: type[T]) -> tuple[T, ...]:
    try:
        return tuple(TypeAdapter(list[model]).validate_json(path.read_bytes()))
    except (OSError, ValidationError) as exc:
        raise DataContractError(f"invalid {path.name}: {exc}") from exc


def load_bundle(
    prototypes_path: Path = PROTOTYPES_PATH,
    routing_path: Path = ROUTING_PATH,
    multiturn_path: Path = MULTITURN_PATH,
) -> RoutingBundle:
    prototypes = _parse(prototypes_path, Prototype)
    rows = _parse(routing_path, RoutingRow)
    conversations = _parse(multiturn_path, RoutingConversation)
    validate_bundle(prototypes, rows, conversations)
    canonical = json.dumps(
        {
            "prototypes": [item.model_dump(mode="json") for item in prototypes],
            "rows": [item.model_dump(mode="json") for item in rows],
            "conversations": [item.model_dump(mode="json") for item in conversations],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return RoutingBundle(
        prototypes, rows, conversations, hashlib.sha256(canonical).hexdigest()
    )


def dataset_name(bundle: RoutingBundle) -> str:
    return f"healthcare-rag-routing-{bundle.content_hash[:12]}"


def to_langsmith_examples(rows: Sequence[RoutingRow]) -> tuple[LangSmithExample, ...]:
    return tuple(LangSmithExample(uuid.uuid5(NAMESPACE, row.id), row) for row in rows)


def sync_dataset(
    client: RoutingDatasetClient, *, bundle: RoutingBundle | None = None
) -> SyncResult:
    loaded = bundle or load_bundle()
    name = dataset_name(loaded)
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(
            dataset_name=name, description="Frozen healthcare routing evidence"
        )
    examples = to_langsmith_examples(loaded.rows)
    desired = Counter(example.id for example in examples)
    existing = Counter(
        example.id for example in client.list_examples(dataset_id=dataset.id)
    )
    extra = existing - desired
    if extra:
        raise DataContractError(
            f"remote dataset ID multiset differs: extra={sum(extra.values())} missing={sum((desired - existing).values())}"
        )
    missing = desired - existing
    create = [example.as_sdk_payload() for example in examples if example.id in missing]
    update = [
        example.as_sdk_payload() for example in examples if example.id in existing
    ]
    if create:
        client.create_examples(dataset_id=dataset.id, examples=create)
    if update:
        client.update_examples(dataset_id=dataset.id, updates=update)
    remote = Counter(
        example.id for example in client.list_examples(dataset_id=dataset.id)
    )
    if remote != desired:
        raise DataContractError(
            f"remote dataset ID multiset differs: extra={sum((remote - desired).values())} missing={sum((desired - remote).values())}"
        )
    return SyncResult(dataset, len(create), len(update))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate frozen routing evidence")
    _ = parser.add_argument("--validate", action="store_true", required=True)
    _ = parser.parse_args()
    bundle = load_bundle()
    print(
        json.dumps(
            {
                "counts": {
                    "prototypes": len(bundle.prototypes),
                    "rows": len(bundle.rows),
                    "conversations": len(bundle.conversations),
                },
                "sha256": bundle.content_hash,
                "dataset_name": dataset_name(bundle),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
