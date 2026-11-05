from __future__ import annotations

import json
import math
import pickle
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# 只做英文检索。A 交付的 title/abstract 保持原始可读文本，分词在 B 层完成。
_TOKEN_RE = re.compile(r"[a-z0-9]+", flags=re.IGNORECASE)
_VERSION_RE = re.compile(r"^(?P<base>\d{4}\.\d{4,5})v\d+$", flags=re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_STOPWORDS = frozenset(
    """
    a an and are as at be been being but by can could did do does doing for from
    had has have having he her here hers herself him himself his how i if in into
    is it its itself may me might more most my myself no nor not of off on once only
    or other our ours ourselves out over own same she should so some such than that
    the their theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why will
    with would you your yours yourself yourselves
    """.split()
)


class EmptyDocumentError(ValueError):
    """论文记录缺少可检索文本。"""


@dataclass(frozen=True)
class SearchRequest:
    """检索参数。search_with_meta 也可直接接收这些参数。"""

    query: str
    year_from: int | None = None
    year_to: int | None = None
    category: str | Sequence[str] | None = None
    top_k: int = 20
    offset: int = 0
    require_all: bool = False
    sort_by: str = "score"


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _normalize_space(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _versionless_id(value: str) -> str:
    value = value.strip()
    match = _VERSION_RE.match(value)
    return match.group("base") if match else value


def tokenize(
    text: str,
    *,
    stopwords: frozenset[str] | set[str] = DEFAULT_STOPWORDS,
) -> list[str]:
    """英文小写分词，去除常见停用词。

    标题和摘要中的标点与换行会被统一处理。数字、缩写和连字符词会被
    拆成稳定 token，例如 retrieval-augmented -> retrieval, augmented。
    """

    if not text:
        return []
    normalized = _normalize_text(text).lower()
    return [
        token
        for token in _TOKEN_RE.findall(normalized)
        if token not in stopwords and len(token) > 1
    ]


class PaperSearchEngine:
    """面向 A 部分 JSONL 数据格式的 BM25 检索引擎。

    核心评分：
    - 标题 token 重复 title_weight 次，默认权重为 2；
    - 正文使用 BM25Okapi 形式的相关性打分；
    - 完整查询短语出现在标题/摘要时增加一个小幅 phrase bonus；
    - 支持年份、分类、AND 检索、分页和排序。
    """

    INDEX_VERSION = 1
    SORT_OPTIONS = {"score", "year_desc", "year_asc", "title"}

    def __init__(
        self,
        papers: Iterable[Mapping[str, Any]] | None = None,
        *,
        k1: float = 1.5,
        b: float = 0.75,
        title_weight: int = 2,
        phrase_boost: float = 0.5,
        stopwords: Iterable[str] | None = None,
        strict: bool = False,
    ) -> None:
        self.k1 = float(k1)
        self.b = float(b)
        self.title_weight = max(1, int(title_weight))
        self.phrase_boost = max(0.0, float(phrase_boost))
        self.stopwords = frozenset(stopwords) if stopwords is not None else DEFAULT_STOPWORDS
        self.strict = bool(strict)

        self._papers: list[dict[str, Any]] = []
        self._inverted_index: dict[str, dict[int, int]] = defaultdict(dict)
        self._doc_lengths: list[int] = []
        self._title_term_texts: list[str] = []
        self._abstract_term_texts: list[str] = []
        self._title_token_sets: list[set[str]] = []
        self._abstract_token_sets: list[set[str]] = []
        self._avgdl = 0.0
        self.skipped_records: list[dict[str, str]] = []

        if papers is not None:
            self.build(papers)

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        strict: bool = False,
        **kwargs: Any,
    ) -> "PaperSearchEngine":
        """从 A 交付的 UTF-8 JSONL 文件构建索引。"""

        engine = cls(strict=strict, **kwargs)
        engine.build_from_jsonl(path, strict=strict)
        return engine

    def build_from_jsonl(self, path: str | Path, *, strict: bool | None = None) -> dict[str, Any]:
        records = self._read_jsonl(path)
        return self.build(records, strict=strict)

    def build(
        self,
        papers: Iterable[Mapping[str, Any]],
        *,
        strict: bool | None = None,
    ) -> dict[str, Any]:
        """清空旧索引并构建新索引。"""

        effective_strict = self.strict if strict is None else bool(strict)
        self._papers = []
        self._inverted_index = defaultdict(dict)
        self._doc_lengths = []
        self._title_term_texts = []
        self._abstract_term_texts = []
        self._title_token_sets = []
        self._abstract_token_sets = []
        self.skipped_records = []
        self._avgdl = 0.0

        seen_ids: set[str] = set()
        seen_titles: set[str] = set()

        for line_number, paper in enumerate(papers, start=1):
            try:
                normalized = self._normalize_record(paper)
                paper_id = normalized["paper_id"]
                title_key = _normalize_space(normalized["title"]).lower()

                if paper_id in seen_ids or title_key in seen_titles:
                    self.skipped_records.append(
                        {"line": str(line_number), "paper_id": paper_id, "reason": "duplicate"}
                    )
                    continue

                title_tokens = tokenize(normalized["title"], stopwords=self.stopwords)
                abstract_tokens = tokenize(normalized["abstract"], stopwords=self.stopwords)
                weighted_tokens = title_tokens * self.title_weight + abstract_tokens
                if not weighted_tokens:
                    raise EmptyDocumentError("title/abstract 没有可检索 token")

                doc_index = len(self._papers)
                self._papers.append(normalized)
                self._doc_lengths.append(len(weighted_tokens))
                self._title_term_texts.append(" ".join(title_tokens))
                self._abstract_term_texts.append(" ".join(abstract_tokens))
                self._title_token_sets.append(set(title_tokens))
                self._abstract_token_sets.append(set(abstract_tokens))

                for term, frequency in Counter(weighted_tokens).items():
                    self._inverted_index[term][doc_index] = frequency

                seen_ids.add(paper_id)
                seen_titles.add(title_key)
            except (TypeError, ValueError, KeyError) as exc:
                message = str(exc) or exc.__class__.__name__
                if effective_strict:
                    raise ValueError(f"第 {line_number} 条论文数据无效: {message}") from exc
                self.skipped_records.append(
                    {"line": str(line_number), "paper_id": "", "reason": message}
                )

        self._inverted_index = dict(self._inverted_index)
        total_length = sum(self._doc_lengths)
        self._avgdl = total_length / len(self._doc_lengths) if self._doc_lengths else 0.0
        if not self._papers:
            raise ValueError("没有成功构建任何论文记录，请检查 A 的 JSONL 数据和字段名")
        return self.stats

    def search(
        self,
        query: str,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        category: str | Sequence[str] | None = None,
        top_k: int = 20,
        offset: int = 0,
        require_all: bool = False,
        sort_by: str = "score",
    ) -> list[dict[str, Any]]:
        """返回检索结果列表，适合 C 直接调用。"""

        request = SearchRequest(
            query=query,
            year_from=year_from,
            year_to=year_to,
            category=category,
            top_k=top_k,
            offset=offset,
            require_all=require_all,
            sort_by=sort_by,
        )
        return self.search_with_meta(request=request)["hits"]

    def search_with_meta(
        self,
        query: str | None = None,
        *,
        request: SearchRequest | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        category: str | Sequence[str] | None = None,
        top_k: int = 20,
        offset: int = 0,
        require_all: bool = False,
        sort_by: str = "score",
    ) -> dict[str, Any]:
        """返回 hits 以及 total/filter 信息，便于 C 做分页。"""

        if request is None:
            request = SearchRequest(
                query=query or "",
                year_from=year_from,
                year_to=year_to,
                category=category,
                top_k=top_k,
                offset=offset,
                require_all=require_all,
                sort_by=sort_by,
            )

        query = _normalize_space(request.query or "")
        top_k = max(0, min(int(request.top_k), 200))
        offset = max(0, int(request.offset))
        if request.sort_by not in self.SORT_OPTIONS:
            raise ValueError(f"sort_by 必须是 {sorted(self.SORT_OPTIONS)} 之一")

        candidate_terms = list(dict.fromkeys(tokenize(query, stopwords=self.stopwords)))
        empty_response = {
            "query": query,
            "total": 0,
            "hits": [],
            "filters": self._filters_as_dict(request),
        }
        if not candidate_terms or top_k == 0:
            return empty_response

        posting_sets: list[set[int]] = []
        for term in candidate_terms:
            postings = self._inverted_index.get(term)
            if not postings:
                if request.require_all:
                    return empty_response
                continue
            eligible = {
                doc_index
                for doc_index in postings
                if self._matches_filters(doc_index, request)
            }
            posting_sets.append(eligible)

        if not posting_sets:
            return empty_response

        if request.require_all:
            candidates = set.intersection(*posting_sets)
        else:
            candidates = set.union(*posting_sets)

        n_docs = len(self._papers)
        phrase = " ".join(candidate_terms)
        scored: list[tuple[int, float, list[str]]] = []

        for doc_index in candidates:
            score = 0.0
            matched_terms: list[str] = []
            for term in candidate_terms:
                postings = self._inverted_index.get(term)
                if not postings or doc_index not in postings:
                    continue
                matched_terms.append(term)
                frequency = postings[doc_index]
                document_frequency = len(postings)
                idf = math.log(1.0 + (n_docs - document_frequency + 0.5) / (document_frequency + 0.5))
                doc_length = self._doc_lengths[doc_index]
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * doc_length / max(self._avgdl, 1e-12)
                )
                score += idf * (frequency * (self.k1 + 1.0)) / denominator

            if self.phrase_boost and len(candidate_terms) > 1:
                if phrase in self._title_term_texts[doc_index]:
                    score += self.phrase_boost * 1.5
                elif phrase in self._abstract_term_texts[doc_index]:
                    score += self.phrase_boost

            scored.append((doc_index, score, matched_terms))

        sort_key = request.sort_by
        if sort_key == "score":
            scored.sort(key=lambda item: (-item[1], -int(self._papers[item[0]]["year"]), item[0]))
        elif sort_key == "year_desc":
            scored.sort(key=lambda item: (-int(self._papers[item[0]]["year"]), -item[1], item[0]))
        elif sort_key == "year_asc":
            scored.sort(key=lambda item: (int(self._papers[item[0]]["year"]), -item[1], item[0]))
        else:
            scored.sort(key=lambda item: (self._papers[item[0]]["title"].lower(), -item[1]))

        total = len(scored)
        page = scored[offset : offset + top_k]
        hits: list[dict[str, Any]] = []
        for rank, (doc_index, score, matched_terms) in enumerate(page, start=offset + 1):
            paper = self._papers[doc_index]
            hits.append(
                {
                    "rank": rank,
                    "paper_id": paper["paper_id"],
                    "title": paper["title"],
                    "abstract": paper["abstract"],
                    "snippet": self._make_snippet(paper["abstract"], candidate_terms),
                    "authors": paper["authors"],
                    "year": paper["year"],
                    "category": paper["category"],
                    "url": paper["url"],
                    "source": paper["source"],
                    "score": round(score, 6),
                    "matched_terms": matched_terms,
                    "title_match": bool(set(matched_terms) & self._title_token_sets[doc_index]),
                    "abstract_match": bool(set(matched_terms) & self._abstract_token_sets[doc_index]),
                }
            )

        return {
            "query": query,
            "total": total,
            "hits": hits,
            "filters": self._filters_as_dict(request),
        }

    @property
    def papers(self) -> list[dict[str, Any]]:
        return self._papers

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "index_version": self.INDEX_VERSION,
            "documents": len(self._papers),
            "vocabulary": len(self._inverted_index),
            "average_document_length": round(self._avgdl, 4),
            "skipped_records": len(self.skipped_records),
        }

    def save(self, path: str | Path) -> None:
        """保存本地索引。只加载自己生成的文件，不要加载不可信 pickle。"""

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as file:
            pickle.dump(
                {
                    "index_version": self.INDEX_VERSION,
                    "engine": self,
                },
                file,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

    @classmethod
    def load(cls, path: str | Path) -> "PaperSearchEngine":
        with Path(path).open("rb") as file:
            payload = pickle.load(file)
        if not isinstance(payload, dict) or payload.get("index_version") != cls.INDEX_VERSION:
            raise ValueError("索引版本不兼容，请重新构建")
        engine = payload.get("engine")
        if not isinstance(engine, cls):
            raise ValueError("索引文件内容不是 PaperSearchEngine")
        return engine

    @staticmethod
    def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with Path(path).open("r", encoding="utf-8-sig") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"第 {line_number} 行不是合法 JSON: {exc}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"第 {line_number} 行必须是 JSON object")
                records.append(value)
        return records

    @staticmethod
    def _normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(record, Mapping):
            raise TypeError("论文记录必须是 object")

        paper_id = _versionless_id(str(record.get("paper_id", "")).strip())
        title = _normalize_space(str(record.get("title", "")))
        abstract = _normalize_space(str(record.get("abstract", "")))
        if not paper_id:
            raise ValueError("paper_id 不能为空")
        if not title:
            raise ValueError("title 不能为空")
        if not abstract:
            raise ValueError("abstract 不能为空")

        raw_authors = record.get("authors", [])
        if isinstance(raw_authors, str):
            authors = [item.strip() for item in raw_authors.split(",") if item.strip()]
        elif isinstance(raw_authors, Sequence):
            authors = [str(item).strip() for item in raw_authors if str(item).strip()]
        else:
            authors = []

        try:
            year = int(record.get("year", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("year 必须是整数") from exc

        category = str(record.get("category", "")).strip() or "unknown"
        url = str(record.get("url", "")).strip()
        source = str(record.get("source", "")).strip() or "unknown"
        return {
            "paper_id": paper_id,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "year": year,
            "category": category,
            "url": url,
            "source": source,
        }

    def _matches_filters(self, doc_index: int, request: SearchRequest) -> bool:
        paper = self._papers[doc_index]
        year = int(paper["year"])
        if request.year_from is not None and year < int(request.year_from):
            return False
        if request.year_to is not None and year > int(request.year_to):
            return False

        if request.category:
            if isinstance(request.category, str):
                categories = {request.category.strip().lower()}
            else:
                categories = {str(item).strip().lower() for item in request.category}
            if str(paper["category"]).lower() not in categories:
                return False
        return True

    @staticmethod
    def _filters_as_dict(request: SearchRequest) -> dict[str, Any]:
        category: Any = request.category
        if isinstance(category, Sequence) and not isinstance(category, str):
            category = list(category)
        return {
            "year_from": request.year_from,
            "year_to": request.year_to,
            "category": category,
            "require_all": request.require_all,
            "sort_by": request.sort_by,
        }

    @staticmethod
    def _make_snippet(text: str, terms: list[str], max_chars: int = 220) -> str:
        compact = _normalize_space(text)
        if not compact:
            return ""
        lowered = compact.lower()
        positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
        position = min(positions) if positions else 0
        start = max(0, position - 70)
        end = min(len(compact), start + max_chars)
        if start > 0:
            next_space = compact.find(" ", start)
            if next_space != -1 and next_space < position:
                start = next_space + 1
        if end < len(compact):
            previous_space = compact.rfind(" ", start, end)
            if previous_space > position:
                end = previous_space
        snippet = compact[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(compact):
            snippet = snippet + "..."
        return snippet
