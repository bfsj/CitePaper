import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cite_paper.config import Settings
from cite_paper.demo import default_request, seed_demo
from cite_paper.store import Conflict, LeaseLost, QueueFull, Store
from cite_paper.worker import Worker
from cite_paper.workflow import ResearchWorkflow


def test_idempotency_and_queue_backpressure(store):
    request = {"topic": "a"}
    a = store.create_run("a", request, "key", max_pending=1)
    assert store.create_run("a", request, "key", max_pending=1)["id"] == a["id"]
    with pytest.raises(Conflict):
        store.create_run("a", {"topic": "b"}, "key")
    with pytest.raises(QueueFull):
        store.create_run("a", request, None, max_pending=1)
    assert store.get_run("b", a["id"]) is None
    assert store.cancel("b", a["id"]) is None
    assert store.events("b", a["id"]) == []


def test_concurrent_workers_claim_once(store):
    run = store.create_run("a", {}, None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(8)))
    assert sum(c is not None for c in claims) == 1
    assert next(c for c in claims if c)["id"] == run["id"]


def test_expired_lease_recovery_fences_stale_worker(store):
    store.create_run("a", {}, None)
    old = store.claim(lease_seconds=-1)
    new = store.claim(lease_seconds=60)
    assert old["id"] == new["id"] and new["attempts"] == 2
    with pytest.raises(LeaseLost):
        store.checkpoint(old["id"], old["lease_token"], {"stage": "plan"})
    with pytest.raises(LeaseLost):
        store.finish(old["id"], old["lease_token"], result={})
    store.finish(new["id"], new["lease_token"], result={"ok": True})
    assert store.get_run("a", new["id"])["status"] == "succeeded"


def test_exhausted_lease_retries_become_failed(store):
    store.create_run("a", {}, None)
    run = store.claim(lease_seconds=-1)
    assert store.claim(max_attempts=1) is None
    assert store.get_run("a", run["id"])["status"] == "failed"


def test_queued_and_running_cancellation(store):
    queued = store.create_run("a", {}, None)
    assert store.cancel("a", queued["id"])["status"] == "cancelled"
    assert store.claim() is None
    store.create_run("a", {}, None)
    run = store.claim()
    store.cancel("a", run["id"])
    with pytest.raises(InterruptedError):
        store.guard(run["id"], run["lease_token"])
    store.finish(run["id"], run["lease_token"])
    assert store.get_run("a", run["id"])["status"] == "cancelled"


def test_offline_workflow_and_event_replay(store):
    ids = seed_demo(store)
    run = store.create_run("demo", default_request(ids), None)
    assert Worker(store, Settings(database=store.path)).once()
    result = store.get_run("demo", run["id"])
    assert result["status"] == "succeeded"
    assert result["checkpoint"]["stage"] == "report"
    assert result["result"]["verification"]["citation_links_valid"]
    assert result["result"]["retrieval_units"] <= 600
    events = store.events("demo", run["id"])
    assert [e["kind"] for e in events] == ["queued", "started"] + ["checkpoint"] * 6 + ["succeeded"]
    assert store.events("demo", run["id"], events[3]["id"]) == events[4:]


def test_checkpoint_survives_new_store_and_skips_completed_plan(store, monkeypatch):
    ids = seed_demo(store)
    initial = store.create_run("demo", default_request(ids), None)
    old = store.claim(lease_seconds=60)
    state = {
        "request": initial["request"],
        "stage": "plan",
        "questions": ["evidence"],
        "document_ids": ids,
        "warnings": [],
    }
    store.checkpoint(old["id"], old["lease_token"], state)
    with store.connect(write=True) as db:
        db.execute("UPDATE runs SET lease_until=? WHERE id=?", (time.time() - 1, old["id"]))
    reopened = Store(store.path)

    def fail_plan(self, state):
        raise AssertionError("Completed planner must not be replayed")

    monkeypatch.setattr(ResearchWorkflow, "plan", fail_plan)
    Worker(reopened, Settings(database=store.path)).once()
    result = reopened.get_run("demo", old["id"])
    assert result["status"] == "succeeded" and result["attempts"] == 2


def test_failed_workflow_records_safe_error(store):
    run = store.create_run("demo", default_request(["missing"]), None)
    Worker(store, Settings(database=store.path)).once()
    finished = store.get_run("demo", run["id"])
    assert finished["status"] == "failed"
    assert finished["error"] == "ValueError"


def test_worker_handles_inflight_cancel(store, monkeypatch):
    import threading

    run = store.create_run("demo", default_request(seed_demo(store)), None)
    entered, release = threading.Event(), threading.Event()
    original = ResearchWorkflow.plan

    def paused(self, state):
        entered.set()
        assert release.wait(5)
        return original(self, state)

    monkeypatch.setattr(ResearchWorkflow, "plan", paused)
    worker = threading.Thread(target=Worker(store, Settings(database=store.path)).once)
    worker.start()
    try:
        assert entered.wait(5)
        store.cancel("demo", run["id"])
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert store.get_run("demo", run["id"])["status"] == "cancelled"


def test_worker_loop_can_be_stopped(store, monkeypatch):
    import threading

    stop = threading.Event()
    worker = Worker(store, Settings(database=store.path))

    def once():
        stop.set()
        return False

    monkeypatch.setattr(worker, "once", once)
    worker.serve(stop)


def test_worker_stale_owner_cannot_finish(store, monkeypatch):
    def stale(self):
        raise LeaseLost("Simulated loss of ownership")

    monkeypatch.setattr(ResearchWorkflow, "invoke", stale)
    run = store.create_run("demo", default_request(seed_demo(store)), None)
    Worker(store, Settings(database=store.path)).once()
    assert store.get_run("demo", run["id"])["status"] == "running"
