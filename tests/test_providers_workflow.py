import json

import httpx
import pytest

from cite_paper.config import Settings
from cite_paper.demo import default_request, seed_demo
from cite_paper.providers import ChatProvider, Plan, ProviderError
from cite_paper.workflow import ResearchWorkflow, _safe_block


def test_provider_retries_transient_and_validates_schema():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"questions":["retrieval evidence"]}'}}],
                "usage": {"total_tokens": 12},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = ChatProvider(Settings(llm_base_url="http://model/v1", llm_model="test"), client)
        assert provider.structured("Plan", {}, Plan)["questions"] == ["retrieval evidence"]
        assert len(calls) == 2 and provider.usage[0]["usage"]["total_tokens"] == 12


@pytest.mark.parametrize(
    "content", ["not json", '{"questions":[]}', '{"questions":["a"],"extra":1}']
)
def test_provider_rejects_invalid_output(content):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        )
    ) as client:
        provider = ChatProvider(Settings(llm_base_url="http://model/v1", llm_model="test"), client)
        with pytest.raises(ProviderError):
            provider.structured("Plan", {}, Plan)


def test_model_report_blocks_fabricated_citation_and_requires_review(store):
    ids = seed_demo(store)
    store.create_run("demo", default_request(ids), None)
    run = store.claim()

    def handler(request):
        payload = json.loads(request.content)
        user = json.loads(payload["messages"][1]["content"])
        if "evidence" not in user:
            data = {"questions": ["retrieval evidence"]}
        else:
            data = {
                "claims": [
                    {
                        "text": "A supported-looking summary",
                        "category": "method",
                        "citations": [user["evidence"][0]["id"]],
                    },
                    {
                        "text": "Fabricated conclusion",
                        "category": "experiment",
                        "citations": ["FAKE"],
                    },
                ],
                "gaps": ["Comparative experiments not available."],
            }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}]})

    settings = Settings(database=store.path, llm_base_url="http://model/v1", llm_model="test")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        state = ResearchWorkflow(store, settings, run, ChatProvider(settings, client)).invoke()
    report = state["report"]
    assert len(report["claims"]) == 1
    assert not report["verification"]["citation_links_valid"]
    assert report["verification"]["needs_review"]
    assert report["verification"]["semantic_entailment"] == "not_evaluated"
    assert "Fabricated conclusion" not in report["markdown"]


def test_empty_retrieval_returns_gap_instead_of_answer(store):
    ids = seed_demo(store)
    store.create_run("demo", default_request(ids, "zzzznoevidencezzzz"), None)
    run = store.claim()
    result = ResearchWorkflow(store, Settings(database=store.path), run).invoke()["report"]
    assert result["claims"] == [] and result["gaps"]
    assert result["verification"]["needs_review"]


def test_source_markdown_cannot_break_fence():
    payload = "```\n<script>alert(1)</script>\n```"
    block = _safe_block(payload)
    assert block.startswith("````text\n") and block.endswith("\n````")


def test_three_questions_share_budget_without_starving_later_questions(store):
    from cite_paper.ingest import ingest_pages

    ids = [
        ingest_pages(store, "demo", f"Doc {i}", [(word + " ") * 100])["id"]
        for i, word in enumerate(["alpha", "beta", "gamma"])
    ]
    request = default_request(ids) | {"strategy": "topk", "budget": 300, "auto_context": False}
    store.create_run("demo", request, None)
    run = store.claim()
    workflow = ResearchWorkflow(store, Settings(database=store.path), run)
    result = workflow.retrieve(
        {"request": request, "questions": ["alpha", "beta", "gamma"], "document_ids": ids}
    )
    assert {e["document_id"] for e in result["evidence"]} == set(ids)


def test_recovery_refuses_changed_model_configuration(store):
    request = default_request(seed_demo(store))
    store.create_run("demo", request, None)
    run = store.claim()
    state = {"request": request, "stage": "plan", "configuration": "different-config"}
    store.checkpoint(run["id"], run["lease_token"], state)
    run["checkpoint"] = state
    with pytest.raises(ValueError, match="configuration changed"):
        ResearchWorkflow(store, Settings(database=store.path), run).invoke()
