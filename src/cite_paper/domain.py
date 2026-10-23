from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass


def digest(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def units(text: str) -> list[re.Match[str]]:
    """Deterministic budget units, NOT model tokens: word, CJK character, punctuation."""
    return list(re.finditer(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]|[^\w\s]", text))


def terms(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]", text.lower())
    stop = {"the", "a", "an", "of", "and", "is", "to", "in", "for", "with", "how", "what"}
    return [w for w in words if w not in stop]


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    page: int
    section: str
    start: int
    end: int
    text: str
    context: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Evidence:
    id: str
    document_id: str
    document_hash: str
    title: str
    source: str
    page: int
    section: str
    start: int
    end: int
    quote: str
    score: float
    chunk_ids: list[str]

    def to_dict(self) -> dict:
        return asdict(self)
