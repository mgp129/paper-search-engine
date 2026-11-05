"""B 部分命令行入口，方便 C 或答辩现场验证检索逻辑。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_search import PaperSearchEngine  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="论文检索模块命令行工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="从 JSONL 构建索引")
    build.add_argument("--data", required=True)
    build.add_argument("--index", required=True)
    build.add_argument("--strict", action="store_true")

    search = subparsers.add_parser("search", help="执行查询")
    source = search.add_mutually_exclusive_group(required=True)
    source.add_argument("--data")
    source.add_argument("--index")
    search.add_argument("--query", required=True)
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--year-from", type=int)
    search.add_argument("--year-to", type=int)
    search.add_argument("--category", action="append")
    search.add_argument("--require-all", action="store_true")
    search.add_argument("--sort-by", default="score", choices=["score", "year_desc", "year_asc", "title"])

    stats = subparsers.add_parser("stats", help="查看索引信息")
    source = stats.add_mutually_exclusive_group(required=True)
    source.add_argument("--data")
    source.add_argument("--index")
    return parser.parse_args()


def load_engine(args: argparse.Namespace) -> PaperSearchEngine:
    if getattr(args, "index", None):
        return PaperSearchEngine.load(args.index)
    return PaperSearchEngine.from_jsonl(args.data)


def main() -> None:
    args = parse_args()
    if args.command == "build":
        engine = PaperSearchEngine.from_jsonl(args.data, strict=args.strict)
        engine.save(args.index)
        print(json.dumps({"index": str(Path(args.index).resolve()), **engine.stats}, ensure_ascii=False, indent=2))
        return

    engine = load_engine(args)
    if args.command == "stats":
        print(json.dumps(engine.stats, ensure_ascii=False, indent=2))
        return

    response = engine.search_with_meta(
        query=args.query,
        year_from=args.year_from,
        year_to=args.year_to,
        category=args.category,
        top_k=args.top_k,
        offset=args.offset,
        require_all=args.require_all,
        sort_by=args.sort_by,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
