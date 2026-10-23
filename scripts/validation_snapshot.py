"""Summarize measured artifacts without publishing paper full text or credentials."""

import json
import platform
import sqlite3
import xml.etree.ElementTree as ET
from importlib.metadata import version
from pathlib import Path


def main():
    output = Path("docs/results")
    coverage = json.loads(Path("output/coverage.json").read_text(encoding="utf-8"))
    suites = ET.parse("output/test-results.xml").getroot()
    tests = sum(int(s.attrib.get("tests", "0")) for s in suites.iter("testsuite"))
    failures = sum(
        int(s.attrib.get("failures", "0")) + int(s.attrib.get("errors", "0"))
        for s in suites.iter("testsuite")
    )
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "test_count": tests,
        "failures_or_errors": failures,
        "statement_coverage_percent": coverage["totals"]["percent_covered"],
        "covered_statements": coverage["totals"]["covered_lines"],
        "total_statements": coverage["totals"]["num_statements"],
        "packages": {
            name: version(name)
            for name in (
                "cite-paper",
                "langgraph",
                "langchain-core",
                "fastapi",
                "httpx",
                "pypdf",
                "pytest",
                "pytest-cov",
                "ruff",
                "torch",
                "transformers",
            )
        },
        "not_verified": [
            "Linux runtime",
            "Docker build / Compose",
            "GitHub Actions run",
            "Production deployment",
            "Automatic semantic entailment",
        ],
    }
    (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    model_path = Path("output/model-smoke-report.json")
    if model_path.exists():
        model_report = json.loads(model_path.read_text(encoding="utf-8"))
        model = {
            k: model_report[k]
            for k in (
                "topic",
                "mode",
                "verification",
                "usage",
                "retrieval_units",
                "budget",
                "timings_ms",
            )
        }
        model["claim_count"] = len(model_report["claims"])
        model["evidence_count"] = len(model_report["evidence"])
        model["note"] = (
            "Real local Qwen CPU inference; connectivity and structure smoke, not answer quality."
        )
        model["history"] = []
        with sqlite3.connect(".citepaper/citepaper.sqlite3") as db:
            for run_id, status, error, checkpoint in db.execute(
                "SELECT id,status,error,checkpoint FROM runs WHERE tenant='real-evaluation' "
                "ORDER BY created"
            ):
                state = json.loads(checkpoint)
                if state.get("request", {}).get("topic") == model_report["topic"]:
                    model["history"].append(
                        {
                            "run_id": run_id,
                            "status": status,
                            "error": error,
                            "last_stage": state.get("stage"),
                        }
                    )
        (output / "model-smoke.json").write_text(
            json.dumps(model, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
