"""检索接口适配层（C 负责）

上游：B 的 `paper_search.engine.PaperSearchEngine`（BM25）
      A 的 `paper-data/data/papers_final.jsonl`（800 篇 arXiv 论文）
下游：`app.py` 只认本文件的 search / get_paper，字段结构保持不变。

本层负责把 B 的字段和参数翻译成前端约定的结构，B 和 A 的改动尽量只影响这个文件：

    B 字段 -> 前端字段
    paper_id   -> id
    year(int)  -> published(str)
    category   -> categories(list)
    matched_terms + snippet -> 带 **高亮** 的 snippet

另外补了 B 没做的三件事：
    1. 空查询浏览（B 对空查询返回 0 条）
    2. get_paper 按 id 查详情（B 未提供）
    3. 索引单例缓存（避免 Streamlit 每次 rerun 重建索引）
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from paper_search.engine import PaperSearchEngine

BASE_DIR = Path(__file__).resolve().parent

# 只使用 A 的正式交付或其等内容副本，不静默退回 B 的 18 篇自测样例。
DATA_CANDIDATES = [
    BASE_DIR / "paper-data" / "data" / "papers_final.jsonl",
    BASE_DIR / "paper-data" / "data" / "papers.jsonl",
]

SORT_MAP = {
    "relevance": "score",
    "newest": "year_desc",
    "oldest": "year_asc",
    "title": "title",
}

SNIPPET_LEN = 220

_ENGINE: PaperSearchEngine | None = None
_ID_MAP: dict[str, dict[str, Any]] | None = None
_DATA_PATH: Path | None = None


class EngineInitError(RuntimeError):
    """索引初始化失败，前端捕获后显示初始化错误页。"""


def data_path() -> Path:
    """返回实际使用的数据文件，找不到就抛错而不是静默降级。"""
    global _DATA_PATH
    if _DATA_PATH is None:
        for candidate in DATA_CANDIDATES:
            if candidate.exists():
                _DATA_PATH = candidate
                break
        else:
            raise EngineInitError(
                "找不到论文数据文件，确认 A 的 papers_final.jsonl 已经放到 paper-data/data/ 下"
            )
    return _DATA_PATH


def get_engine() -> PaperSearchEngine:
    """进程内只构建一次索引。800 篇构建约一秒，不能每次搜索都重来。"""
    global _ENGINE
    if _ENGINE is None:
        try:
            _ENGINE = PaperSearchEngine.from_jsonl(data_path(), strict=False)
        except FileNotFoundError as exc:
            raise EngineInitError(f"数据文件不存在：{exc}") from exc
        except ValueError as exc:
            raise EngineInitError(f"数据格式有问题，B 的索引构建失败：{exc}") from exc
        except Exception as exc:
            raise EngineInitError(f"检索索引初始化失败：{exc}") from exc
    return _ENGINE


def engine_stats() -> dict[str, Any]:
    """页脚展示用：文档数、词表大小、跳过记录数。"""
    engine = get_engine()
    stats = dict(engine.stats)
    stats["data_file"] = data_path().name
    return stats


def _id_map() -> dict[str, dict[str, Any]]:
    global _ID_MAP
    if _ID_MAP is None:
        _ID_MAP = {p["paper_id"]: p for p in get_engine().papers}
    return _ID_MAP


def available_categories() -> list[str]:
    """筛选栏：语料中实际出现过的分类。"""
    return sorted({p["category"] for p in get_engine().papers})


def year_bounds() -> tuple[int, int]:
    """筛选栏：语料的年份区间。"""
    years = [int(p["year"]) for p in get_engine().papers if p.get("year")]
    if not years:
        return (2015, 2026)
    return (min(years), max(years))


def _highlight(text: str, terms: list[str]) -> str:
    """B 给的 snippet 没有高亮标记，用 matched_terms 自己打 ** **。

    按词边界匹配，避免 "ai" 把 "chain" 里的字母也标上。
    """
    if not text or not terms:
        return text
    pattern = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
    return re.sub(rf"\b({pattern})\b", r"**\1**", text, flags=re.IGNORECASE)


def _adapt(hit: dict[str, Any], terms: list[str] | None = None) -> dict[str, Any]:
    """把 B 的一条 hit 翻译成前端字段。"""
    matched = terms if terms is not None else hit.get("matched_terms", [])
    snippet = hit.get("snippet") or (hit.get("abstract", "")[:SNIPPET_LEN])
    category = hit.get("category", "")
    return {
        "id": hit.get("paper_id", ""),
        "title": hit.get("title", ""),
        "abstract": hit.get("abstract", ""),
        "snippet": _highlight(snippet, matched),
        "authors": hit.get("authors", []),
        "categories": [category] if category else [],
        # A 的数据只到年份，没有月日，这里按年展示
        "published": str(hit.get("year", "")) or "",
        "doi": hit.get("doi", ""),
        "url": hit.get("url", ""),
        "source": hit.get("source", ""),
        "score": float(hit.get("score", 0.0)),
        "matched_terms": matched,
        "title_match": bool(hit.get("title_match")),
    }


def _browse(
    page: int,
    page_size: int,
    year_from: int | None,
    year_to: int | None,
    categories: list[str] | None,
    sort_by: str,
) -> dict[str, Any]:
    """空查询浏览。B 的 search 对空查询直接返回 0 条，这里自己走全量过滤。"""
    rows = get_engine().papers
    wanted = {c.lower() for c in categories} if categories else None

    filtered = []
    for p in rows:
        year = int(p.get("year", 0))
        if year_from is not None and year < year_from:
            continue
        if year_to is not None and year > year_to:
            continue
        if wanted and str(p.get("category", "")).lower() not in wanted:
            continue
        filtered.append(p)

    if sort_by == "oldest":
        filtered.sort(key=lambda x: (int(x.get("year", 0)), x.get("title", "")))
    elif sort_by == "title":
        filtered.sort(key=lambda x: x.get("title", "").lower())
    else:  # relevance 无查询词时退化为最新优先
        filtered.sort(key=lambda x: (-int(x.get("year", 0)), x.get("title", "")))

    start = (page - 1) * page_size
    return {
        "total": len(filtered),
        "hits": [_adapt(dict(p), []) for p in filtered[start : start + page_size]],
        "browse_mode": True,
    }


def search(
    query: str,
    page: int = 1,
    page_size: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    categories: list[str] | None = None,
    sort_by: str = "relevance",
    require_all: bool = False,
) -> dict[str, Any]:
    """统一检索入口。

    Args:
        query: 关键词，空字符串表示按筛选条件浏览全部。
        page: 1 起的页码。
        page_size: 每页条数，1~100。
        year_from / year_to: 首次提交年份闭区间，None 不限。
        categories: 分类过滤，空表示不限。
        sort_by: relevance / newest / oldest / title。
        require_all: True 时所有关键词都必须命中（AND 检索）。

    Returns:
        {"total": int, "took_ms": float, "hits": [...], "browse_mode": bool}

    Raises:
        ValueError: 分页或排序参数非法。
        EngineInitError: 索引不可用。
    """
    if page < 1:
        raise ValueError("页码必须从 1 开始")
    if page_size < 1 or page_size > 100:
        raise ValueError("每页条数需在 1~100 之间")
    if sort_by not in SORT_MAP:
        raise ValueError(f"排序方式必须是 {sorted(SORT_MAP)} 之一")

    started = time.perf_counter()

    if not (query or "").strip():
        result = _browse(page, page_size, year_from, year_to, categories, sort_by)
        result["took_ms"] = (time.perf_counter() - started) * 1000
        return result

    response = get_engine().search_with_meta(
        query,
        year_from=year_from,
        year_to=year_to,
        category=list(categories) if categories else None,
        top_k=page_size,
        offset=(page - 1) * page_size,
        require_all=require_all,
        sort_by=SORT_MAP[sort_by],
    )

    return {
        "total": response["total"],
        "took_ms": (time.perf_counter() - started) * 1000,
        "hits": [_adapt(h) for h in response["hits"]],
        "browse_mode": False,
    }


def get_paper(paper_id: str) -> dict[str, Any] | None:
    """详情页：按 id 取单篇。B 未提供此接口，这里用 engine.papers 自建索引。"""
    raw = _id_map().get((paper_id or "").strip())
    if raw is None:
        return None
    return _adapt(dict(raw), [])
