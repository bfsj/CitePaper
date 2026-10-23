"""Authored synthetic fixtures: all names and numbers below are fictional, not papers."""

from __future__ import annotations

from cite_paper.ingest import ingest_pages
from cite_paper.store import Store

FIXTURES = [
    (
        "SYNTHETIC Atlas retrieval study",
        [
            "# Method\nAtlas uses continuous evidence segments for retrieval. "
            "The retriever ranks individual chunks with BM25 before expanding adjacent chunks. "
            "The next paragraph contains the budget constraint, not a separate method.\n\n"
            "Expansion stops at the document boundary. Context is limited to 240 retrieval units. "
            "Source quotations are copied from the original page without adding generated context.",
            "# Experiments\nThis is a fictional test document, not a published experiment. "
            "Atlas is tested on twelve synthetic questions with a fixed context budget. "
            "The evaluation compares isolated chunks, fixed neighbors, and continuous segments.\n"
            "# Limitations\nAtlas has no OCR support. Its parser may reorder two-column PDF text. "
            "It does not verify semantic entailment of generated claims.",
        ],
    ),
    (
        "SYNTHETIC Boreal durable worker study",
        [
            "# Method\nBoreal stores queued research jobs in SQLite with write-ahead logging. "
            "A worker claims a job inside an immediate transaction and receives a lease token. "
            "Only the current token may commit checkpoints or finish the job.\n\n"
            "A heartbeat extends the lease. Expired leases allow another worker to resume. "
            "Recovery resumes at a completed node boundary and may repeat unfinished work.",
            "# Limitations\nBoreal provides at-least-once execution, not exactly-once side effects. "
            "Content-addressed imports make duplicate ingestion harmless. SQLite requires a local "
            "disk and is not a cross-host distributed queue. This entire paper is synthetic.",
        ],
    ),
    (
        "SYNTHETIC Cedar citation study",
        [
            "# Method\nCedar links claims to immutable source snapshots using evidence identifiers. "
            "Each evidence record includes the paper hash, page number, and character offsets. "
            "The verifier reconstructs the quoted span from the stored page text.\n\n"
            "An exact quote match verifies provenance, not whether the claim follows from it. "
            "Semantic entailment is not evaluated automatically in this fictional study.\n"
            "# Limitations\nA valid citation can still support an incorrect inference. "
            "Human review is required for comparative conclusions.",
        ],
    ),
]


def seed_demo(store: Store, tenant: str = "demo") -> list[str]:
    return [
        ingest_pages(store, tenant, title, pages, source="synthetic://citepaper-fixture")["id"]
        for title, pages in FIXTURES
    ]


def default_request(
    ids: list[str], topic: str = "retrieval evidence budget and limitations"
) -> dict:
    return {
        "topic": topic,
        "document_ids": ids,
        "discover": False,
        "budget": 600,
        "strategy": "rse",
        "auto_context": True,
    }
