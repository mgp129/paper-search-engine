"""论文检索系统 · 前端页面（成员 C）

运行：
    streamlit run app.py

数据来自 A 的 papers_final.jsonl，检索由 B 的 PaperSearchEngine（BM25）提供。
本文件只负责展示与交互，所有数据通过 search_api 获取。
"""

from __future__ import annotations

import re

import streamlit as st

import search_api
from search_api import EngineInitError

PAGE_SIZE_OPTIONS = [5, 10, 20]
SORT_LABELS = {
    "relevance": "相关度",
    "newest": "最新优先",
    "oldest": "最早优先",
    "title": "标题排序",
}

st.set_page_config(page_title="论文检索系统", page_icon="🔍", layout="wide")


# ----------------------------------------------------------------- 索引缓存
@st.cache_resource(show_spinner="正在构建检索索引…")
def _boot() -> dict:
    """整个应用只构建一次索引。Streamlit 每次交互都会重跑脚本，必须缓存。"""
    search_api.get_engine()
    return {
        "stats": search_api.engine_stats(),
        "categories": search_api.available_categories(),
        "years": search_api.year_bounds(),
    }


# ----------------------------------------------------------------- 会话状态
st.session_state.setdefault("page", 1)
st.session_state.setdefault("detail_id", None)
st.session_state.setdefault("last_query", "")
# 详情页不渲染搜索控件。显式保留它们的值，避免 Streamlit 清理后返回时丢失查询与筛选。
for _key in ("query", "categories", "sort", "require_all", "page_size", "year_range"):
    if _key in st.session_state:
        st.session_state[_key] = st.session_state[_key]


def _reset_page() -> None:
    """筛选条件变化就回到第 1 页，否则可能停在空白页。"""
    st.session_state.page = 1
    st.session_state.detail_id = None


def _show_detail(paper_id: str) -> None:
    st.session_state.detail_id = paper_id


def _return_to_results() -> None:
    st.session_state.detail_id = None


# ----------------------------------------------------------------- 渲染辅助
def render_highlight(text: str) -> str:
    """把 **关键词** 转成带底色的 HTML，先转义防止摘要里的尖括号破坏页面。"""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(
        r"\*\*(.+?)\*\*",
        r'<mark style="background:#fff3b0;padding:0 2px;border-radius:2px">\1</mark>',
        escaped,
    )


def format_authors(authors: list[str], limit: int = 3) -> str:
    if not authors:
        return "作者信息缺失"
    if len(authors) <= limit:
        return "、".join(authors)
    return "、".join(authors[:limit]) + f" 等 {len(authors)} 人"


def meta_line(paper: dict, author_limit: int = 3) -> str:
    return (
        f"{format_authors(paper.get('authors', []), limit=author_limit)}　|　"
        f"{paper.get('published') or '年份未知'}　|　"
        f"{' · '.join(paper.get('categories', [])) or '无分类'}"
    )


# ----------------------------------------------------------------- 详情页
def render_detail(paper_id: str) -> None:
    st.button("← 返回结果列表", on_click=_return_to_results)

    paper = search_api.get_paper(paper_id)
    if paper is None:
        st.error(f"没有找到 id 为 `{paper_id}` 的论文，数据集可能已更新。")
        return

    st.title(paper["title"])
    st.caption(meta_line(paper, author_limit=len(paper.get("authors", []))))

    st.subheader("摘要")
    st.write(paper.get("abstract") or "（该论文无摘要）")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**arXiv ID**：`{paper.get('id', '-')}`")
        st.markdown(f"**数据来源**：{paper.get('source') or '—'}")
    with col2:
        url = paper.get("url")
        st.markdown(f"**原文链接**：[{url}]({url})" if url else "**原文链接**：—")
        if paper.get("doi"):
            st.markdown(f"**DOI**：{paper['doi']}")


# ----------------------------------------------------------------- 结果卡片
def render_card(paper: dict, browse_mode: bool = False) -> None:
    with st.container(border=True):
        left, right = st.columns([5, 1])
        with left:
            st.markdown(f"#### {paper.get('title') or '(无标题)'}")
            st.caption(meta_line(paper))
            snippet = paper.get("snippet") or paper.get("abstract", "")[:200]
            st.markdown(render_highlight(snippet), unsafe_allow_html=True)
            if paper.get("title_match"):
                st.caption("🎯 标题命中关键词")
        with right:
            if browse_mode:
                st.caption("浏览模式")
            else:
                st.metric("得分", f"{paper.get('score', 0):.2f}")
            st.button("查看详情", key=f"detail-{paper['id']}", on_click=_show_detail, args=(paper["id"],))


