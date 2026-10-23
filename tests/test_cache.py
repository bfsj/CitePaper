import json
from pathlib import Path

from cite_paper.cache import load_cached_paper, save_cached_paper
from cite_paper.models import Paper


def _paper(arxiv_id: str = "2401.12345v2") -> Paper:
    return Paper(
        arxiv_id=arxiv_id,
        title="A Verifiable Retrieval Agent",
        authors=("Alice Example", "Bob Example"),
        abstract="An agent that retrieves and verifies evidence.",
        published="2024-01-20T00:00:00Z",
        updated="2024-02-02T00:00:00Z",
        categories=("cs.CL", "cs.AI"),
        abstract_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


def test_load_returns_none_when_cache_file_is_missing(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"

    assert load_cached_paper("agents", 5, cache_path=cache_path) is None


def test_load_returns_none_for_invalid_json(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("not json", encoding="utf-8")

    assert load_cached_paper("agents", 5, cache_path=cache_path) is None


def test_saved_papers_can_be_loaded(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    expected = [_paper()]

    save_cached_paper("agents", 5, expected, cache_path=cache_path)

    assert load_cached_paper("agents", 5, cache_path=cache_path) == expected


def test_load_returns_none_when_entry_has_expired(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    save_cached_paper("agents", 5, [_paper()], cache_path=cache_path)

    data = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = next(iter(data["entries"].values()))
    entry["saved_at"] = 0
    cache_path.write_text(json.dumps(data), encoding="utf-8")

    assert load_cached_paper("agents", 5, cache_path=cache_path, ttl_second=1) is None


def test_save_preserves_other_queries(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    first = [_paper("2401.00001v1")]
    second = [_paper("2401.00002v1")]

    save_cached_paper("agents", 5, first, cache_path=cache_path)
    save_cached_paper("retrieval", 3, second, cache_path=cache_path)

    assert load_cached_paper("agents", 5, cache_path=cache_path) == first
    assert load_cached_paper("retrieval", 3, cache_path=cache_path) == second
