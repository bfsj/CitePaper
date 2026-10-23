"""Reproducible span retrieval evaluation; never equate it with answer factual accuracy."""

from __future__ import annotations

import json
import platform
import statistics
import time
from importlib.metadata import version
from pathlib import Path

from cite_paper.demo import seed_demo
from cite_paper.domain import units
from cite_paper.retrieval import retrieve, verify_evidence
from cite_paper.store import Store

CASES = [
    (0, 0, "Atlas retrieval budget constraint", "Context is limited to 240 retrieval units."),
    (0, 0, "Atlas source quotations generated context", "without adding generated context."),
    (0, 1, "Atlas parser OCR limitations", "Atlas has no OCR support."),
    (
        0,
        1,
        "Atlas evaluation comparisons",
        "isolated chunks, fixed neighbors, and continuous segments.",
    ),
    (1, 0, "Boreal worker claims lease token", "receives a lease token."),
    (1, 0, "Boreal crash recovery unfinished work", "may repeat unfinished work."),
    (1, 1, "Boreal exactly once execution", "not exactly-once side effects."),
    (1, 1, "Boreal SQLite deployment limits", "not a cross-host distributed queue."),
    (2, 0, "Cedar evidence record provenance", "page number, and character offsets."),
    (2, 0, "Cedar semantic entailment verification", "not whether the claim follows from it."),
    (
        2,
        0,
        "Cedar valid citation inference limitations",
        "can still support an incorrect inference.",
    ),
    (2, 0, "Cedar comparative conclusions review", "Human review is required"),
]


def evaluate(
    store: Store,
    tenant: str,
    ids: list[str],
    cases: list[dict],
    *,
    budget=240,
    repeats=5,
    label="synthetic-regression",
) -> dict:
    if repeats < 1 or not cases:
        raise ValueError("Evaluation needs cases and at least one repeat")
    docs = {d["id"]: d for d in store.documents(tenant, ids)}
    results = []
    for strategy, context in [("topk", True), ("neighbors", True), ("rse", True), ("rse", False)]:
        rows = []
        for case in cases:
            doc = docs[case["document_id"]]
            page = doc["pages"][case["page"] - 1]
            start = page.index(case["gold"])
            end = start + len(case["gold"])
            elapsed = []
            for _ in range(repeats):
                before = time.perf_counter()
                evidence = retrieve(
                    store,
                    tenant,
                    ids,
                    case["query"],
                    budget=budget,
                    strategy=strategy,
                    auto_context=context,
                )
                elapsed.append((time.perf_counter() - before) * 1000)
            covered = []
            for e in evidence:
                if e.document_id == case["document_id"] and e.page == case["page"]:
                    covered.append((max(start, e.start), min(end, e.end)))
            # Segments are non-overlapping by construction.
            coverage = min(1, sum(max(0, b - a) for a, b in covered) / (end - start))
            rows.append(
                {
                    "query": case["query"],
                    "full_span_hit": coverage == 1,
                    "gold_span_coverage": round(coverage, 4),
                    "units": sum(len(units(e.quote)) for e in evidence),
                    "source_spans_valid": all(
                        verify_evidence(e.to_dict(), docs)[0] for e in evidence
                    ),
                    "latency_ms": elapsed,
                }
            )
        timings = sorted(t for row in rows for t in row["latency_ms"])
        results.append(
            {
                "strategy": strategy,
                "auto_context": context,
                "full_span_recall": sum(r["full_span_hit"] for r in rows) / len(rows),
                "mean_span_coverage": statistics.mean(r["gold_span_coverage"] for r in rows),
                "mean_units": statistics.mean(r["units"] for r in rows),
                "p50_ms": statistics.median(timings),
                "p95_ms": timings[min(len(timings) - 1, int(len(timings) * 0.95))],
                "budget_violations": sum(r["units"] > budget for r in rows),
                "invalid_source_spans": sum(not r["source_spans_valid"] for r in rows),
                "cases": rows,
            }
        )
    return {
        "label": label,
        "created_unix": time.time(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "langgraph": version("langgraph"),
        "budget_units": budget,
        "case_count": len(cases),
        "repeats": repeats,
        "documents": [
            {"id": d["id"], "title": d["title"], "hash": d["hash"]} for d in docs.values()
        ],
        "results": results,
        "limitations": [
            "Small manually selected retrieval regression, not an unbiased benchmark.",
            "Budget units are not model tokenizer tokens.",
            "No LLM answer quality or semantic entailment measured.",
            "RSE is not guaranteed to beat simpler baselines.",
        ],
    }


def synthetic_evaluation(store: Store, *, budget=240, repeats=5) -> dict:
    ids = seed_demo(store, "evaluation")
    cases = [{"document_id": ids[d], "page": p + 1, "query": q, "gold": g} for d, p, q, g in CASES]
    return evaluate(store, "evaluation", ids, cases, budget=budget, repeats=repeats)


def save_evaluation(result: dict, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    (target / "metrics.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        "# Retrieval evaluation",
        "",
        f"Corpus: {result['label']}; "
        f"cases: {result['case_count']}; budget: {result['budget_units']} retrieval units.",
        "",
        "| Strategy | Context | Full span recall | Mean units | p50 ms | p95 ms |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in result["results"]:
        lines.append(
            f"| {row['strategy']} | {row['auto_context']} | "
            f"{row['full_span_recall']:.3f} | {row['mean_units']:.1f} | "
            f"{row['p50_ms']:.2f} | {row['p95_ms']:.2f} |"
        )
    lines += ["", *["- " + item for item in result["limitations"]]]
    (target / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
