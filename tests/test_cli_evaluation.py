import json
import sys

import pytest

from cite_paper.cli import main
from cite_paper.evaluation import save_evaluation, synthetic_evaluation


def test_synthetic_evaluation_budget_and_artifact(store, tmp_path):
    result = synthetic_evaluation(store, repeats=1)
    assert result["case_count"] == 12
    assert len(result["results"]) == 4
    assert all(
        r["budget_violations"] == 0 and r["invalid_source_spans"] == 0 for r in result["results"]
    )
    save_evaluation(result, tmp_path / "results")
    assert json.loads((tmp_path / "results/metrics.json").read_text())["case_count"] == 12
    assert "not an unbiased benchmark" in (tmp_path / "results/summary.md").read_text()


@pytest.mark.parametrize("command", ["demo", "evaluate", "worker"])
def test_cli_offline_commands(tmp_path, monkeypatch, command):
    args = ["cite-paper", "--database", str(tmp_path / "cli.sqlite3"), command]
    if command in {"demo", "evaluate"}:
        output = tmp_path / ("report.md" if command == "demo" else "eval")
        args += ["--output", str(output)]
    else:
        args += ["--once"]
    monkeypatch.setattr(sys, "argv", args)
    assert main() == 0


def test_cli_import_and_run(tmp_path, monkeypatch, capsys):
    path = tmp_path / "input.txt"
    path.write_text("Evidence with immutable source spans.", encoding="utf-8")
    database = str(tmp_path / "cli.sqlite3")
    monkeypatch.setattr(sys, "argv", ["cite-paper", "--database", database, "import", str(path)])
    assert main() == 0
    doc_id = json.loads(capsys.readouterr().out)["id"]
    output = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cite-paper",
            "--database",
            database,
            "run",
            "evidence",
            "--documents",
            doc_id,
            "--wait",
            "--output",
            str(output),
        ],
    )
    assert main() == 0 and output.exists()


def test_cli_rejects_public_bind_without_auth(monkeypatch):
    monkeypatch.delenv("CITEPAPER_API_KEYS", raising=False)
    monkeypatch.setattr(sys, "argv", ["cite-paper", "serve", "--host", "0.0.0.0"])
    assert main() == 1


def test_cli_search_uses_cached_network_layer(tmp_path, monkeypatch, capsys):
    from test_arxiv import ATOM_FIXTURE

    from cite_paper import cli
    from cite_paper.tools.arxiv import parse_atom_feed

    monkeypatch.setattr(cli, "cached_search", lambda *a, **kw: parse_atom_feed(ATOM_FIXTURE))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cite-paper",
            "--database",
            str(tmp_path / "search.sqlite3"),
            "search",
            "retrieval",
            "--max-results",
            "1",
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1


def test_cli_fetch_corpus_checks_pinned_hash(tmp_path, monkeypatch):
    import time

    from cite_paper import cli
    from cite_paper.domain import digest

    data = b"%PDF-test-fixture"
    manifest = tmp_path / "corpus.json"
    manifest.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "key": "paper",
                        "arxiv_id": "2005.11401v4",
                        "file": "pdfs/paper.pdf",
                        "sha256": digest(data),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "download_arxiv", lambda _: data)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["cite-paper", "fetch-corpus", str(manifest)])
    assert main() == 0
    assert (tmp_path / "pdfs/paper.pdf").read_bytes() == data
    assert main() == 0  # Existing file is reused without another download.
