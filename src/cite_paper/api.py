from __future__ import annotations

import asyncio
import hmac
import json
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cite_paper.config import Settings
from cite_paper.domain import digest
from cite_paper.ingest import ingest_pages, ingest_pdf
from cite_paper.network import cached_search
from cite_paper.retrieval import retrieve
from cite_paper.store import TERMINAL, Conflict, QueueFull, Store
from cite_paper.tools.arxiv import ArxivError
from cite_paper.worker import Worker


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PaperSearchRequest(StrictModel):
    query: str = Field(min_length=2, max_length=300)
    max_results: int = Field(default=5, ge=1, le=50)


class TextDocument(StrictModel):
    title: str = Field(min_length=1, max_length=250)
    text: str = Field(min_length=1, max_length=1_000_000)


class ResearchRequest(StrictModel):
    topic: str = Field(min_length=2, max_length=500)
    document_ids: list[str] = Field(default_factory=list, max_length=30)
    discover: bool = False
    budget: int = Field(default=600, ge=100, le=4000)
    strategy: Literal["topk", "neighbors", "rse"] = "topk"
    auto_context: bool = True

    @model_validator(mode="after")
    def needs_source(self):
        if not self.document_ids and not self.discover:
            raise ValueError("Provide document_ids or set discover=true")
        return self


class RetrievalRequest(StrictModel):
    query: str = Field(min_length=2, max_length=500)
    document_ids: list[str] = Field(min_length=1, max_length=30)
    budget: int = Field(default=600, ge=100, le=4000)
    strategy: Literal["topk", "neighbors", "rse"] = "topk"
    auto_context: bool = True


class ReviewRequest(StrictModel):
    report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]
    note: str = Field(min_length=3, max_length=2000)


