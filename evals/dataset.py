"""
Golden dataset loading + LangSmith dataset upsert.

The source of truth is ``evals/golden_dataset.json`` (version-controlled).
``sync_dataset`` pushes it to LangSmith idempotently: example UUIDs are derived
from the stable ``id`` field, so re-running updates rather than duplicates.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from langsmith import Client

GOLDEN_PATH = Path(__file__).parent / "golden_dataset.json"
DEFAULT_DATASET_NAME = "healthcare-rag-golden"

# Namespace for deterministic example ids (never change, or ids will churn).
_NS = uuid.UUID("5d0f4d1e-3b7c-4a2e-9d51-9d5c1a3f0b77")


def load_golden(path: Path = GOLDEN_PATH) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    ids = [d["id"] for d in data]
    assert len(ids) == len(set(ids)), "duplicate example ids in golden dataset"
    return data


def to_langsmith_example(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid5(_NS, row["id"]),
        "inputs": {"question": row["question"], "history": row.get("history") or []},
        "outputs": {
            "reference_answer": row["reference_answer"],
            "expected_behavior": row["expected_behavior"],
            "must_mention": row.get("must_mention") or [],
            "must_not_mention": row.get("must_not_mention") or [],
            "expected_source_pages": row.get("expected_source_pages") or [],
            "expected_source_chunk_ids": row.get("expected_source_chunk_ids") or [],
            "drug": row.get("drug"),
            "category": row["category"],
        },
        "split": row.get("split", "core"),
        "metadata": {
            "example_id": row["id"],
            "split": row.get("split", "core"),
            "category": row["category"],
            "drug": row.get("drug"),
            "expected_behavior": row["expected_behavior"],
            "notes": row.get("notes", ""),
        },
    }


def sync_dataset(client: Client, name: str = DEFAULT_DATASET_NAME, path: Path = GOLDEN_PATH) -> tuple[Any, int, int]:
    """Create the dataset if missing and upsert all examples. Returns (dataset, created, updated)."""
    rows = load_golden(path)
    if client.has_dataset(dataset_name=name):
        ds = client.read_dataset(dataset_name=name)
    else:
        ds = client.create_dataset(
            dataset_name=name,
            description="Golden Q/A + behaviour expectations for the healthcare RAG (Lipitor/Metformin monographs). "
            "Source of truth: evals/golden_dataset.json",
        )
    existing = {e.id for e in client.list_examples(dataset_id=ds.id)}
    examples = [to_langsmith_example(r) for r in rows]
    to_create = [e for e in examples if e["id"] not in existing]
    to_update = [e for e in examples if e["id"] in existing]
    if to_create:
        client.create_examples(dataset_id=ds.id, examples=to_create)
    if to_update:
        client.update_examples(dataset_id=ds.id, updates=to_update)
    return ds, len(to_create), len(to_update)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    ds, c, u = sync_dataset(Client())
    print(f"dataset '{ds.name}' ({ds.id}): created={c} updated={u}")
