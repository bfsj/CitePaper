from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Paper:
    """Normalized metadata for one arXiv paper."""

    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    published: str
    updated: str
    categories: tuple[str, ...]
    abstract_url: str
    pdf_url: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
