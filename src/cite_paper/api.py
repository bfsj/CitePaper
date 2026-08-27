from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from cite_paper.tools.arxiv import ArxivError, search_arxiv

app = FastAPI(title="CitePaper API", version="0.1.0")


class PaperSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    max_results: int = Field(default=5, ge=1, le=50)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/papers/search")
def search_papers(request: PaperSearchRequest) -> dict[str, object]:
    try:
        papers = search_arxiv(request.query, max_results=request.max_results)
    except (ArxivError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "query": request.query,
        "count": len(papers),
        "papers": [paper.to_dict() for paper in papers],
    }
