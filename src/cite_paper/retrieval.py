"""BM25 + source-aware continuous evidence segments (inspired by dsRAG RSE).

This is an independent budgeted implementation, not dsRAG's algorithm or benchmark.
No generated title/section context is ever emitted as a source quotation.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from cite_paper.domain import Chunk, Evidence, digest, terms, units
from cite_paper.store import Store


def bm25(query: str, chunks: list[Chunk], *, auto_context: bool = True) -> list[float]:
    bodies = [terms(c.text) + (terms(c.context) if auto_context else []) for c in chunks]
    if not bodies:
        return []
    counts = [Counter(t) for t in bodies]
    df = Counter(t for body in bodies for t in set(body))
    avg = sum(map(len, bodies)) / len(bodies) or 1
    scores = []
    for body, frequencies in zip(bodies, counts, strict=True):
        score = 0.0
        for term in set(terms(query)):
            n = frequencies[term]
            inverse = math.log(1 + (len(bodies) - df[term] + 0.5) / (df[term] + 0.5))
            score += inverse * n * 2.2 / (n + 1.2 * (0.25 + 0.75 * len(body) / avg))
        scores.append(score)
    return scores


def retrieve(
    store: Store,
    tenant: str,
    document_ids: list[str],
    query: str,
    *,
    budget: int = 600,
    strategy: str = "rse",
    auto_context: bool = True,
    exclude_spans: tuple[tuple[str, int, int, int], ...] = (),
) -> list[Evidence]:
    if budget < 16 or budget > 4000 or strategy not in {"topk", "neighbors", "rse"}:
        raise ValueError("Invalid retrieval budget or strategy")
    docs = {d["id"]: d for d in store.documents(tenant, document_ids)}
    chunks = store.chunks(tenant, list(docs))
    scores = bm25(query, chunks, auto_context=auto_context)
    if not scores or max(scores) <= 0:
        return []
    maximum = max(scores)
    groups = defaultdict(list)
    for c, score in zip(chunks, scores, strict=True):
        groups[(c.document_id, c.page, c.section)].append((c, score))
    candidates = []
    for (doc_id, page_number, _), group in groups.items():
        group.sort(key=lambda pair: pair[0].start)
        page = docs[doc_id]["pages"][page_number - 1]
        for i, (_first, first_score) in enumerate(group):
            if strategy == "topk":
                spans = [(i, i + 1)]
            elif strategy == "neighbors":
                spans = [(max(0, i - 1), min(len(group), i + 2))] if first_score > 0 else []
            else:
                spans = [(i, j) for j in range(i + 1, min(len(group), i + 3) + 1)]
            for start, end in spans:
                selected = group[start:end]
                left, right = selected[0][0].start, selected[-1][0].end
                cost = len(units(page[left:right]))
                if cost > budget or max(s for _, s in selected) <= 0:
                    continue
                # Reward relevance and continuity; charge for irrelevant context and length.
                value = sum(s / maximum - 0.18 for _, s in selected)
                if strategy != "rse":
                    value = max(s for _, s in selected) / maximum
                candidates.append((value, cost, doc_id, page_number, left, right, selected))
    # RSE chooses at most budget/length non-overlapping segments greedily; not a global optimum.
    candidates.sort(key=lambda x: (-x[0], x[1], x[2], x[3], x[4]))
    evidence: list[Evidence] = []
    spent = 0
    for value, cost, doc_id, page_number, left, right, selected in candidates:
        if value <= 0 or spent + cost > budget:
            continue
        if any(
            d == doc_id and p == page_number and max(left, a) < min(right, b)
            for d, p, a, b in exclude_spans
        ):
            continue
        if any(
            e.document_id == doc_id
            and e.page == page_number
            and max(left, e.start) < min(right, e.end)
            for e in evidence
        ):
            continue
        doc = docs[doc_id]
        evidence.append(
            Evidence(
                id="E" + digest(f"{doc_id}:{page_number}:{left}:{right}")[:12],
                document_id=doc_id,
                document_hash=doc["hash"],
                title=doc["title"],
                source=doc["source"],
                page=page_number,
                section=selected[0][0].section,
                start=left,
                end=right,
                quote=doc["pages"][page_number - 1][left:right],
                score=round(value, 6),
                chunk_ids=[c.id for c, _ in selected],
            )
        )
        spent += cost
    return evidence


def verify_evidence(evidence: dict, documents: dict[str, dict]) -> tuple[bool, str]:
    doc = documents.get(evidence.get("document_id"))
    if not doc or doc["hash"] != evidence.get("document_hash"):
        return False, "missing_or_stale_document"
    page, start, end = evidence.get("page"), evidence.get("start"), evidence.get("end")
    if (
        not isinstance(page, int)
        or not 1 <= page <= len(doc["pages"])
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= start < end <= len(doc["pages"][page - 1])
    ):
        return False, "invalid_span"
    if doc["pages"][page - 1][start:end] != evidence.get("quote"):
        return False, "quote_mismatch"
    # Prevent forged labels/source links even when the quote and offsets match.
    if evidence.get("title") != doc["title"] or evidence.get("source") != doc["source"]:
        return False, "metadata_mismatch"
    return True, "exact_source_span"
