"""Build and cache a PageIndex tree for one monograph PDF.

Why this is a standalone script
-------------------------------
``pageindex`` (VectifyAI, MIT) pulls ``openai>=2`` and ``litellm``, which are
incompatible with the app venv's ``openai<2`` pin (langchain-openai). So this
module is *never* imported by the runtime — it runs in an ephemeral env:

    uv run --no-project --with pageindex --with python-dotenv --python 3.12 \
        python healthcare_rag/storage/pageindex_index.py \
        --pdf docs/lipitor.pdf --out data/pageindex_tree_lipitor.json

or simply ``make index-pageindex``. The runtime only reads the JSON it writes
(``healthcare_rag/processors/pageindex_retrieval.py``), so it depends on the
cached artifact, not on the library.

Output shape::

    {
      "source_pdf": "docs/lipitor.pdf",
      "collection": "Lipitor",
      "index_model": "openai/gpt-5.6-luna",
      "mode": "flash",
      "page_count": 48,
      "generated_at": "2026-08-20T12:00:00+00:00",
      "tree": [ {node_id, title, start_index, end_index, summary, nodes: [...]} ]
    }

Page indices are 1-based and inclusive, matching ``page_numbers`` in
``data/chunks_*.json``.

Note on the PageIndex API: the SDK's ``get_tree()`` returns the *cloud wire
shape*, which renames ``start_index`` to ``page_index`` and drops
``end_index`` — useless for a page-range mapping. ``LocalAPI.raw_tree()``
returns the stored tree verbatim (with both bounds), so that is the primary
source; ``get_tree(node_summary=True)`` is the fallback and its missing
``end_index`` is then inferred from the next node's start.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_INDEX_MODEL = "openai/gpt-5.6-luna"


def _normalize(nodes: list[dict[str, Any]], page_count: int) -> list[dict[str, Any]]:
    """Keep only the fields the retrieval adapter needs, with sane page bounds."""
    out: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        children = _normalize(node.get("nodes") or [], page_count)
        start = node.get("start_index")
        if start is None:
            start = node.get("page_index")
        end = node.get("end_index")
        if end is None:
            # Cloud wire shape has no end_index: run to just before the next
            # sibling, or to the last page for the final sibling.
            following = nodes[index + 1 :]
            next_start = next(
                (
                    sibling.get("start_index") or sibling.get("page_index")
                    for sibling in following
                    if (sibling.get("start_index") or sibling.get("page_index"))
                ),
                None,
            )
            end = (next_start - 1) if next_start else page_count
        if start is None:
            start = children[0]["start_index"] if children else 1
        start = max(1, min(int(start), page_count))
        end = max(start, min(int(end), page_count))
        for child in children:
            end = max(end, child["end_index"])
        summary = node.get("summary") or node.get("prefix_summary") or ""
        out.append(
            {
                "node_id": str(node.get("node_id") or f"n{index}"),
                "title": str(node.get("title") or "").strip(),
                "start_index": start,
                "end_index": end,
                "summary": " ".join(str(summary).split()),
                "nodes": children,
            }
        )
    return out


def _stats(nodes: list[dict[str, Any]], depth: int = 1) -> tuple[int, int, set[int]]:
    count = 0
    max_depth = 0
    pages: set[int] = set()
    for node in nodes:
        count += 1
        max_depth = max(max_depth, depth)
        pages.update(range(node["start_index"], node["end_index"] + 1))
        sub_count, sub_depth, sub_pages = _stats(node["nodes"], depth + 1)
        count += sub_count
        max_depth = max(max_depth, sub_depth)
        pages.update(sub_pages)
    return count, max_depth, pages


def build_tree(pdf: Path, model: str, mode: str) -> dict[str, Any]:
    # Imported here, not at module scope: the package exists only in the ephemeral env.
    from pageindex import PageIndexClient

    client = PageIndexClient(index_model=model, storage_path=".pageindex")
    submitted = client.submit_document(str(pdf), mode=mode)
    doc_id = submitted["doc_id"]

    # LocalAPI.raw_tree is the only source of end_index; get_tree drops it.
    raw = getattr(client._api, "raw_tree", None)
    structure = raw(doc_id) if raw is not None else None
    if not structure:
        structure = client.get_tree(doc_id, node_summary=True, include_text=False)["result"]

    meta = client.get_document(doc_id)
    page_count = int(meta.get("pageNum") or meta.get("page_count") or 0)
    if not page_count:
        page_count = len(client.get_ocr(doc_id, format="page")["result"])

    return {
        "source_pdf": pdf.as_posix(),
        "collection": pdf.stem.capitalize(),
        "index_model": model,
        "mode": mode,
        "page_count": page_count,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tree": _normalize(structure, page_count),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default=os.getenv("HC_RAG_PAGEINDEX_MODEL", DEFAULT_INDEX_MODEL))
    parser.add_argument("--mode", default="flash", choices=("flash", "standard"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.out.exists() and not args.force:
        print(f"SKIP {args.out} already exists (use --force to rebuild)")
        return 0

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set (put it in .env)", file=sys.stderr)
        return 1

    payload = build_tree(args.pdf, args.model, args.mode)
    count, depth, pages = _stats(payload["tree"])
    page_count = payload["page_count"]
    missing = sorted(set(range(1, page_count + 1)) - pages)
    out_of_range = [p for p in pages if p < 1 or p > page_count]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print(
        f"WROTE {args.out} collection={payload['collection']} mode={payload['mode']} "
        f"pages={page_count} nodes={count} depth={depth} "
        f"covered={len(pages)}/{page_count} uncovered={missing} out_of_range={out_of_range}"
    )
    return 0


if __name__ == "__main__":  # PageIndex Flash spawns worker processes.
    raise SystemExit(main())
