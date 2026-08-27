from __future__ import annotations

import argparse
import json

from cite_paper.tools.arxiv import ArxivError, search_arxiv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research CS papers with verifiable citations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search arXiv by research topic.")
    search_parser.add_argument("query", help="Natural-language research topic.")
    search_parser.add_argument("--max-results", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "search":
        try:
            papers = search_arxiv(args.query, max_results=args.max_results)
        except (ArxivError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            return 1

        print(
            json.dumps(
                {"query": args.query, "count": len(papers), "papers": [p.to_dict() for p in papers]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
