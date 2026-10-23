"""Cross-process arXiv metadata cache and rate reservations on the same local database."""

from __future__ import annotations

import time

import httpx

from cite_paper.store import Store
from cite_paper.tools.arxiv import ArxivError, search_arxiv


def pace_arxiv(store: Store, interval: float = 3):
    now = time.time()
    with store.connect(write=True) as db:
        db.execute("CREATE TABLE IF NOT EXISTS network_pacing (name TEXT PRIMARY KEY, next REAL)")
        row = db.execute("SELECT next FROM network_pacing WHERE name='arxiv'").fetchone()
        reserved = max(now, row[0] if row else now)
        db.execute(
            "INSERT OR REPLACE INTO network_pacing VALUES ('arxiv',?)", (reserved + interval,)
        )
    if reserved > now:
        time.sleep(reserved - now)


def cached_search(store: Store, query: str, max_results: int = 5):
    def fetch(request, timeout):
        key = request.full_url
        with store.connect(write=True) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS network_cache "
                "(key TEXT PRIMARY KEY, body BLOB, expires REAL)"
            )
            row = db.execute(
                "SELECT body,expires FROM network_cache WHERE key=?", (key,)
            ).fetchone()
            if row and row["expires"] > time.time():
                return row["body"]
        pace_arxiv(store)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.get(key, headers={"User-Agent": "CitePaper/0.2"})
                response.raise_for_status()
                body = response.content
                if len(body) > 2 * 1024 * 1024:
                    raise ArxivError("arXiv metadata response too large")
        except httpx.HTTPError as exc:
            raise ArxivError("arXiv metadata request failed") from exc
        with store.connect(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO network_cache VALUES (?,?,?)",
                (key, body, time.time() + 86400),
            )
        return body

    return search_arxiv(query, max_results=max_results, fetcher=fetch)
