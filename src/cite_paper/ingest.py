"""Immutable page-text snapshots. Offsets refer to extracted page text, not PDF bytes."""

from __future__ import annotations

import io
import re
from itertools import pairwise
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from pypdf import PdfReader

from cite_paper.domain import Chunk, digest, units
from cite_paper.store import Store

ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?\Z")
MAX_PAGES = 80
MAX_TEXT = 1_000_000


def chunk_pages(document_id: str, title: str, pages: list[str], size: int = 100) -> list[Chunk]:
    """Non-overlapping chunks preserve exact page offsets; section headers are metadata."""
    if size < 8:
        raise ValueError("Chunk size must be at least 8 budget units")
    chunks = []
    section = "Unclassified"
    for page_number, page in enumerate(pages, 1):
        # Split only on explicit Markdown or short numbered paper section headings.
        headings = list(re.finditer(r"(?m)^(?:#{1,4}\s+|\d+(?:\.\d+)*[. ]\s*)[^\n]{2,90}$", page))
        boundaries = sorted({0, len(page), *(m.start() for m in headings)})
        for left, right in pairwise(boundaries):
            heading = next((m for m in headings if m.start() == left), None)
            if heading:
                section = re.sub(r"^#+\s*", "", heading.group()).strip()
            matches = units(page[left:right])
            for i in range(0, len(matches), size):
                group = matches[i : i + size]
                start, end = left + group[0].start(), left + group[-1].end()
                chunks.append(
                    Chunk(
                        id=digest(f"{document_id}:{page_number}:{start}:{end}")[:24],
                        document_id=document_id,
                        page=page_number,
                        section=section,
                        start=start,
                        end=end,
                        text=page[start:end],
                        context=f"{title} | {section}",
                    )
                )
    return chunks


def ingest_pages(
    store: Store,
    tenant: str,
    title: str,
    pages: list[str],
    *,
    source: str = "",
    content_hash: str | None = None,
    chunk_size: int = 100,
) -> dict:
    if not title.strip() or not pages or not any(p.strip() for p in pages):
        raise ValueError("Title and non-empty extracted text are required; scanned PDFs need OCR")
    if len(pages) > MAX_PAGES or sum(map(len, pages)) > MAX_TEXT:
        raise ValueError("Document exceeds page/text limits")
    # Include extraction content in identity: parser changes create a new immutable version.
    extracted_hash = digest("\f".join(pages))
    version_hash = digest(f"{content_hash or extracted_hash}:{extracted_hash}")
    doc_id = digest(f"{title.strip()}:{source}:{version_hash}:chunk-v1:{chunk_size}")[:24]
    document = {
        "id": doc_id,
        "title": title.strip(),
        "source": source,
        "hash": version_hash,
        "pages": pages,
    }
    return store.add_document(tenant, document, chunk_pages(doc_id, title, pages, chunk_size))


def ingest_pdf(store: Store, tenant: str, title: str, data: bytes, *, source: str = "") -> dict:
    if not data.startswith(b"%PDF-"):
        raise ValueError("Expected PDF bytes, not a renamed file")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("PDF exceeds 80-page limit")
        pages = [p.extract_text() or "" for p in reader.pages]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Cannot parse PDF") from exc
    return ingest_pages(store, tenant, title, pages, source=source, content_hash=digest(data))


def ingest_file(store: Store, tenant: str, path: Path, title: str | None = None) -> dict:
    data = path.read_bytes()
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("File exceeds 20 MiB")
    if path.suffix.lower() == ".pdf":
        return ingest_pdf(store, tenant, title or path.stem, data)
    return ingest_pages(store, tenant, title or path.stem, [data.decode("utf-8")])


def download_arxiv(
    arxiv_id: str, *, max_bytes: int = 20 * 1024 * 1024, client: httpx.Client | None = None
) -> bytes:
    if not ARXIV_ID.fullmatch(arxiv_id):
        raise ValueError("Invalid arXiv identifier")
    url = f"https://arxiv.org/pdf/{arxiv_id}"

    def fetch(session: httpx.Client) -> bytes:
        nonlocal url
        for _ in range(4):
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in {"arxiv.org", "export.arxiv.org"}
                or parsed.port not in {None, 443}
                or parsed.username
                or parsed.password
                or not parsed.path.startswith("/pdf/")
            ):
                raise ValueError("Blocked PDF redirect outside the arXiv allowlist")
            with session.stream("GET", url, follow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                body = bytearray()
                for part in response.iter_bytes():
                    body.extend(part)
                    if len(body) > max_bytes:
                        raise ValueError("PDF download exceeds size limit")
                return bytes(body)
        raise ValueError("Too many PDF redirects")

    if client is not None:
        return fetch(client)
    with httpx.Client(
        timeout=30, headers={"User-Agent": "CitePaper/0.2 (research prototype)"}
    ) as c:
        return fetch(c)