def tenant(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
    keys = request.app.state.settings.api_keys
    if not keys:
        return "demo"  # LOCAL DEMO ONLY; deployment requires keys, TLS, and a proxy.
    token = (authorization or "").removeprefix("Bearer ")
    for key, tenant_id in keys.items():
        if hmac.compare_digest(key.encode(), token.encode()):
            return tenant_id
    raise HTTPException(401, "Valid Bearer token required")


Tenant = Annotated[str, Depends(tenant)]


def repository(request: Request) -> Store:
    return request.app.state.store


Repo = Annotated[Store, Depends(repository)]


class BodyLimit:
    """Bound chunked as well as Content-Length bodies before multipart/JSON parsing."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > self.max_bytes:
                    raise HTTPException(413, "Request body exceeds size limit")
            return message

        await self.app(scope, bounded_receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        application.state.store = Store(settings.database)
        stop = threading.Event()
        worker = None
        if settings.embedded_worker:
            worker = threading.Thread(
                target=Worker(application.state.store, settings).serve, args=(stop,), daemon=True
            )
            worker.start()
        try:
            yield
        finally:
            stop.set()
            if worker:
                await asyncio.to_thread(worker.join, 2)

    application = FastAPI(title="CitePaper API", version="0.2.0", lifespan=lifespan)
    application.state.settings = settings
    application.add_middleware(BodyLimit, max_bytes=settings.max_pdf_bytes + 64 * 1024)

    @application.middleware("http")
    async def request_id(request, call_next):
        response = await call_next(request)
        response.headers["X-Request-ID"] = uuid.uuid4().hex
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.get("/health")
    def health():
        return {
            "status": "ok",
            "version": "0.2.0",
            "auth": "api-key" if settings.api_keys else "local-demo",
            "provider": "model-assisted" if settings.llm_base_url else "extractive",
        }

    @application.get("/ready")
    def ready(store: Repo):
        with store.connect() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ready"}

    @application.post("/papers/search")
    def papers(request: PaperSearchRequest, user: Tenant, store: Repo):
        if not settings.allow_arxiv:
            raise HTTPException(403, "Set CITEPAPER_ALLOW_ARXIV=1 for network access")
        try:
            found = cached_search(store, request.query, max_results=request.max_results)
        except (ArxivError, ValueError) as exc:
            raise HTTPException(502, "arXiv request failed") from exc
        return {"query": request.query, "count": len(found), "papers": [p.to_dict() for p in found]}

    @application.post("/documents/text", status_code=201)
    def add_text(body: TextDocument, user: Tenant, store: Repo):
        try:
            doc = ingest_pages(store, user, body.title, [body.text])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {k: v for k, v in doc.items() if k != "pages"}

    @application.post("/documents/pdf", status_code=201)
    def add_pdf(
        user: Tenant,
        store: Repo,
        title: Annotated[str, Form(max_length=250)],
        file: Annotated[UploadFile, File()],
    ):
        data = file.file.read(settings.max_pdf_bytes + 1)
        if len(data) > settings.max_pdf_bytes:
            raise HTTPException(413, "PDF exceeds size limit")
        try:
            doc = ingest_pdf(store, user, title, data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {k: v for k, v in doc.items() if k != "pages"}

    @application.get("/documents")
    def documents(user: Tenant, store: Repo):
        return [{k: v for k, v in d.items() if k != "pages"} for d in store.documents(user)]

    @application.get("/stats")
    def stats(user: Tenant, store: Repo):
        return store.stats(user)

    @application.get("/documents/{document_id}")
    def document(document_id: str, user: Tenant, store: Repo):
        docs = store.documents(user, [document_id])
        if not docs:
            raise HTTPException(404, "Document not found")
        return docs[0]

    def validate_documents(store, user, ids):
        if set(ids) != {d["id"] for d in store.documents(user, ids)}:
            raise HTTPException(404, "Document not found")

    @application.post("/retrieve")
    def evidence(body: RetrievalRequest, user: Tenant, store: Repo):
        validate_documents(store, user, body.document_ids)
        return {
            "evidence": [
                e.to_dict()
                for e in retrieve(
                    store,
                    user,
                    body.document_ids,
                    body.query,
                    budget=body.budget,
                    strategy=body.strategy,
                    auto_context=body.auto_context,
                )
            ]
        }

    @application.post("/runs", status_code=202)
    def submit(
        body: ResearchRequest,
        user: Tenant,
        store: Repo,
        idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
    ):
        if body.discover and not settings.allow_arxiv:
            raise HTTPException(403, "arXiv discovery is disabled")
        validate_documents(store, user, body.document_ids)
        try:
            run = store.create_run(user, body.model_dump(), idempotency_key, settings.max_pending)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except QueueFull as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "5"}) from exc
        return _public_run(run)

    def require_run(store, user, run_id):
        run = store.get_run(user, run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run

    @application.get("/runs/{run_id}")
    def status(run_id: str, user: Tenant, store: Repo):
        return _public_run(require_run(store, user, run_id))

    @application.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, user: Tenant, store: Repo):
        require_run(store, user, run_id)
        return _public_run(store.cancel(user, run_id))

    @application.get("/runs/{run_id}/report", response_class=PlainTextResponse)
    def report(run_id: str, user: Tenant, store: Repo):
        run = require_run(store, user, run_id)
        if run["status"] != "succeeded":
            raise HTTPException(409, "Report is not available yet")
        return PlainTextResponse(run["result"]["markdown"], media_type="text/markdown")

    @application.get("/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        user: Tenant,
        store: Repo,
        last_event_id: Annotated[int, Header(ge=0)] = 0,
    ):
        require_run(store, user, run_id)

        async def stream():
            cursor = last_event_id
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(store.events, user, run_id, cursor)
                if batch:
                    for event in batch:
                        cursor = event["id"]
                        yield (
                            f"id: {cursor}\nevent: {event['kind']}\ndata: "
                            + json.dumps(event["data"], ensure_ascii=False)
                            + "\n\n"
                        )
                    continue
                run = await asyncio.to_thread(store.get_run, user, run_id)
                if run["status"] in TERMINAL:
                    # Terminal transition could have committed between the previous two reads.
                    final = await asyncio.to_thread(store.events, user, run_id, cursor)
                    if not final:
                        break
                    continue
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.post("/runs/{run_id}/reviews", status_code=201)
    def add_review(run_id: str, body: ReviewRequest, user: Tenant, store: Repo):
        require_run(store, user, run_id)
        try:
            return store.review(user, run_id, body.report_hash, body.decision, body.note)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @application.get("/runs/{run_id}/reviews")
    def reviews(run_id: str, user: Tenant, store: Repo):
        require_run(store, user, run_id)
        return store.reviews(user, run_id)

    return application


def _public_run(run):
    return {k: run[k] for k in ("id", "status", "attempts", "created", "updated", "error")} | {
        "stage": run["checkpoint"].get("stage"),
        "result": run["result"],
        "report_hash": digest(json.dumps(run["result"], ensure_ascii=False))
        if run["result"]
        else None,
    }


app = create_app()
