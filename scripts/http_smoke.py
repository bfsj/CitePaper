"""Exercise the actual HTTP listener (not TestClient); output an auditable JSON summary."""

import json
import time
from pathlib import Path

import httpx


def main():
    with httpx.Client(base_url="http://127.0.0.1:8012", trust_env=False, timeout=20) as client:
        health = client.get("/health")
        health.raise_for_status()
        with Path(".citepaper/pdfs/2005.11401v4.pdf").open("rb") as pdf:
            response = client.post(
                "/documents/pdf",
                data={"title": "RAG real HTTP smoke"},
                files={"file": ("rag.pdf", pdf, "application/pdf")},
            )
        response.raise_for_status()
        document = response.json()
        body = {"topic": "RAG parametric memory Wikipedia", "document_ids": [document["id"]]}
        submission = client.post("/runs", json=body, headers={"Idempotency-Key": "real-http-smoke"})
        submission.raise_for_status()
        run_id = submission.json()["id"]
        for _ in range(80):
            status = client.get("/runs/" + run_id).json()
            if status["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert status["status"] == "succeeded", status
        events = client.get(f"/runs/{run_id}/events").text
        assert "event: succeeded" in events
        first_id = events.split("id: ", 1)[1].split("\n", 1)[0]
        replay = client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": first_id}).text
        assert "event: queued" not in replay
        review = client.post(
            f"/runs/{run_id}/reviews",
            json={
                "report_hash": status["report_hash"],
                "decision": "reject",
                "note": "Smoke test only; semantic content has not been reviewed.",
            },
        )
        review.raise_for_status()
        report = client.get(f"/runs/{run_id}/report")
        report.raise_for_status()
        result = {
            "transport": "real localhost HTTP",
            "pdf_upload": response.status_code,
            "submission": submission.status_code,
            "status": status["status"],
            "event_count": events.count("\nevent:"),
            "sse_replay": True,
            "review_audit": review.status_code,
            "report_bytes": len(report.content),
            "verification": status["result"]["verification"],
        }
        output = Path("docs/results/http-smoke.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
