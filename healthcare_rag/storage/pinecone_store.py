"""
Build and load the serverless Pinecone hybrid index used by ``HC_RAG_RETRIEVER=pinecone``.

Mirrors ``healthcare_rag/storage/vector_store.py``: same CLI shape, same chunk
files, same logical schema — one namespace per collection instead of one
Weaviate collection per monograph.

    python healthcare_rag/storage/pinecone_store.py --delete-all \
        --collection Lipitor   data/chunks_lipitor.json \
        --collection Metformin data/chunks_metformin.json

What lands in Pinecone
----------------------
* One serverless index (``HC_RAG_PINECONE_INDEX``, default ``healthcare-rag``),
  metric **dotproduct** (required for sparse+dense hybrid), dimension 1536.
* One namespace per collection, lower-cased (``lipitor``, ``metformin``).
* Per chunk: a dense vector from OpenAI ``text-embedding-3-small`` over the
  ``contextualized`` text, a sparse vector from Pinecone Inference
  (``pinecone-sparse-english-v0``, ``input_type="passage"``) over the same text,
  and metadata ``{id_, text, contextualized, doc_source, page_numbers}``.

Two deliberate divergences from the Weaviate arm, both unavoidable:

* Weaviate's ``text2vec-openai`` vectoriser embeds the concatenation of the
  class name and *all* five properties (so ``text`` — a near-duplicate of
  ``contextualized`` — plus ``id_`` and ``page_numbers`` ride along). This module
  embeds ``contextualized`` alone: it is the field the Weaviate hybrid's lexical
  half searches (``query_properties=["contextualized"]``) and the field returned
  as document content, and reproducing Weaviate's concatenation would mean
  encoding integers as prose.
* Pinecone metadata values may only be strings, numbers, booleans or **lists of
  strings** — so ``page_numbers`` is stored as ``["3", "4"]`` and converted back
  to ``[3, 4]`` on read (see ``pinecone_retrieval.page_numbers_from_metadata``).

Required environment variables: ``PINECONE_API_KEY`` and ``OPENAI_API_KEY``.
``PINECONE_CLOUD`` / ``PINECONE_REGION`` override the serverless placement
(default ``aws`` / ``us-east-1``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("MedicalRAG")

DEFAULT_UPSERT_BATCH = 100
DENSE_EMBED_BATCH = 96
SPARSE_EMBED_BATCH = 96
EMBEDDING_DIMENSION = 1536
INDEX_METRIC = "dotproduct"
DEFAULT_CLOUD = "aws"
DEFAULT_REGION = "us-east-1"
INDEX_READY_TIMEOUT_S = 300


def require_pinecone_api_key(api_key: str | None = None) -> str:
    """Return the Pinecone key or fail loudly — never hang on an unauthenticated call."""
    key = (api_key if api_key is not None else os.getenv("PINECONE_API_KEY", "")).strip()
    if not key:
        message = "PINECONE_API_KEY is not set"
        raise ValueError(message)
    return key


def require_openai_api_key(api_key: str | None = None) -> str:
    key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()
    if not key:
        message = "OPENAI_API_KEY is not set"
        raise ValueError(message)
    return key


def connect_to_pinecone(api_key: str | None = None):
    """Construct the (sync) Pinecone client."""
    from pinecone import Pinecone

    return Pinecone(api_key=require_pinecone_api_key(api_key))


def namespace_for(collection_name: str) -> str:
    """One namespace per collection, lower-cased — the single naming rule both sides share."""
    return collection_name.strip().lower()


def ensure_index(
    pc: Any,
    index_name: str,
    *,
    dimension: int = EMBEDDING_DIMENSION,
    cloud: str | None = None,
    region: str | None = None,
) -> Any:
    """Create the serverless index when missing, then block until it reports ready."""
    from pinecone import ServerlessSpec

    if not pc.has_index(index_name):
        spec = ServerlessSpec(
            cloud=cloud or os.getenv("PINECONE_CLOUD", DEFAULT_CLOUD),
            region=region or os.getenv("PINECONE_REGION", DEFAULT_REGION),
        )
        logger.info(
            "Creating serverless index '%s' (metric=%s, dim=%s, %s/%s)",
            index_name, INDEX_METRIC, dimension, spec.cloud, spec.region,
        )
        pc.create_index(
            name=index_name, dimension=dimension, metric=INDEX_METRIC, spec=spec
        )
    else:
        logger.info("Index '%s' already exists; reusing it.", index_name)

    deadline = time.monotonic() + INDEX_READY_TIMEOUT_S
    while True:
        description = pc.describe_index(index_name)
        if getattr(description.status, "ready", False):
            break
        if time.monotonic() > deadline:
            message = f"Pinecone index {index_name!r} was not ready after {INDEX_READY_TIMEOUT_S}s"
            raise TimeoutError(message)
        time.sleep(2)
    return pc.Index(index_name)


def _batched(items: list[Any], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def dense_embeddings(
    openai_client: Any, model: str, texts: list[str], batch_size: int = DENSE_EMBED_BATCH
) -> list[list[float]]:
    """OpenAI embeddings for ``texts``, in input order."""
    vectors: list[list[float]] = []
    for batch in _batched(texts, batch_size):
        response = openai_client.embeddings.create(model=model, input=batch)
        vectors.extend(item.embedding for item in response.data)
    return vectors


def sparse_embeddings(
    pc: Any,
    model: str,
    texts: list[str],
    *,
    input_type: str = "passage",
    batch_size: int = SPARSE_EMBED_BATCH,
) -> list[dict[str, list[Any]]]:
    """Pinecone Inference sparse embeddings as ``{"indices": [...], "values": [...]}``."""
    sparse: list[dict[str, list[Any]]] = []
    for batch in _batched(texts, batch_size):
        response = pc.inference.embed(
            model=model,
            inputs=list(batch),
            parameters={"input_type": input_type, "truncate": "END"},
        )
        sparse.extend(
            {
                "indices": list(embedding.sparse_indices),
                "values": list(embedding.sparse_values),
            }
            for embedding in response.data
        )
    return sparse


def chunk_text(chunk: dict[str, Any]) -> str:
    """The text that gets vectorised — the same field the Weaviate hybrid searches."""
    return str(chunk.get("contextualized") or chunk.get("text") or "")


def chunk_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    """Pinecone-legal metadata: page numbers become a list of strings (no int lists allowed)."""
    chunk_id = chunk.get("id_", chunk.get("id"))
    return {
        "id_": int(chunk_id),
        "text": str(chunk.get("text") or ""),
        "contextualized": str(chunk.get("contextualized") or ""),
        "doc_source": str(chunk.get("doc_source") or ""),
        "page_numbers": [str(int(page)) for page in (chunk.get("page_numbers") or [])],
    }


def build_vectors(
    chunks: list[dict[str, Any]],
    namespace: str,
    dense: list[list[float]],
    sparse: list[dict[str, list[Any]]],
) -> list[dict[str, Any]]:
    """Zip chunks with their two vectors into Pinecone upsert records.

    The vector id is ``<namespace>-<chunk id>``, so re-running the ingest
    overwrites rather than duplicates (idempotent without a wipe).
    """
    if not (len(chunks) == len(dense) == len(sparse)):
        message = (
            "chunk/dense/sparse length mismatch: "
            f"{len(chunks)}/{len(dense)}/{len(sparse)}"
        )
        raise ValueError(message)
    records: list[dict[str, Any]] = []
    for chunk, dense_values, sparse_values in zip(chunks, dense, sparse, strict=True):
        metadata = chunk_metadata(chunk)
        records.append(
            {
                "id": f"{namespace}-{metadata['id_']}",
                "values": list(dense_values),
                "sparse_values": sparse_values,
                "metadata": metadata,
            }
        )
    return records


def upsert_vectors(
    index: Any,
    namespace: str,
    vectors: list[dict[str, Any]],
    batch_size: int = DEFAULT_UPSERT_BATCH,
) -> int:
    upserted = 0
    for batch in _batched(vectors, batch_size):
        index.upsert(vectors=batch, namespace=namespace, show_progress=False)
        upserted += len(batch)
        logger.info("Upserted %s/%s vectors into namespace '%s'", upserted, len(vectors), namespace)
    return upserted


def load_data_from_json(json_filepath: Path) -> list[dict[str, Any]]:
    """Load a checked-in chunk file (same contract as vector_store.load_data_from_json)."""
    if not json_filepath.is_file():
        logger.error("JSON data file not found: %s", json_filepath)
        return []
    try:
        data = json.loads(json_filepath.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.exception("Failed to decode JSON from file: %s", json_filepath)
        return []
    except OSError:
        logger.exception("Failed to read file: %s", json_filepath)
        return []
    if not isinstance(data, list):
        logger.error("JSON data in %s is not a list.", json_filepath)
        return []
    logger.info("Loaded %s records from %s", len(data), json_filepath)
    return data


def clear_namespace(index: Any, namespace: str) -> None:
    """Delete every vector in one namespace, tolerating a namespace that never existed."""
    from pinecone.exceptions import NotFoundException

    try:
        index.delete(delete_all=True, namespace=namespace)
        logger.info("Cleared namespace '%s'", namespace)
    except NotFoundException:
        logger.info("Namespace '%s' does not exist yet; nothing to clear.", namespace)


def ingest_chunks(
    pc: Any,
    index: Any,
    collection_name: str,
    chunks: list[dict[str, Any]],
    *,
    openai_client: Any,
    embedding_model: str,
    sparse_model: str,
    batch_size: int = DEFAULT_UPSERT_BATCH,
) -> int:
    """Embed and upsert one collection's chunks into its namespace."""
    namespace = namespace_for(collection_name)
    usable = [chunk for chunk in chunks if chunk_text(chunk).strip()]
    skipped = len(chunks) - len(usable)
    if skipped:
        logger.warning("Skipping %s chunk(s) with no text in '%s'", skipped, collection_name)
    if not usable:
        return 0

    texts = [chunk_text(chunk) for chunk in usable]
    logger.info("Embedding %s chunks for '%s' (%s + %s)", len(texts), collection_name, embedding_model, sparse_model)
    dense = dense_embeddings(openai_client, embedding_model, texts)
    sparse = sparse_embeddings(pc, sparse_model, texts, input_type="passage")
    vectors = build_vectors(usable, namespace, dense, sparse)
    return upsert_vectors(index, namespace, vectors, batch_size)


