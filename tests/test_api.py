from fastapi.testclient import TestClient

from cite_paper.api import create_app
from cite_paper.config import Settings
from cite_paper.worker import Worker

KEY_A = "test-key-tenant-a-0000"
KEY_B = "test-key-tenant-b-0000"


def test_api_job_lifecycle_isolation_idempotency_and_sse(tmp_path):
    settings = Settings(database=tmp_path / "api.sqlite3", api_keys={KEY_A: "a", KEY_B: "b"})
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/documents").status_code == 401
        a = {"Authorization": "Bearer " + KEY_A}
        b = {"Authorization": "Bearer " + KEY_B}
        doc = client.post(
            "/documents/text",
            headers=a,
            json={
                "title": "Citation study",
                "text": "# Method\nEvidence quotes preserve source spans. " * 15,
            },
        ).json()
        body = {"topic": "evidence source spans", "document_ids": [doc["id"]]}
        assert client.post("/runs", headers=b, json=body).status_code == 404
        headers = a | {"Idempotency-Key": "test-submission"}
        response = client.post("/runs", headers=headers, json=body)
        assert response.status_code == 202
        run_id = response.json()["id"]
        assert client.post("/runs", headers=headers, json=body).json()["id"] == run_id
        assert (
            client.post("/runs", headers=headers, json=body | {"topic": "changed"}).status_code
            == 409
        )
        assert client.get(f"/runs/{run_id}", headers=b).status_code == 404
        assert client.get(f"/runs/{run_id}/events", headers=b).status_code == 404
        assert client.get(f"/runs/{run_id}/report", headers=a).status_code == 409
        # The submission request has ended; independent worker execution still completes.
        Worker(client.app.state.store, settings).once()
        result = client.get(f"/runs/{run_id}", headers=a).json()
        assert result["status"] == "succeeded"
        assert "lease_token" not in result
        stream = client.get(f"/runs/{run_id}/events", headers=a).text
        assert "event: succeeded" in stream
        first_id = int(stream.split("id: ", 1)[1].split("\n", 1)[0])
        resumed = client.get(
            f"/runs/{run_id}/events", headers=a | {"Last-Event-ID": str(first_id)}
        ).text
        assert "event: queued" not in resumed and "event: succeeded" in resumed
        report = client.get(f"/runs/{run_id}/report", headers=a)
        assert report.status_code == 200 and "Evidence ledger" in report.text
        assert client.get("/documents", headers=b).json() == []


def test_api_validation_cancel_and_body_limit(tmp_path):
    settings = Settings(database=tmp_path / "api.sqlite3", max_pdf_bytes=10)
    with TestClient(create_app(settings)) as client:
        assert client.post("/runs", json={"topic": "test"}).status_code == 422
        assert client.post("/runs", json={"topic": "x", "discover": True}).status_code == 422
        assert client.post("/runs", json={"topic": "test", "discover": True}).status_code == 403
        assert client.post("/documents/text", json={"title": "a", "text": " "}).status_code == 422
        assert (
            client.post(
                "/documents/pdf", data={"title": "x"}, files={"file": ("evil.pdf", b"not a pdf")}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/documents/pdf", data={"title": "x"}, files={"file": ("big.pdf", b"%PDF-" * 10)}
            ).status_code
            == 413
        )
        doc = client.post("/documents/text", json={"title": "x", "text": "evidence"}).json()
        run_id = client.post(
            "/runs", json={"topic": "evidence", "document_ids": [doc["id"]]}
        ).json()["id"]
        assert client.post(f"/runs/{run_id}/cancel").json()["status"] == "cancelled"
        assert client.get("/runs/missing").status_code == 404


def test_oversize_chunked_body_rejected(tmp_path):
    settings = Settings(database=tmp_path / "api.sqlite3", max_pdf_bytes=1)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/documents/text",
            content=iter([b"x" * 70000]),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
