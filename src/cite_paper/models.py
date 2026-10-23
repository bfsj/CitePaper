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

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Paper:
        return cls(
            arxiv_id=data["arxiv_id"],
            title=data["title"],
            authors=tuple(data["authors"]),
            abstract=data["abstract"],
            published=data["published"],
            updated=data["updated"],
            categories=tuple(data["categories"]),
            abstract_url=data["abstract_url"],
            pdf_url=data.get("pdf_url"),
        )
