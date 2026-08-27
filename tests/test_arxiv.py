from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from cite_paper.tools.arxiv import ArxivError, parse_atom_feed, search_arxiv

ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <updated>2024-02-02T00:00:00Z</updated>
    <published>2024-01-20T00:00:00Z</published>
    <title>
      A Verifiable Retrieval Agent
    </title>
    <summary>
      We present an agent that retrieves evidence and verifies every claim.
    </summary>
    <author><name>Alice Example</name></author>
    <author><name>Bob Example</name></author>
    <category term="cs.CL" />
    <category term="cs.AI" />
    <link href="http://arxiv.org/abs/2401.12345v2" rel="alternate" type="text/html" />
    <link title="pdf" href="http://arxiv.org/pdf/2401.12345v2" rel="related" type="application/pdf" />
  </entry>
</feed>
"""


def test_parse_atom_feed_returns_normalized_paper() -> None:
    papers = parse_atom_feed(ATOM_FIXTURE)

    assert len(papers) == 1
    paper = papers[0]
    assert paper.arxiv_id == "2401.12345v2"
    assert paper.title == "A Verifiable Retrieval Agent"
    assert paper.authors == ("Alice Example", "Bob Example")
    assert paper.categories == ("cs.CL", "cs.AI")
    assert paper.pdf_url == "http://arxiv.org/pdf/2401.12345v2"
    assert "verifies every claim" in paper.abstract


def test_search_arxiv_builds_bounded_relevance_query() -> None:
    captured: dict[str, object] = {}

    def fake_fetcher(request, timeout: float) -> bytes:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["user_agent"] = request.headers["User-agent"]
        return ATOM_FIXTURE

    papers = search_arxiv(
        '  retrieval   augmented "generation"  ',
        max_results=3,
        timeout=4.5,
        fetcher=fake_fetcher,
    )

    query = parse_qs(urlparse(str(captured["url"])).query)
    assert query["search_query"] == ['all:"retrieval augmented generation"']
    assert query["max_results"] == ["3"]
    assert query["sortBy"] == ["relevance"]
    assert captured["timeout"] == 4.5
    assert str(captured["user_agent"]).startswith("CitePaper/")
    assert len(papers) == 1


@pytest.mark.parametrize("query", ["", "   "])
def test_search_arxiv_rejects_empty_query(query: str) -> None:
    with pytest.raises(ValueError, match="query must not be empty"):
        search_arxiv(query, fetcher=lambda _request, _timeout: ATOM_FIXTURE)


@pytest.mark.parametrize("max_results", [0, 51])
def test_search_arxiv_rejects_unfriendly_result_count(max_results: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 50"):
        search_arxiv("agents", max_results=max_results, fetcher=lambda _request, _timeout: b"")


def test_parse_atom_feed_reports_invalid_xml() -> None:
    with pytest.raises(ArxivError, match="invalid Atom XML"):
        parse_atom_feed(b"not xml")
