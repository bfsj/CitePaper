from __future__ import annotations

from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from cite_paper.models import Paper

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM = "http://www.w3.org/2005/Atom"
NAMESPACES = {"atom": ATOM}
DEFAULT_USER_AGENT = "CitePaper/0.1 (educational research project)"

Fetcher = Callable[[Request, float], bytes]


class ArxivError(RuntimeError):
    """Raised when an arXiv request or response cannot be processed."""


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _required_text(entry: ElementTree.Element, field: str) -> str:
    value = _clean_text(entry.findtext(f"atom:{field}", namespaces=NAMESPACES))
    if not value:
        raise ArxivError(f"arXiv entry is missing required field: {field}")
    return value


def _extract_arxiv_id(abstract_url: str) -> str:
    marker = "/abs/"
    if marker not in abstract_url:
        raise ArxivError(f"Unexpected arXiv abstract URL: {abstract_url}")
    return abstract_url.split(marker, maxsplit=1)[1]


def parse_atom_feed(payload: bytes | str) -> list[Paper]:
    """Parse an arXiv Atom response into normalized paper metadata."""

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ArxivError("arXiv returned invalid Atom XML") from exc

    papers: list[Paper] = []
    for entry in root.findall("atom:entry", NAMESPACES):
        abstract_url = _required_text(entry, "id")
        authors = tuple(
            name
            for author in entry.findall("atom:author", NAMESPACES)
            if (name := _clean_text(author.findtext("atom:name", namespaces=NAMESPACES)))
        )
        categories = tuple(
            category.attrib["term"]
            for category in entry.findall("atom:category", NAMESPACES)
            if category.attrib.get("term")
        )

        pdf_url = None
        for link in entry.findall("atom:link", NAMESPACES):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href")
                break

        papers.append(
            Paper(
                arxiv_id=_extract_arxiv_id(abstract_url),
                title=_required_text(entry, "title"),
                authors=authors,
                abstract=_required_text(entry, "summary"),
                published=_required_text(entry, "published"),
                updated=_required_text(entry, "updated"),
                categories=categories,
                abstract_url=abstract_url,
                pdf_url=pdf_url,
            )
        )

    return papers


def _default_fetcher(request: Request, timeout: float) -> bytes:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ArxivError(f"arXiv request failed: {exc}") from exc


def search_arxiv(
    query: str,
    *,
    max_results: int = 5,
    timeout: float = 30.0,
    fetcher: Fetcher | None = None,
) -> list[Paper]:
    """Search arXiv by topic and return results ordered by relevance."""

    normalized_query = _clean_text(query).replace('"', "")
    if not normalized_query:
        raise ValueError("query must not be empty")
    if not 1 <= max_results <= 50:
        raise ValueError("max_results must be between 1 and 50")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    params = {
        "search_query": f'all:"{normalized_query}"',
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    request = Request(
        f"{ARXIV_API_URL}?{urlencode(params)}",
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )
    payload = (fetcher or _default_fetcher)(request, timeout)
    return parse_atom_feed(payload)