# ----------------------------------------------------------------- 主页面
def render_search_page(boot: dict) -> None:
    st.title("🔍 论文检索系统")
    st.caption("基于 BM25 的 arXiv 计算机方向论文检索（cs.IR / cs.CL / cs.LG / cs.AI）")

    y_min, y_max = boot["years"]

    with st.sidebar:
        st.header("筛选条件")

        if y_min == y_max:
            # A 当前数据集年份单一，滑块无意义，直接显示
            year_range = (y_min, y_max)
            st.write(f"**首次提交年份**：{y_min}（数据集仅含该年份）")
        else:
            year_range = st.slider(
                "首次提交年份",
                min_value=y_min,
                max_value=y_max,
                value=(y_min, y_max),
                on_change=_reset_page,
                key="year_range",
            )

        categories = st.multiselect(
            "学科分类", options=boot["categories"], default=[], on_change=_reset_page, key="categories"
        )
        sort_by = st.selectbox(
            "排序方式",
            options=list(SORT_LABELS),
            format_func=lambda k: SORT_LABELS[k],
            on_change=_reset_page,
            key="sort",
        )
        require_all = st.checkbox(
            "所有关键词都必须命中（AND）", value=False, on_change=_reset_page, key="require_all"
        )
        page_size = st.selectbox(
            "每页条数", options=PAGE_SIZE_OPTIONS, index=1, on_change=_reset_page, key="page_size"
        )

        stats = boot["stats"]
        st.divider()
        st.caption(
            f"索引：{stats['documents']} 篇 · 词表 {stats['vocabulary']}\n\n"
            f"数据文件：{stats['data_file']}"
        )
        if stats.get("skipped_records"):
            st.caption(f"⚠️ 构建时跳过 {stats['skipped_records']} 条异常记录")

    query = st.text_input(
        "输入关键词",
        placeholder="例如：dense retrieval、query expansion、language model",
        label_visibility="collapsed",
        key="query",
    )
    st.button("检索", type="primary")

    if query != st.session_state.last_query:
        st.session_state.last_query = query
        _reset_page()

    def run_search(page: int) -> dict:
        return search_api.search(
            query=query,
            page=page,
            page_size=page_size,
            year_from=year_range[0],
            year_to=year_range[1],
            categories=categories,
            sort_by=sort_by,
            require_all=require_all,
        )

    try:
        result = run_search(st.session_state.page)
    except ValueError as exc:
        st.error(f"检索参数有问题：{exc}")
        return
    except Exception as exc:  # 检索层抛任何异常都不要让页面崩掉
        st.error("检索服务异常，请稍后重试或联系检索模块负责同学。")
        st.exception(exc)
        return

    total, hits = result["total"], result["hits"]

    if total == 0:
        if query.strip():
            st.warning(
                "没有匹配的论文。可以试试：去掉部分筛选条件、关闭 AND 检索，"
                "或换更常见的英文关键词（当前数据集为英文论文）。"
            )
        else:
            st.warning("当前筛选条件下没有论文，试试放宽筛选条件。")
        return

    total_pages = max(1, (total + page_size - 1) // page_size)
    clamped_page = min(st.session_state.page, total_pages)
    if clamped_page != st.session_state.page:
        st.session_state.page = clamped_page
        result = run_search(clamped_page)
        total, hits = result["total"], result["hits"]

    if result.get("browse_mode"):
        st.info(f"未输入关键词，按筛选条件浏览全部 {total} 篇论文（{SORT_LABELS[sort_by]}）。")
    else:
        st.success(
            f"共找到 {total} 篇，耗时 {result['took_ms']:.0f} ms　|　"
            f"第 {st.session_state.page} / {total_pages} 页"
        )

    for paper in hits:
        render_card(paper, browse_mode=result.get("browse_mode", False))

    prev_col, mid_col, next_col = st.columns([1, 3, 1])
    with prev_col:
        if st.button("上一页", disabled=st.session_state.page <= 1):
            st.session_state.page -= 1
            st.rerun()
    with mid_col:
        st.markdown(
            f"<div style='text-align:center;padding-top:6px;color:#888'>"
            f"{st.session_state.page} / {total_pages}</div>",
            unsafe_allow_html=True,
        )
    with next_col:
        if st.button("下一页", disabled=st.session_state.page >= total_pages):
            st.session_state.page += 1
            st.rerun()


# ----------------------------------------------------------------- 入口
try:
    _boot_data = _boot()
except EngineInitError as exc:
    st.title("🔍 论文检索系统")
    st.error(f"索引初始化失败：{exc}")
    st.info("确认 A 的 papers_final.jsonl 在 paper-data/data/ 目录下，然后刷新页面。")
    st.stop()

if st.session_state.detail_id:
    render_detail(st.session_state.detail_id)
else:
    render_search_page(_boot_data)
