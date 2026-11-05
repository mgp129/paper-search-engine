#!/usr/bin/env python3
"""仅采集 arXiv 官方 Atom API 元数据；可从缓存继续，不下载 PDF。"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
API = "https://export.arxiv.org/api/query"
CATEGORIES = ("cs.IR", "cs.CL", "cs.LG", "cs.AI")
FIELDS = ("paper_id", "title", "abstract", "authors", "year", "category", "url", "source")
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom",
      "o": "http://a9.com/-/spec/opensearch/1.1/"}
ID_PATTERN = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})\Z")
# 只移除明确的 HTML 标签，保留数学不等号和 <T> 一类技术内容。
TAG_PATTERN = re.compile(
    r"<!--.*?-->|</?(?:a|abbr|address|area|article|aside|audio|b|base|bdi|bdo|blockquote|"
    r"body|br|button|canvas|caption|cite|code|col|colgroup|data|datalist|dd|del|details|dfn|"
    r"dialog|div|dl|dt|em|embed|fieldset|figcaption|figure|font|footer|form|h[1-6]|head|"
    r"header|hgroup|hr|html|i|iframe|img|input|ins|kbd|label|legend|li|link|main|map|mark|"
    r"menu|meta|meter|nav|noscript|object|ol|optgroup|option|output|p|param|picture|pre|"
    r"progress|q|rp|rt|ruby|s|samp|script|search|section|select|slot|small|source|span|"
    r"strong|style|sub|summary|sup|svg|table|tbody|td|template|textarea|tfoot|th|thead|"
    r"time|title|tr|track|u|ul|var|video|wbr)\b[^>]*>", re.I | re.S)
ENTITY_PATTERN = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]+|#\d+|#x[0-9a-fA-F]+);")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value):
    text = value or ""
    # XML 已解码一层；继续解码可能残留的 HTML 实体，直到稳定。
    for _ in range(8):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = TAG_PATTERN.sub(" ", text)
    return " ".join(text.split())


def has_text_problem(text):
    return bool(TAG_PATTERN.search(text) or "\ufffd" in text
                or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", text)
                or any(marker in text for marker in ("â€™", "â€œ", "â€", "ï¿½", "Ã©", "Ã¶", "Ã¼", "ðŸ"))
                or any(html.unescape(m.group()) != m.group()
                       for m in ENTITY_PATTERN.finditer(text)))


def extract_record(entry, cutoff):
    """返回 (记录, None) 或 (None, 丢弃原因)，原因互斥。"""
    def field(name):
        node = entry.find("a:" + name, NS)
        return "" if node is None else "".join(node.itertext())

    raw_id = field("id").strip()
    parsed_id = urlsplit(raw_id)
    if parsed_id.hostname not in {"arxiv.org", "export.arxiv.org"} or not parsed_id.path.startswith("/abs/"):
        return None, "invalid_id_removed"
    paper_id = re.sub(r"v\d+$", "", parsed_id.path[5:])
    if not ID_PATTERN.fullmatch(paper_id):
        return None, "invalid_id_removed"
    title, abstract = clean_text(field("title")), clean_text(field("summary"))
    if not title:
        return None, "missing_title_removed"
    if not abstract:
        return None, "missing_abstract_removed"
    primary = entry.find("x:primary_category", NS)
    category = "" if primary is None else primary.get("term", "")
    if category not in CATEGORIES:
        return None, "outside_primary_category_removed"
    try:
        published = datetime.fromisoformat(field("published").replace("Z", "+00:00"))
        if published.tzinfo is None or not 1991 <= published.year <= datetime.now(timezone.utc).year:
            raise ValueError("invalid year")
        if published > datetime.strptime(cutoff, "%Y%m%d%H%M").replace(tzinfo=timezone.utc):
            raise ValueError("after query cutoff")
    except (ValueError, TypeError):
        return None, "invalid_published_removed"
    authors = [clean_text("".join(n.itertext())) for n in entry.findall("a:author/a:name", NS)]
    authors = [name for name in authors if name]
    if not authors:
        return None, "missing_authors_removed"
    if any(has_text_problem(s) for s in [title, abstract, *authors]):
        return None, "invalid_text_removed"
    record = dict(zip(FIELDS, [paper_id, title, abstract, authors, published.year,
                              category, "https://arxiv.org/abs/" + paper_id, "arxiv"]))
    return {"record": record, "published": published.isoformat()}, None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quotas(size):
    return {cat: size // 4 + (i < size % 4) for i, cat in enumerate(CATEGORIES)}


def select_records(pool, size):
    quota = quotas(size)
    selected = []
    # 每类保留最近首次提交的论文；固定 ID 次序消除时间相同的不确定性。
    ordered = sorted(pool.values(), key=lambda x: (x["published"], x["record"]["paper_id"]), reverse=True)
    counts = Counter()
    for item in ordered:
        cat = item["record"]["category"]
        if counts[cat] < quota[cat]:
            selected.append(item["record"])
            counts[cat] += 1
    return selected


class Collector:
    def __init__(self, root, delay=3.1, timeout=45, retries=2, max_pages=40, opener=None):
        self.root = Path(root)
        self.cache = self.root / ".cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.state_path = self.cache / "state.json"
        self.delay, self.timeout, self.retries = max(3.0, delay), timeout, retries
        self.max_pages = max_pages
        self.opener = opener or urlopen
        self.last_finished = 0.0
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if self.state.get("schema_version") != 1:
                raise ValueError("缓存版本不兼容；请另选 --root，勿混用缓存。")
        else:
            self.state = {"schema_version": 1, "started_at_utc": utc_now(),
                          "query_cutoff_utc": datetime.now(timezone.utc).strftime("%Y%m%d%H%M"),
                          "pages": [], "attempts": [], "last_request_finished_at_utc": None}
            self.save()
        self.pool, self.counts = {}, Counter()
        self.starts = {cat: 0 for cat in CATEGORIES}
        self.exhausted = set()
        self.query_counts = Counter()
        # 重放已成功下载的完整 XML 页，恢复真实统计，不重新请求。
        for page in self.state["pages"]:
            raw = (self.cache / page["file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != page["sha256"]:
                raise ValueError("缓存 XML 校验失败：" + page["file"])
            self.consume(raw, page)

    def save(self):
        write_json(self.state_path, self.state)

    @staticmethod
    def parse_feed(raw):
        root = ET.fromstring(raw)
        if root.tag != "{" + NS["a"] + "}feed":
            raise ValueError("API 返回的不是 Atom feed")
        entries = root.findall("a:entry", NS)
        for entry in entries:
            if "api/errors" in (entry.findtext("a:id", "", NS)):
                raise ValueError("arXiv API 错误：" + entry.findtext("a:summary", "", NS))
        total = int(root.findtext("o:totalResults", "0", NS))
        start = int(root.findtext("o:startIndex", "0", NS))
        return entries, total, start

    def consume(self, raw, page):
        entries, total, start = self.parse_feed(raw)
        if start != page["start"]:
            raise ValueError("API 返回的分页起点与请求不一致")
        cat = page["category"]
        self.query_counts[cat] += 1
        self.starts[cat] = start + len(entries)
        if self.starts[cat] >= total:
            self.exhausted.add(cat)
        for entry in entries:
            self.counts["raw_entries_received"] += 1
            item, reason = extract_record(entry, self.state["query_cutoff_utc"])
            if reason:
                self.counts[reason] += 1
            elif item["record"]["paper_id"] in self.pool:
                self.counts["duplicates_removed"] += 1
            else:
                self.pool[item["record"]["paper_id"]] = item

    def wait_for_request(self):
        wait = self.delay - (time.monotonic() - self.last_finished)
        previous = self.state.get("last_request_finished_at_utc")
        if previous:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(previous)).total_seconds()
            # 持久化时间精确到秒，多留一秒，确保进程重启后也不会缩短间隔。
            wait = max(wait, self.delay + 1.0 - elapsed)
        if wait > 0:
            time.sleep(wait)

    def request(self, url, expected_start):
        for attempt in range(self.retries + 1):
            self.wait_for_request()
            log = {"url": url, "started_at_utc": utc_now(), "attempt": attempt + 1}
            self.state["attempts"].append(log)
            self.save()
            error = None
            retryable = True
            backoff = min(30.0, self.delay * (2 ** attempt))
            try:
                request = Request(url, headers={"User-Agent": "paper-data-course/1.0 (arXiv metadata; Python urllib)",
                                                "Accept": "application/atom+xml"})
                with self.opener(request, timeout=self.timeout) as response:
                    raw = response.read()
                    log["http_status"] = response.status
                entries, total, start = self.parse_feed(raw)
                if start != expected_start or (not entries and start < total):
                    raise ValueError("API 分页位置异常或意外返回空页")
                log["result"] = "success"
                return raw
            except HTTPError as exc:
                error = exc
                log["http_status"] = exc.code
                retryable = exc.code in {408, 429, 500, 502, 503, 504}
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                if retry_after.isdigit():
                    if int(retry_after) > 60:
                        retryable = False  # 不在后台长时间等待；用户稍后重新执行即可。
                    else:
                        backoff = max(backoff, int(retry_after))
            except (URLError, TimeoutError, OSError, ValueError, ET.ParseError) as exc:
                error = exc
            finally:
                self.last_finished = time.monotonic()
                self.state["last_request_finished_at_utc"] = utc_now()
                if error:
                    log["result"], log["error"] = "failed", str(error)
                self.save()
            print(f"请求失败 ({attempt + 1}/{self.retries + 1}): {error}", file=sys.stderr, flush=True)
            if not retryable or attempt == self.retries:
                raise RuntimeError(f"官方 API 请求失败，已停止且保留缓存：{error}") from error
            time.sleep(backoff)

    def fill(self, size, page_size):
        target = quotas(size)
        while True:
            available = Counter(item["record"]["category"] for item in self.pool.values())
            pending = [cat for cat in CATEGORIES if available[cat] < target[cat]]
            if not pending:
                return
            progressed = False
            for cat in pending:
                if cat in self.exhausted or self.query_counts[cat] >= self.max_pages:
                    continue
                query = f"cat:{cat} AND submittedDate:[199101010000 TO {self.state['query_cutoff_utc']}]"
                params = {"search_query": query, "start": self.starts[cat], "max_results": page_size,
                          "sortBy": "submittedDate", "sortOrder": "descending"}
                url = API + "?" + urlencode(params)
                print(f"采集 {cat}, start={self.starts[cat]}, max_results={page_size}", flush=True)
                raw = self.request(url, self.starts[cat])
                filename = f"page_{len(self.state['pages']):04d}.xml"
                page = {"category": cat, "query": query, "start": self.starts[cat], "max_results": page_size,
                        "url": url, "file": filename, "fetched_at_utc": utc_now(),
                        "sha256": hashlib.sha256(raw).hexdigest()}
                (self.cache / filename).write_bytes(raw)
                self.consume(raw, page)
                self.state["pages"].append(page)
                self.save()
                print("  已保留的唯一有效论文：" + json.dumps(
                    dict(Counter(x["record"]["category"] for x in self.pool.values())), ensure_ascii=False), flush=True)
                progressed = True
            if not progressed:
                raise RuntimeError("达到分页上限或结果已耗尽，仍不足各类目标；请检查网络/查询，或提高 --max-pages。")

    def export(self, stage, sample_size, dev_size, final_size):
        size = {"sample": sample_size, "dev": dev_size, "final": final_size}[stage]
        records = select_records(self.pool, size)
        if len(records) != size:
            raise ValueError("数量未达标，拒绝写入交付文件")
        data = self.root / "data"
        data.mkdir(exist_ok=True)
        if stage == "sample":
            write_jsonl(self.cache / "papers_sample.jsonl", records)
        elif stage == "dev":
            write_jsonl(data / "papers_dev.jsonl", records)
        else:
            # 开发版优先在 fill(final) 之前生成；若不存在则明确报错。
            if not (data / "papers_dev.jsonl").is_file():
                raise ValueError("缺少开发版，请先执行 --stage dev")
            write_jsonl(data / "papers_final.jsonl", records)
            shutil.copyfile(data / "papers_final.jsonl", data / "papers.jsonl")
        stats = self.make_stats(stage, records)
        write_json(data / "data_stats.json", stats)
        self.write_card(stats)
        print(f"{stage} 导出成功：{len(records)} 篇", flush=True)
        return stats

    def make_stats(self, stage, records):
        counters = {name: self.counts[name] for name in (
            "raw_entries_received", "duplicates_removed", "missing_title_removed", "missing_abstract_removed",
            "outside_primary_category_removed", "invalid_id_removed", "invalid_published_removed",
            "missing_authors_removed", "invalid_text_removed")}
        files = {}
        for filename in ("papers_dev.jsonl", "papers_final.jsonl", "papers.jsonl"):
            path = self.root / "data" / filename
            if path.exists():
                rows = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]
                files[filename] = {"records": len(rows), "sha256": sha256(path),
                                   "categories": {cat: sum(r["category"] == cat for r in rows) for cat in CATEGORIES}}
        return {"stage": stage, "source": "arxiv", "api_endpoint": API,
                "collection_started_at_utc": self.state["started_at_utc"], "generated_at_utc": utc_now(),
                "query_cutoff_utc": self.state["query_cutoff_utc"],
                "total_records": len(records), **counters, "unique_valid_candidates": len(self.pool),
                "valid_candidates_not_selected": len(self.pool) - len(records),
                "categories": {cat: sum(r["category"] == cat for r in records) for cat in CATEGORIES},
                "year_range": [min(r["year"] for r in records), max(r["year"] for r in records)],
                "files": files, "successful_api_pages": len(self.state["pages"]),
                "http_attempts": len(self.state["attempts"]),
                "failed_http_attempts": sum(x.get("result") != "success" for x in self.state["attempts"]),
                "request_interval_seconds": self.delay, "timeout_seconds": self.timeout,
                "retries_per_request": self.retries, "max_pages_per_category": self.max_pages,
                "queries": [{k: v for k, v in page.items() if k not in {"file", "sha256"}}
                            for page in self.state["pages"]],
                "request_log": self.state["attempts"],
                "url_access_check": {"status": "not_run", "note": "运行 validate_data.py --check-urls 4 后记录实际结果"}}

    def write_card(self, stats):
        files = stats["files"]
        dev = files.get("papers_dev.jsonl", {}).get("records", 0)
        final = files.get("papers_final.jsonl", {}).get("records", 0)
        distribution = "\n".join(f"| {cat} | {n} |" for cat, n in stats["categories"].items())
        text = f"""# arXiv 论文元数据说明

