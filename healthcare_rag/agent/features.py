from __future__ import annotations

import re
import unicodedata
from typing import Final

ERASE_ACTION: Final = re.compile(
    r"\b(?:delete(?:d|ing)?|erase(?:d|ing)?|remove(?:d|ing)?|forget|wipe(?:d|ing)?|"
    + r"clear(?:ed|ing)?|purge(?:d|ing)?|get rid of)\b"
)
ERASE_OBJECT: Final = re.compile(
    r"\b(?:my\s+)?(?:data|account|history|records|everything|medication history)\b"
)


def _normalize(text: str) -> tuple[str, tuple[str, ...]]:
    source = unicodedata.normalize("NFC", text).lower()
    return source, tuple(token for token in re.split(r"[\W_]+", source) if token)


def is_erase_request(question: str) -> bool:
    source, _ = _normalize(question)
    return ERASE_ACTION.search(source) is not None and ERASE_OBJECT.search(source) is not None


__all__ = ["ERASE_ACTION", "ERASE_OBJECT", "is_erase_request"]
