"""SQLite WAL repository, durable queue, event log, and application-level checkpoints.

Every write by a worker is fenced by its unique lease token AND the lease expiry.
Checkpoint and progress event commit together. One connection per operation/thread.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from cite_paper.domain import Chunk, digest

TERMINAL = {"succeeded", "failed", "cancelled"}


class LeaseLost(RuntimeError):
    pass


class Conflict(ValueError):
    pass


class QueueFull(RuntimeError):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    tenant TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,
                    source TEXT NOT NULL, hash TEXT NOT NULL, pages TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY (tenant,id));
                CREATE TABLE IF NOT EXISTS chunks (
                    tenant TEXT NOT NULL, id TEXT NOT NULL, document_id TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY (tenant,id),
                    FOREIGN KEY (tenant,document_id) REFERENCES documents(tenant,id));
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, request TEXT NOT NULL,
                    status TEXT NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}',
                    result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT, lease_until REAL, cancel INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                    UNIQUE (tenant,idempotency_key));
                CREATE INDEX IF NOT EXISTS runs_queue ON runs(status,created);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id));
                CREATE INDEX IF NOT EXISTS run_events ON events(run_id,id);
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    tenant TEXT NOT NULL, report_hash TEXT NOT NULL, decision TEXT NOT NULL,
                    note TEXT NOT NULL, created REAL NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id));
                CREATE INDEX IF NOT EXISTS chunks_documents ON chunks(tenant,document_id);
            """)

    @contextmanager
    def connect(self, *, write: bool = False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db, run_id: str, kind: str, data: dict):
        db.execute(
            "INSERT INTO events(run_id,kind,data,created) VALUES (?,?,?,?)",
            (run_id, kind, json.dumps(data, ensure_ascii=False), time.time()),
        )

    def add_document(self, tenant: str, document: dict, chunks: list[Chunk]) -> dict:
        with self.connect(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,?)",
                (
                    tenant,
                    document["id"],
                    document["title"],
                    document["source"],
                    document["hash"],
                    json.dumps(document["pages"], ensure_ascii=False),
                    time.time(),
                ),
            )
            db.executemany(
                "INSERT OR IGNORE INTO chunks VALUES (?,?,?,?)",
                [
                    (tenant, c.id, c.document_id, json.dumps(c.to_dict(), ensure_ascii=False))
                    for c in chunks
                ],
            )
        return document

    def documents(self, tenant: str, ids: list[str] | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM documents WHERE tenant=? ORDER BY created,id", (tenant,)
            ).fetchall()
        selected = [dict(r) for r in rows if ids is None or r["id"] in ids]
        for doc in selected:
            doc["pages"] = json.loads(doc["pages"])
            doc.pop("tenant")
        return selected

    def chunks(self, tenant: str, ids: list[str]) -> list[Chunk]:
        with self.connect() as db:
            rows = db.execute("SELECT document_id,payload FROM chunks WHERE tenant=?", (tenant,))
            return [Chunk(**json.loads(r["payload"])) for r in rows if r["document_id"] in ids]

    def create_run(self, tenant: str, request: dict, key: str | None, max_pending=30) -> dict:
        encoded = json.dumps(request, sort_keys=True, ensure_ascii=False)
        with self.connect(write=True) as db:
            if key:
                old = db.execute(
                    "SELECT * FROM runs WHERE tenant=? AND idempotency_key=?", (tenant, key)
                ).fetchone()
                if old:
                    if old["request"] != encoded:
                        raise Conflict("Idempotency key already used with a different request")
                    return self._run(old)
            count = db.execute(
                "SELECT COUNT(*) FROM runs WHERE tenant=? AND status IN ('queued','running')",
                (tenant,),
            ).fetchone()[0]
            if count >= max_pending:
                raise QueueFull("Tenant queue is full")
            run_id = uuid.uuid4().hex
            now = time.time()
            db.execute(
                "INSERT INTO runs(id,tenant,request,status,idempotency_key,created,updated) "
                "VALUES (?,?,?,'queued',?,?,?)",
                (run_id, tenant, encoded, key, now, now),
            )
            self._event(db, run_id, "queued", {})
            return self._run(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    @staticmethod
    def _run(row) -> dict:
        result = dict(row)
        for key in ("request", "checkpoint", "result"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        result.pop("lease_token", None)
        return result

    def get_run(self, tenant: str, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE tenant=? AND id=?", (tenant, run_id)
            ).fetchone()
        return self._run(row) if row else None

    def claim(self, lease_seconds: float = 60, max_attempts: int = 3) -> dict | None:
        now = time.time()
        with self.connect(write=True) as db:
            stale = db.execute(
                "SELECT * FROM runs WHERE status='running' AND lease_until < ?", (now,)
            ).fetchall()
            for row in stale:
                status = (
                    "cancelled"
                    if row["cancel"]
                    else ("failed" if row["attempts"] >= max_attempts else "queued")
                )
                db.execute(
                    "UPDATE runs SET status=?,lease_token=NULL,lease_until=NULL,updated=?,"
                    "error=? WHERE id=?",
                    (status, now, "Worker lease expired", row["id"]),
                )
                self._event(
                    db,
                    row["id"],
                    "recovered" if status == "queued" else status,
                    {"reason": "lease_expired"},
                )
            row = db.execute(
                "SELECT * FROM runs WHERE status='queued' AND cancel=0 ORDER BY created LIMIT 1"
            ).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute(
                "UPDATE runs SET status='running',lease_token=?,lease_until=?,attempts=attempts+1,"
                "updated=?,error=NULL WHERE id=?",
                (token, now + lease_seconds, now, row["id"]),
            )
            self._event(db, row["id"], "started", {"attempt": row["attempts"] + 1})
            result = self._run(db.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())
            result["lease_token"] = token
            return result

    @staticmethod
    def _owned(db, run_id: str, token: str, *, allow_cancel: bool = False):
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if (
            not row
            or row["status"] != "running"
            or row["lease_token"] != token
            or row["lease_until"] <= time.time()
        ):
            raise LeaseLost("Run lease no longer belongs to this worker")
        if row["cancel"] and not allow_cancel:
            raise InterruptedError("Run cancelled")
        return row

    def heartbeat(self, run_id: str, token: str, lease_seconds: float):
        with self.connect(write=True) as db:
            self._owned(db, run_id, token)
            db.execute(
                "UPDATE runs SET lease_until=? WHERE id=?", (time.time() + lease_seconds, run_id)
            )

    def guard(self, run_id: str, token: str):
        with self.connect() as db:
            self._owned(db, run_id, token)

    def checkpoint(self, run_id: str, token: str, state: dict):
        with self.connect(write=True) as db:
            self._owned(db, run_id, token)
            db.execute(
                "UPDATE runs SET checkpoint=?,updated=? WHERE id=?",
                (json.dumps(state, ensure_ascii=False), time.time(), run_id),
            )
            self._event(
                db,
                run_id,
                "checkpoint",
                {
                    "stage": state["stage"],
                    "duration_ms": state.get("timings_ms", {}).get(state["stage"]),
                },
            )

    def finish(self, run_id: str, token: str, *, result=None, error: str | None = None):
        with self.connect(write=True) as db:
            row = self._owned(db, run_id, token, allow_cancel=True)
            status = "cancelled" if row["cancel"] else ("failed" if error else "succeeded")
            db.execute(
                "UPDATE runs SET status=?,result=?,error=?,lease_token=NULL,lease_until=NULL,"
                "updated=? WHERE id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if status == "succeeded" else None,
                    error if status == "failed" else None,
                    time.time(),
                    run_id,
                ),
            )
            self._event(db, run_id, status, {})

    def cancel(self, tenant: str, run_id: str) -> dict | None:
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT * FROM runs WHERE tenant=? AND id=?", (tenant, run_id)
            ).fetchone()
            if not row:
                return None
            if row["status"] not in TERMINAL:
                status = "cancelled" if row["status"] == "queued" else row["status"]
                db.execute(
                    "UPDATE runs SET cancel=1,status=?,updated=? WHERE id=?",
                    (status, time.time(), run_id),
                )
                self._event(
                    db, run_id, "cancelled" if status == "cancelled" else "cancel_requested", {}
                )
        return self.get_run(tenant, run_id)

    def events(self, tenant: str, run_id: str, after: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT e.* FROM events e JOIN runs r ON e.run_id=r.id "
                "WHERE r.tenant=? AND r.id=? AND e.id>? ORDER BY e.id LIMIT 200",
                (tenant, run_id, after),
            ).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def review(self, tenant: str, run_id: str, report_hash: str, decision: str, note: str):
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT * FROM runs WHERE tenant=? AND id=?", (tenant, run_id)
            ).fetchone()
            if not row or row["status"] != "succeeded":
                raise Conflict("A completed report is required")
            if digest(row["result"]) != report_hash:
                raise Conflict("Report hash changed; read the latest report before reviewing")
            verification = json.loads(row["result"])["verification"]
            if decision == "approve" and not (
                verification["source_spans_valid"] and verification["citation_links_valid"]
            ):
                raise Conflict("Cannot approve a report with invalid source spans or citations")
            if decision not in {"approve", "reject"}:
                raise ValueError("Unknown review decision")
            db.execute(
                "INSERT INTO reviews(run_id,tenant,report_hash,decision,note,created) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, tenant, report_hash, decision, note, time.time()),
            )
            self._event(db, run_id, "reviewed", {"decision": decision, "report_hash": report_hash})
        return self.reviews(tenant, run_id)[-1]

    def reviews(self, tenant: str, run_id: str) -> list[dict]:
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM reviews WHERE tenant=? AND run_id=? ORDER BY id",
                    (tenant, run_id),
                )
            ]

    def stats(self, tenant: str) -> dict:
        with self.connect() as db:
            statuses = {
                r["status"]: r["n"]
                for r in db.execute(
                    "SELECT status,COUNT(*) AS n FROM runs WHERE tenant=? GROUP BY status",
                    (tenant,),
                )
            }
            documents = db.execute(
                "SELECT COUNT(*) FROM documents WHERE tenant=?", (tenant,)
            ).fetchone()[0]
        return {"runs": statuses, "documents": documents}
