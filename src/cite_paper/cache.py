import json
import time
from pathlib import Path

from cite_paper.models import Paper

DEFAULT_CACHE_PATH = Path(".citepaper/arxiv-cache.json")


def load_cached_paper(
    query: str,
    max_result: int,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    ttl_second: int = 86400,
) -> list[Paper] | None:
    """Load unexpired papers for one query, or return None on a cache miss."""

    key = _make_cache_key(query, max_result)
    if not cache_path.exists():
        return None

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    entries = data.get("entries")
    if not isinstance(entries, dict):
        return None

    entry = entries.get(key)
    if not isinstance(entry, dict):
        return None

    saved_at = entry.get("saved_at")
    if not isinstance(saved_at, (int, float)):
        return None
    if time.time() - saved_at > ttl_second:
        return None

    paper_data = entry.get("papers")
    if not isinstance(paper_data, list) or not all(isinstance(item, dict) for item in paper_data):
        return None

    try:
        return [Paper.from_dict(item) for item in paper_data]
    except (KeyError, TypeError):
        return None


def save_cached_paper(
    query: str,
    max_result: int,
    papers: list[Paper],
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> None:
    """Save papers for one query without discarding other cached queries."""

    entries: dict[str, object] = {}
    if cache_path.exists():
        try:
            existing_data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_data = None

        if isinstance(existing_data, dict):
            existing_entries = existing_data.get("entries")
            if isinstance(existing_entries, dict):
                entries = existing_entries

    key = _make_cache_key(query, max_result)
    entries[key] = {
        "saved_at": time.time(),
        "papers": [paper.to_dict() for paper in papers],
    }

    data = {"entries": entries}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _make_cache_key(query: str, max_result: int) -> str:
    """Make a deterministic cache key from the query and result limit."""

    return json.dumps(
        {"query": query, "max_result": max_result},
        ensure_ascii=False,
        sort_keys=True,
    )
