from __future__ import annotations

import argparse
import json
import logging
from dataclasses import replace
from pathlib import Path

from cite_paper.config import Settings
from cite_paper.demo import default_request, seed_demo
from cite_paper.domain import digest
from cite_paper.evaluation import evaluate, save_evaluation, synthetic_evaluation
from cite_paper.ingest import download_arxiv, ingest_file, ingest_pdf
from cite_paper.network import cached_search
from cite_paper.store import Store
from cite_paper.tools.arxiv import ArxivError
from cite_paper.worker import Worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-grounded CS paper research backend.")
    parser.add_argument("--database", type=Path, help="Override CITEPAPER_DATABASE.")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Search arXiv metadata (network).")
    search.add_argument("query")
    search.add_argument("--max-results", type=int, default=5)
    ingest = sub.add_parser("import", help="Import a UTF-8 text/Markdown file or PDF.")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--title")
    ingest.add_argument("--tenant", default="demo")
    run = sub.add_parser("run", help="Submit a durable research job.")
    run.add_argument("topic")
    run.add_argument("--documents", nargs="*", default=[])
    run.add_argument("--discover", action="store_true")
    run.add_argument("--budget", type=int, default=600)
    run.add_argument("--strategy", choices=["topk", "neighbors", "rse"], default="topk")
    run.add_argument(
        "--wait", action="store_true", help="Process queued jobs until this run finishes."
    )
    run.add_argument("--output", type=Path, default=Path("output/report.md"))
    run.add_argument("--tenant", default="demo")
    demo = sub.add_parser("demo", help="Offline end-to-end run using clearly synthetic fixtures.")
    demo.add_argument("--output", type=Path, default=Path("output/demo-report.md"))
    worker = sub.add_parser("worker", help="Run the durable job worker.")
    worker.add_argument("--once", action="store_true")
    serve = sub.add_parser("serve", help="Start the HTTP API; use a separate worker in deployment.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--embedded-worker", action="store_true")
    evaluation = sub.add_parser("evaluate", help="Compare retrieval baselines under equal budgets.")
    evaluation.add_argument(
        "--manifest", type=Path, help="Optional pinned real-paper corpus manifest."
    )
    evaluation.add_argument("--budget", type=int, default=240)
    evaluation.add_argument("--repeats", type=int, default=5)
    evaluation.add_argument("--output", type=Path, default=Path("output/evaluation"))
    corpus = sub.add_parser("fetch-corpus", help="Download the arXiv PDFs in a corpus manifest.")
    corpus.add_argument("manifest", type=Path)
    return parser


def _wait(store, settings, run, output):
    worker = Worker(store, settings)
    while store.get_run(run["tenant"], run["id"])["status"] not in {
        "succeeded",
        "failed",
        "cancelled",
    }:
        if not worker.once():
            raise RuntimeError("Run is owned by another worker; use the API to poll its status")
    finished = store.get_run(run["tenant"], run["id"])
    if finished["status"] != "succeeded":
        raise RuntimeError("Run ended as " + finished["status"] + ": " + str(finished["error"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(finished["result"]["markdown"], encoding="utf-8")
    output.with_suffix(".json").write_text(
        json.dumps(finished["result"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "run_id": run["id"],
        "status": finished["status"],
        "report": str(output.resolve()),
        "verification": finished["result"]["verification"],
    }


def _manifest_corpus(store, manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = []
    aliases = {}
    for item in manifest["documents"]:
        path = manifest_path.parent / item["file"]
        data = path.read_bytes()
        if item.get("sha256") and digest(data) != item["sha256"]:
            raise ValueError("Corpus PDF hash mismatch: " + item["key"])
        doc = ingest_pdf(
            store,
            "real-evaluation",
            item["title"],
            data,
            source="https://arxiv.org/abs/" + item["arxiv_id"],
        )
        ids.append(doc["id"])
        aliases[item["key"]] = doc["id"]
    cases = [{**case, "document_id": aliases[case["document"]]} for case in manifest["cases"]]
    return ids, cases, manifest.get("label", "real-paper-regression")


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        settings = Settings.from_env()
        if args.database:
            settings = replace(settings, database=args.database)
        if args.command == "search":
            papers = cached_search(
                Store(settings.database), args.query, max_results=args.max_results
            )
            result = {
                "query": args.query,
                "count": len(papers),
                "papers": [p.to_dict() for p in papers],
            }
        elif args.command == "serve":
            import uvicorn

            from cite_paper.api import create_app

            settings = replace(settings, embedded_worker=args.embedded_worker)
            if args.host not in {"127.0.0.1", "localhost", "::1"} and not settings.api_keys:
                raise ValueError("Refusing public bind without CITEPAPER_API_KEYS")
            uvicorn.run(create_app(settings), host=args.host, port=args.port)
            return 0
        elif args.command == "fetch-corpus":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            for item in manifest["documents"]:
                path = (args.manifest.parent / item["file"]).resolve()
                if not path.exists():
                    # The manifest is trusted local configuration, not an API request.
                    data = download_arxiv(item["arxiv_id"])
                    if item.get("sha256") and digest(data) != item["sha256"]:
                        raise ValueError("Downloaded PDF hash mismatch: " + item["key"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    import time

                    time.sleep(3)  # Respect arXiv's request interval.
            result = {"downloaded_or_cached": len(manifest["documents"])}
        else:
            store = Store(settings.database)
            if args.command == "import":
                document = ingest_file(store, args.tenant, args.path, args.title)
                result = {k: v for k, v in document.items() if k != "pages"}
            elif args.command == "worker":
                if args.once:
                    result = {"processed": Worker(store, settings).once()}
                else:
                    Worker(store, settings).serve()
                    return 0
            elif args.command == "demo":
                settings = replace(settings, llm_base_url="", llm_model="", allow_arxiv=False)
                ids = seed_demo(store)
                run = store.create_run("demo", default_request(ids), None)
                result = _wait(store, settings, run, args.output)
            elif args.command == "run":
                from cite_paper.api import ResearchRequest

                request = ResearchRequest(
                    topic=args.topic,
                    document_ids=args.documents,
                    discover=args.discover,
                    budget=args.budget,
                    strategy=args.strategy,
                ).model_dump()
                run = store.create_run(args.tenant, request, None)
                result = (
                    _wait(store, settings, run, args.output)
                    if args.wait
                    else {"run_id": run["id"], "status": run["status"]}
                )
            elif args.command == "evaluate":
                if args.manifest:
                    ids, cases, label = _manifest_corpus(store, args.manifest)
                    result = evaluate(
                        store,
                        "real-evaluation",
                        ids,
                        cases,
                        budget=args.budget,
                        repeats=args.repeats,
                        label=label,
                    )
                else:
                    result = synthetic_evaluation(store, budget=args.budget, repeats=args.repeats)
                save_evaluation(result, args.output)
                result = {
                    "output": str(args.output.resolve()),
                    "case_count": result["case_count"],
                    "results": [
                        {k: v for k, v in r.items() if k != "cases"} for r in result["results"]
                    ],
                }
            else:
                raise ValueError("Unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ArxivError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
