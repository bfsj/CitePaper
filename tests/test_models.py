from cite_paper.models import Paper


def test_paper_dict_round_trip():
    paper1 = Paper(
        arxiv_id="1234.56789",
        title="Test Paper",
        authors=("Alice", "Bob"),
        abstract="This is a test paper.",
        published="2026-09-01",
        updated="2026-09-02",
        categories=("cs.AI", "cs.LG"),
        abstract_url="https://example.com/paper",
        pdf_url="https://example.com/paper.pdf",
    )

    paper2 = Paper.from_dict(paper1.to_dict())

    assert paper1 == paper2
    assert isinstance(paper2.authors, tuple)
    assert isinstance(paper2.categories, tuple)
