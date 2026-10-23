import io
from itertools import pairwise

import httpx
import pytest
from reportlab.pdfgen.canvas import Canvas

from cite_paper.domain import units
from cite_paper.ingest import chunk_pages, download_arxiv, ingest_pages, ingest_pdf
from cite_paper.retrieval import retrieve, verify_evidence


def test_chunk_offsets_and_section_boundaries():
    pages = ["# Method\n" + "Alpha beta gamma. " * 30 + "\n# Limits\nNo OCR is supported."]
    chunks = chunk_pages("doc", "Title", pages, size=12)
    assert len(chunks) > 4
    assert {c.section for c in chunks} == {"Method", "Limits"}
    for c in chunks:
        assert c.text == pages[c.page - 1][c.start : c.end]
        assert len(units(c.text)) <= 12
    assert all(a.end <= b.start for a, b in pairwise(chunks))


def test_ingestion_idempotent_and_versions_immutable(store):
    a = ingest_pages(store, "a", "Title", ["Evidence one."])
    b = ingest_pages(store, "a", "Title", ["Evidence one."])
    c = ingest_pages(store, "a", "Title", ["Evidence two."])
    assert a["id"] == b["id"] != c["id"]
    assert len(store.documents("a")) == 2
    assert store.documents("b") == []
    assert len(store.chunks("a", [a["id"]])) == 1


@pytest.mark.parametrize("strategy", ["topk", "neighbors", "rse"])
def test_retrieval_budget_no_overlap_and_exact_quotes(store, strategy):
    doc = ingest_pages(
        store,
        "a",
        "Evidence",
        ["# Method\n" + "Retrieval uses evidence with fixed budget. " * 40],
        chunk_size=16,
    )
    found = retrieve(
        store, "a", [doc["id"]], "retrieval evidence budget", budget=100, strategy=strategy
    )
    assert found
    assert sum(len(units(e.quote)) for e in found) <= 100
    for i, e in enumerate(found):
        assert verify_evidence(e.to_dict(), {doc["id"]: doc}) == (True, "exact_source_span")
        for other in found[i + 1 :]:
            assert max(e.start, other.start) >= min(e.end, other.end)
    assert retrieve(store, "b", [doc["id"]], "evidence") == []


@pytest.mark.parametrize("change", ["quote", "document_hash", "page", "start", "source", "title"])
def test_tampered_evidence_rejected(store, change):
    doc = ingest_pages(store, "a", "Title", ["Immutable source evidence."])
    evidence = retrieve(store, "a", [doc["id"]], "evidence")[0].to_dict()
    evidence[change] = 999 if change in {"page", "start"} else "forged"
    assert not verify_evidence(evidence, {doc["id"]: doc})[0]


def test_autocontext_never_becomes_quote(store):
    doc = ingest_pages(store, "a", "SpecialMetadataTerm", ["Actual source body."])
    found = retrieve(store, "a", [doc["id"]], "SpecialMetadataTerm")
    assert found and "SpecialMetadataTerm" not in found[0].quote
    assert retrieve(store, "a", [doc["id"]], "SpecialMetadataTerm", auto_context=False) == []


def test_pdf_pages_and_invalid_pdf(store):
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 720, "First page: cache provenance.")
    canvas.showPage()
    canvas.drawString(72, 720, "Second page: immutable evidence.")
    canvas.save()
    doc = ingest_pdf(store, "a", "Test PDF", buffer.getvalue())
    assert len(doc["pages"]) == 2
    assert "First page" in doc["pages"][0]
    e = retrieve(store, "a", [doc["id"]], "immutable evidence")[0]
    assert e.page == 2
    with pytest.raises(ValueError):
        ingest_pdf(store, "a", "Bad", b"not a PDF")
    with pytest.raises(ValueError):
        ingest_pages(store, "a", "Empty", [""])


@pytest.mark.parametrize("identifier", ["../../etc/passwd", "http://localhost", "2401.12345?url=x"])
def test_download_rejects_invalid_ids(identifier):
    with pytest.raises(ValueError):
        download_arxiv(identifier)


def test_download_blocks_external_redirect_and_oversize():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        )
    ) as client:
        with pytest.raises(ValueError, match="allowlist"):
            download_arxiv("2005.11401v4", client=client)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"%PDF-" + b"x" * 100))
    ) as client:
        with pytest.raises(ValueError, match="size"):
            download_arxiv("2005.11401v4", max_bytes=30, client=client)
