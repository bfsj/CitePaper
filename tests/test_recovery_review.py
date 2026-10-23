import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from cite_paper.api import create_app
from cite_paper.config import Settings
from cite_paper.demo import default_request, seed_demo
from cite_paper.domain import digest
from cite_paper.store import Conflict
from cite_paper.worker import Worker
from cite_paper.workflow import ResearchWorkflow


def test_real_child_process_crash_recovers_checkpoint(store):
    ids = seed_demo(store)
    request = default_request(ids)
    run = store.create_run("demo", request, None)
    code = (
        "import os,sys; from pathlib import Path; from cite_paper.store import Store; "
        "from cite_paper.workflow import ResearchWorkflow; from cite_paper.config import Settings; "
        "s=Store(Path(sys.argv[1])); r=s.claim(lease_seconds=60); "
        "w=ResearchWorkflow(s,Settings(database=s.path),r); "
        "state={'request':r['request'],'warnings':[]}; w._node('plan')(state); os._exit(23)"
    )
    env = os.environ | {"PYTHONPATH": str(__import__("pathlib").Path("src").resolve())}
    child = subprocess.run(
        [sys.executable, "-c", code, str(store.path)], env=env, timeout=30, capture_output=True
    )
    assert child.returncode == 23, child.stderr.decode(errors="replace")
    assert store.get_run("demo", run["id"])["checkpoint"]["stage"] == "plan"
    with store.connect(write=True) as db:
        db.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, run["id"]))
    assert Worker(store, Settings(database=store.path)).once()
    result = store.get_run("demo", run["id"])
    assert result["status"] == "succeeded" and result["attempts"] == 2


def test_heartbeat_keeps_slow_node_owned(store, monkeypatch):
    run = store.create_run("demo", default_request(seed_demo(store)), None)
    original = ResearchWorkflow.plan

    def slow_plan(self, state):
        time.sleep(0.5)
        return original(self, state)

    monkeypatch.setattr(ResearchWorkflow, "plan", slow_plan)
    Worker(store, Settings(database=store.path, lease_seconds=0.3)).once()
    assert store.get_run("demo", run["id"])["status"] == "succeeded"


def test_review_hash_binding_and_cross_tenant_isolation(tmp_path):
    settings = Settings(database=tmp_path / "review.sqlite3")
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        run = store.create_run("demo", default_request(seed_demo(store)), None)
        Worker(store, settings).once()
        status = client.get("/runs/" + run["id"]).json()
        review = {
            "report_hash": status["report_hash"],
            "decision": "approve",
            "note": "Reviewed the evidence spans manually.",
        }
        url = "/runs/" + run["id"] + "/reviews"
        assert client.post(url, json=review | {"report_hash": "0" * 64}).status_code == 409
        assert client.post(url, json=review).status_code == 201
        assert len(client.get(url).json()) == 1
        assert store.reviews("other", run["id"]) == []
        assert client.get("/stats").json()["runs"]["succeeded"] == 1
        # Approval is a human action in the audit log, not an automatic entailment proof.
        assert (
            client.get("/runs/" + run["id"]).json()["result"]["verification"]["semantic_entailment"]
            == "not_evaluated"
        )


def test_bad_citations_cannot_be_approved(store):
    store.create_run("demo", {}, None)
    run = store.claim()
    report = {"verification": {"source_spans_valid": True, "citation_links_valid": False}}
    store.finish(run["id"], run["lease_token"], result=report)
    report_hash = digest(json.dumps(report, ensure_ascii=False))
    with pytest.raises(Conflict):
        store.review("demo", run["id"], report_hash, "approve", "I reviewed it")
    assert (
        store.review("demo", run["id"], report_hash, "reject", "Invalid citations")["decision"]
        == "reject"
    )
