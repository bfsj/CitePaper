"""Run actual model inference against the real corpus; store auditable artifacts, not a mock."""

import json
from dataclasses import replace
from pathlib import Path

from cite_paper.cli import _manifest_corpus, _wait
from cite_paper.config import Settings
from cite_paper.demo import default_request
from cite_paper.store import Store


def main():
    settings = replace(Settings.from_env(), llm_timeout=300)
    if not settings.llm_base_url:
        raise SystemExit("Set CITEPAPER_LLM_BASE_URL and CITEPAPER_LLM_MODEL")
    store = Store(settings.database)
    ids, _, _ = _manifest_corpus(store, Path("examples/real_corpus.json"))
    request = default_request(ids, "Compare how RAG and Self-RAG decide when to retrieve evidence.")
    request.update(strategy="topk", budget=400)
    run = store.create_run("real-evaluation", request, None)
    result = _wait(store, settings, run, Path("output/model-smoke-report.md"))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