数据来自 [arXiv](https://arxiv.org/)，仅通过[官方 API]({API})获取 Atom/XML 元数据，无 PDF 下载、无网页抓取。
参见[API 手册](https://info.arxiv.org/help/api/user-manual.html)与[使用条款](https://info.arxiv.org/help/api/tou.html)。

## 采集范围与时间

- 起始时间（UTC）：{stats['collection_started_at_utc']}；本次导出：{stats['generated_at_utc']}。
- 当前交付阶段：{stats['stage']}。开发版 {dev} 篇，正式版 {final} 篇。
- 查询 `cat:cs.IR`、`cat:cs.CL`、`cat:cs.LG`、`cat:cs.AI`，分别与
  `submittedDate:[199101010000 TO {stats['query_cutoff_utc']}]` 取 AND。
- `sortBy=submittedDate`、`sortOrder=descending`；通过 `start` 和 `max_results` 分页。
- 截止时间在第一次运行时固定（UTC 分钟），支持缓存续采。每页的实际参数、URL、时间及请求结果见 `data/data_stats.json`。
- 全部请求串行，相邻请求结束后至少等待 {self.delay} 秒；超时 {self.timeout} 秒，单个请求最多重试 {self.retries} 次。
- 以 API 的 `arxiv:primary_category` 为准，只保留四个指定主分类，不将交叉分类强行改成主分类。

## 字段与文件

全部 JSONL 均为 UTF-8 无 BOM，一行一个完整 JSON 对象，无空行，恰好八个字段。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| paper_id | string | 去除版本号的唯一 arXiv ID |
| title | string | 原始可读标题，保留大小写和语言 |
| abstract | string | 原始完整摘要，仅清理格式 |
| authors | list[string] | 作者姓名数组 |
| year | integer | API `published`（首次提交）年份，不使用更新时间 |
| category | string | API 提供的真实主分类 |
| url | string | `https://arxiv.org/abs/` 加无版本号 ID |
| source | string | 固定为 `arxiv` |

开发版用于联调；最终完成后 B、C 统一读取 `data/papers.jsonl`。
`papers.jsonl` 是 `papers_final.jsonl` 的逐字节副本；实际 SHA-256 见统计文件。

## 清洗与实际计数

去除 ID 的 `v1/v2` 等后缀后全局去重；解码 HTML 实体、去除明确 HTML 标签、合并换行及空白。
不分词、不转小写、不去停用词、不提取词干、不改写/翻译摘要、不生成 `search_text`。
标题或摘要缺失、作者为空、ID/日期不合法、文本含无法消除的编码异常时丢弃记录。

- API 实际返回条目：{stats['raw_entries_received']}。
- 去重丢弃：{stats['duplicates_removed']}（只统计重复出现的有效目标论文）。
- 缺标题丢弃：{stats['missing_title_removed']}；缺摘要丢弃：{stats['missing_abstract_removed']}。
- 主分类不在范围内：{stats['outside_primary_category_removed']}。
- ID 异常：{stats['invalid_id_removed']}；日期异常：{stats['invalid_published_removed']}；作者缺失：{stats['missing_authors_removed']}；文本异常：{stats['invalid_text_removed']}。
- 唯一有效候选：{stats['unique_valid_candidates']}，其中 {stats['valid_candidates_not_selected']} 篇因类别配额未进入当前版本。

上述丢弃原因按固定检查顺序互斥计数；原始条目数 = 丢弃计数之和 + 唯一有效候选数。
开发版和正式版使用同一采集会话、复用已下载 XML，未为生成三个文件重复抓取。
开发版在正式采集前固定，正式版按每类配额从当时已有候选中选择最近首次提交的论文。
因后续查询可发现交叉分类论文，开发版不承诺一定是正式版的子集。

## 当前版本分布

| 主分类 | 篇数 |
| --- | ---: |
{distribution}

当前版本总数 {stats['total_records']}，年份范围 {stats['year_range'][0]}–{stats['year_range'][1]}。

## 验证与局限

执行 `python scripts/validate_data.py` 检查格式、字段、类型、ID、分类、文本、统计、正式文件一致性。
加 `--check-urls 4` 以固定随机种子分层抽样，每类一篇，串行访问 URL；实际状态保存在统计文件的 `url_access_check`。
URL 结构通过不代表已完成网络访问；网络失败会如实记录为未验证，不冒报成功。

数据偏向最近提交的四个 CS 主分类，不代表整个 arXiv，也不是严格随机或全量样本。
API 索引可能延迟或变化；固定查询截止时间与分页不能提供服务端事务快照。每类满足配额即停止，不保证获取该类所有最新论文。
论文可能是未经过同行评审的预印本；作者提供的摘要中可保留 LaTeX 公式和技术符号。
版本号被去除，入口链接通常指向当前最新版本；首次提交年份仍来自 `published`。
编码检查可发现替换字符和控制字符，不能保证发现所有语义上不易识别的乱码。
描述性元数据按 arXiv API 条款可使用（CC0）；本项目不打包论文正文，正文版权另属作者或出版方。

用途：供 B 使用标题和摘要建立 BM25 索引、按年份和主分类筛选；供 C 展示标题、作者、摘要和论文入口。
读取无需数据库、网络服务或后台采集进程。
"""
        path = self.root / "docs" / "data_card.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="输出项目目录；同目录自动续采")
    parser.add_argument("--stage", choices=("sample", "dev", "final"), default="final")
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--dev-size", type=int, default=150)
    parser.add_argument("--final-size", type=int, default=800)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--delay", type=float, default=3.1)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--retries", type=int, default=2, help="首次请求之外的重试次数")
    parser.add_argument("--max-pages", type=int, default=40, help="每类累计成功页数上限")
    args = parser.parse_args()
    if not (100 <= args.dev_size <= 200 and 500 <= args.final_size <= 1000
            and 4 <= args.sample_size <= 50 and 1 <= args.page_size <= 1000
            and 0 <= args.retries <= 5 and 0 < args.timeout <= 60 and args.max_pages > 0
            and 3 <= args.delay <= 60):
        parser.error("请检查数量、页长、超时、重试次数和请求间隔；--delay 不得少于 3 秒。")
    collector = None
    try:
        collector = Collector(args.root, args.delay, args.timeout, args.retries, args.max_pages)
        if args.stage == "final" and not (args.root / "data/papers_dev.jsonl").exists():
            collector.fill(args.dev_size, args.page_size)
            collector.export("dev", args.sample_size, args.dev_size, args.final_size)
            from validate_data import validate_jsonl
            _, errors = validate_jsonl(args.root / "data/papers_dev.jsonl", 100, 200)
            if errors:
                raise ValueError("开发版验证失败：" + "; ".join(errors[:5]))
            print("开发版格式验证通过，继续正式版采集。", flush=True)
        size = {"sample": args.sample_size, "dev": args.dev_size, "final": args.final_size}[args.stage]
        collector.fill(size, args.page_size)
        collector.export(args.stage, args.sample_size, args.dev_size, args.final_size)
    except (Exception, KeyboardInterrupt) as exc:
        failure = {"status": "failed", "stage": args.stage, "at_utc": utc_now(),
                   "error": str(exc) or "interrupted", "cache_preserved": True}
        if collector:
            failure["unique_valid_candidates"] = len(collector.pool)
        write_json(args.root / ".cache/last_failure.json", failure)
        print(f"采集停止：{failure['error']}。修复网络后重运行相同命令可续采。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