def main() -> None:
    # Import-time side effects belong to the CLI: the runtime imports the embed
    # helpers from this module and must not have its logging reconfigured.
    import dotenv
    from openai import OpenAI

    dotenv.load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    from healthcare_rag.services.models import (
        embedding_model,
        pinecone_index_name,
        pinecone_sparse_model,
    )

    parser = argparse.ArgumentParser(description="Pinecone hybrid index loader")
    parser.add_argument(
        "--collection",
        action="append",
        nargs=2,
        metavar=("COLLECTION_NAME", "JSON_FILE_PATH"),
        help="Collection name and the path to its JSON chunk file. Repeatable.",
        dest="collections_to_load",
        default=[],
    )
    parser.add_argument(
        "--delete-all",
        action="store_true",
        help="Wipe each target namespace before upserting.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_UPSERT_BATCH,
        help=f"Upsert batch size (default: {DEFAULT_UPSERT_BATCH}, Pinecone's practical maximum).",
    )
    parser.add_argument(
        "--index", type=str, default=None,
        help="Pinecone index name (default: HC_RAG_PINECONE_INDEX or 'healthcare-rag').",
    )
    args = parser.parse_args()

    if not args.collections_to_load:
        logger.warning("No collections specified. Use --collection NAME PATH.")
        return

    index_name = args.index or pinecone_index_name()
    openai_client = OpenAI(api_key=require_openai_api_key())
    pc = connect_to_pinecone()
    index = ensure_index(pc, index_name)

    if args.delete_all:
        for collection_name, _ in args.collections_to_load:
            clear_namespace(index, namespace_for(collection_name))

    for collection_name, json_file in args.collections_to_load:
        chunks = load_data_from_json(Path(json_file))
        if not chunks:
            logger.warning("No data loaded from %s; skipping '%s'.", json_file, collection_name)
            continue
        count = ingest_chunks(
            pc,
            index,
            collection_name,
            chunks,
            openai_client=openai_client,
            embedding_model=embedding_model(),
            sparse_model=pinecone_sparse_model(),
            batch_size=args.batch_size,
        )
        logger.info("Upserted %s vectors for '%s'", count, collection_name)

    stats = index.describe_index_stats()
    logger.info("Index '%s' total vectors: %s", index_name, stats.get("total_vector_count"))
    for namespace, detail in (stats.get("namespaces") or {}).items():
        logger.info("  namespace '%s': %s vectors", namespace, detail.get("vector_count"))


if __name__ == "__main__":
    main()
