from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, override

from pydantic import BaseModel, ConfigDict, RootModel


class ChunkFileRow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    id: int
    contextualized: str


class ChunkFile(RootModel[tuple[ChunkFileRow, ...]]):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


@dataclass(frozen=True, slots=True)
class CheckedChunk:
    source_name: str
    chunk_id: int
    content: str


@dataclass(frozen=True, slots=True)
class ChunkMappingError(LookupError):
    source_name: str
    chunk_id: int | str | None

    @override
    def __str__(self) -> str:
        return (
            "checked-in chunk mapping failed for "
            f"source_name={self.source_name!r}, id_={self.chunk_id!r}"
        )


@dataclass(frozen=True, slots=True)
class ChunkCatalog:
    chunks: Mapping[tuple[str, int], CheckedChunk]

    SOURCES: ClassVar[Mapping[str, str]] = {
        "Lipitor": "chunks_lipitor.json",
        "Metformin": "chunks_metformin.json",
    }

    @classmethod
    def load(cls, data_dir: Path) -> ChunkCatalog:
        chunks: dict[tuple[str, int], CheckedChunk] = {}
        for source_name, filename in cls.SOURCES.items():
            parsed = ChunkFile.model_validate_json(
                (data_dir / filename).read_text(encoding="utf-8")
            )
            for row in parsed.root:
                chunks[(source_name, row.id)] = CheckedChunk(
                    source_name=source_name,
                    chunk_id=row.id,
                    content=row.contextualized,
                )
        return cls(chunks=chunks)

    def resolve(
        self, source_name: str, metadata: Mapping[str, int | str]
    ) -> CheckedChunk:
        runtime_id = metadata.get("id_")
        try:
            chunk_id = int(runtime_id) if runtime_id is not None else None
        except ValueError:
            chunk_id = None
        if chunk_id is None or (source_name, chunk_id) not in self.chunks:
            raise ChunkMappingError(source_name=source_name, chunk_id=runtime_id)
        return self.chunks[(source_name, chunk_id)]


__all__ = ["CheckedChunk", "ChunkCatalog", "ChunkMappingError"]
