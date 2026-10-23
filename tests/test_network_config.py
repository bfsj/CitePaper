from types import SimpleNamespace

import httpx
import pytest
from test_arxiv import ATOM_FIXTURE

from cite_paper import network
from cite_paper.config import Settings
from cite_paper.demo import default_request
from cite_paper.ingest import ingest_pages
from cite_paper.store import Store
from cite_paper.worker import Worker


def test_metadata_cache_persists_across_store_instances(store, monkeypatch):
    calls = []
    original_client = httpx.Client

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=ATOM_FIXTURE)

    monkeypatch.setattr(network, "pace_arxiv", lambda _: None)
    monkeypatch.setattr(
        network.httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler)),
    )
    assert network.cached_search(store, "retrieval")[0].arxiv_id == "2401.12345v2"
    assert network.cached_search(Store(store.path), "retrieval")
    assert len(calls) == 1
    with store.connect(write=True) as db:
        db.execute("UPDATE network_cache SET expires=0")
    assert network.cached_search(store, "retrieval")
    assert len(calls) == 2


def test_network_pacing_shared_state(store):
    network.pace_arxiv(store, interval=0)
    network.pace_arxiv(Store(store.path), interval=0)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM network_pacing").fetchone()[0] == 1


@pytest.mark.parametrize("value", ["[]", '{"short":"a"}', '{"0123456789abcdef":1}'])
def test_settings_reject_invalid_key_mapping(monkeypatch, value):
    monkeypatch.setenv("CITEPAPER_API_KEYS", value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_require_both_model_and_endpoint(monkeypatch):
    monkeypatch.setenv("CITEPAPER_API_KEYS", "{}")
    monkeypatch.setenv("CITEPAPER_LLM_BASE_URL", "http://localhost:8011/v1")
    monkeypatch.delenv("CITEPAPER_LLM_MODEL", raising=False)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_arxiv_discovery_node_is_wired_into_graph(store, monkeypatch):
    from cite_paper import workflow

    calls = []
    monkeypatch.setattr(
        workflow,
        "cached_search",
        lambda *a, **kw: [SimpleNamespace(arxiv_id="2005.11401v4", title="Discovered study")],
    )
    monkeypatch.setattr(workflow, "pace_arxiv", lambda *a: None)
    monkeypatch.setattr(workflow, "download_arxiv", lambda *a, **kw: calls.append(a[0]) or b"PDF")
    monkeypatch.setattr(
        workflow,
        "ingest_pdf",
        lambda repo, tenant, title, data, **kw: ingest_pages(
            repo, tenant, title, ["Retrieved evidence source."], **kw
        ),
    )
    request = default_request([], "retrieved evidence") | {"discover": True}
    run = store.create_run("demo", request, None)
    Worker(store, Settings(database=store.path, allow_arxiv=True)).once()
    assert store.get_run("demo", run["id"])["status"] == "succeeded"
    assert calls == ["2005.11401v4"]
