#!/usr/bin/env python3
"""离线验收 JSONL 与统计；可选按类别随机抽样检查论文入口。"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from fetch_arxiv import (ROOT, CATEGORIES, FIELDS, ID_PATTERN, has_text_problem,
                         sha256, utc_now, write_json)


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 对象内出现重复键：" + key)
        result[key] = value
    return result


def validate_jsonl(path, minimum, maximum):
    path = Path(path)
    errors, records, seen = [], [], set()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [], [f"无法读取 {path.name}: {exc}"]
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append("文件含 UTF-8 BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return [], [f"UTF-8 解码失败：{exc}"]
    if not text:
        errors.append("文件为空")
    # split('\n') 可以识别空白行；允许正常的单个末尾换行符。
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for number, line in enumerate(lines, 1):
        prefix = f"第 {number} 行："
        if not line.strip():
            errors.append(prefix + "不允许空行")
            continue
        try:
            row = json.loads(line, object_pairs_hook=reject_duplicate_keys)
        except (ValueError, TypeError) as exc:
            errors.append(prefix + "JSON 解析失败：" + str(exc))
            continue
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            errors.append(prefix + "必须是恰好包含规定八个字段的 JSON 对象")
            continue
        records.append(row)
        for field in ("paper_id", "title", "abstract", "category", "url", "source"):
            if not isinstance(row[field], str) or not row[field].strip():
                errors.append(prefix + field + " 必须是非空字符串")
        pid = row["paper_id"]
        if isinstance(pid, str):
            if not ID_PATTERN.fullmatch(pid) or re.search(r"v\d+$", pid):
                errors.append(prefix + "arXiv ID 格式错误或含版本号")
            if pid in seen:
                errors.append(prefix + "重复 paper_id：" + pid)
            seen.add(pid)
        authors = row["authors"]
        if not isinstance(authors, list) or not authors or not all(isinstance(s, str) and s.strip() for s in authors):
            errors.append(prefix + "authors 必须是非空字符串数组")
        if type(row["year"]) is not int or not 1991 <= row["year"] <= datetime.now(timezone.utc).year:
            errors.append(prefix + "year 必须是合理范围内的整数，不能是布尔值")
        if not isinstance(row["category"], str) or row["category"] not in CATEGORIES:
            errors.append(prefix + "category 不属于指定四类")
        if row["url"] != "https://arxiv.org/abs/" + str(pid):
            errors.append(prefix + "URL 必须是与 ID 对应的规范 arXiv 摘要页 URL")
        if row["source"] != "arxiv":
            errors.append(prefix + "source 必须为 arxiv")
        texts = [row["title"], row["abstract"]] + (authors if isinstance(authors, list) else [])
        for value in texts:
            if isinstance(value, str):
                if has_text_problem(value):
                    errors.append(prefix + "存在 HTML 标签、未解码实体、替换字符或控制字符")
                if value != " ".join(value.split()):
                    errors.append(prefix + "文本仍含换行、首尾空白或连续多余空格")
    if not minimum <= len(records) <= maximum:
        errors.append(f"记录数 {len(records)} 不在 [{minimum}, {maximum}] 范围内")
    present = {r["category"] for r in records if isinstance(r.get("category"), str)}
    if set(CATEGORIES) - present:
        errors.append("未覆盖四个指定主分类：" + ", ".join(sorted(set(CATEGORIES) - present)))
    return records, errors


def validate_stats(root, all_records):
    errors = []
    try:
        stats = json.loads((root / "data/data_stats.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, ["统计文件读取失败：" + str(exc)]
    rows = all_records["papers_final.jsonl"]
    counts = {cat: sum(r.get("category") == cat for r in rows) for cat in CATEGORIES}
    years = [r["year"] for r in rows if type(r.get("year")) is int]
    if stats.get("stage") != "final":
        errors.append("统计文件不是 final 阶段")
    if stats.get("total_records") != len(rows) or stats.get("categories") != counts:
        errors.append("统计总数/分类分布与正式数据不一致")
    if not years or stats.get("year_range") != [min(years), max(years)]:
        errors.append("统计年份范围与正式数据不一致")
    counters = ("duplicates_removed", "missing_title_removed", "missing_abstract_removed",
                "outside_primary_category_removed", "invalid_id_removed", "invalid_published_removed",
                "missing_authors_removed", "invalid_text_removed", "unique_valid_candidates",
                "valid_candidates_not_selected", "raw_entries_received")
    valid_counters = all(type(stats.get(k)) is int and stats[k] >= 0 for k in counters)
    if not valid_counters:
        errors.append("采集/清洗计数缺失或不是非负整数")
    else:
        removed = sum(stats[k] for k in counters[:8])
        if stats["raw_entries_received"] != removed + stats["unique_valid_candidates"]:
            errors.append("原始条目、丢弃条目、有效候选的计数不守恒")
        if stats["unique_valid_candidates"] != len(rows) + stats["valid_candidates_not_selected"]:
            errors.append("唯一候选与选中/未选中数量不一致")
    for filename, records in all_records.items():
        saved = stats.get("files", {}).get(filename, {})
        path = root / "data" / filename
        actual = {cat: sum(r.get("category") == cat for r in records) for cat in CATEGORIES}
        if saved.get("records") != len(records) or saved.get("categories") != actual:
            errors.append(filename + " 的文件级统计不一致")
        if path.exists() and saved.get("sha256") != sha256(path):
            errors.append(filename + " 的 SHA-256 与统计文件不一致")
    a, b = root / "data/papers.jsonl", root / "data/papers_final.jsonl"
    if not a.exists() or not b.exists() or a.read_bytes() != b.read_bytes():
        errors.append("papers.jsonl 与 papers_final.jsonl 并非逐字节一致")
    return stats, errors


def check_urls(records, count, seed, delay, timeout):
    rng = random.Random(seed)
    chosen = []
    for cat in CATEGORIES:
        group = [r for r in records if r["category"] == cat]
        if group and len(chosen) < count:
            chosen.append(rng.choice(group))
    remaining = [r for r in records if r not in chosen]
    chosen += rng.sample(remaining, min(count - len(chosen), len(remaining)))
    checks = []
    for i, row in enumerate(chosen):
        if i:
            time.sleep(delay)
        item = {"paper_id": row["paper_id"], "category": row["category"], "url": row["url"],
                "checked_at_utc": utc_now()}
        try:
            request = Request(row["url"], headers={"User-Agent": "paper-data-course/1.0 (URL validation)"})
            with urlopen(request, timeout=timeout) as response:
                body = response.read(512 * 1024).decode("utf-8", "replace")
                item["http_status"] = response.status
                item["resolved_url"] = response.url
                host = urlsplit(response.url).hostname
                if response.status != 200 or host not in {"arxiv.org", "export.arxiv.org", "www.arxiv.org"}:
                    raise ValueError("状态或最终域名异常")
                # 仅检测 ID 是否出现在页面中，不从 HTML 采集论文数据。
                if row["paper_id"] not in body:
                    raise ValueError("HTTP 200，但未在响应中确认论文 ID")
                item["result"] = "passed"
        except Exception as exc:
            item["result"], item["error"] = "unverified", str(exc)
        checks.append(item)
        print(f"URL {row['paper_id']}: {item['result']}", flush=True)
    passed = sum(c["result"] == "passed" for c in checks)
    return {"status": "passed" if passed == len(checks) and checks else "unverified",
            "sampling": "fixed-seed stratified by primary category; then random remainder",
            "seed": seed, "sample_size": len(checks), "passed": passed,
            "unverified": len(checks) - passed, "request_interval_seconds": delay,
            "checks": checks, "note": "仅对抽样 URL 进行实际访问，不代表所有链接当前均可访问。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--file", type=Path, help="单文件模式，用于小样本/开发版验收")
    parser.add_argument("--min-records", type=int, default=100)
    parser.add_argument("--max-records", type=int, default=200)
    parser.add_argument("--check-urls", type=int, default=0, help="默认不联网；建议 4，每类一篇")
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument("--delay", type=float, default=3.1)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not (0 <= args.check_urls <= 20 and 3 <= args.delay <= 60 and 0 < args.timeout <= 60
            and 0 <= args.min_records <= args.max_records):
        parser.error("抽样数须为 0–20，请求间隔至少 3 秒；请检查数量上下限/超时。")
    errors, summaries = [], {}
    if args.file:
        rows, errors = validate_jsonl(args.file, args.min_records, args.max_records)
        summaries[args.file.name] = {"records": len(rows)}
        stats = {}
    else:
        all_records = {}
        for filename, minimum, maximum in (("papers_dev.jsonl", 100, 200),
                                            ("papers_final.jsonl", 500, 1000), ("papers.jsonl", 500, 1000)):
            rows, file_errors = validate_jsonl(args.root / "data" / filename, minimum, maximum)
            all_records[filename] = rows
            errors.extend(filename + ": " + e for e in file_errors)
            summaries[filename] = {"records": len(rows)}
        stats, stat_errors = validate_stats(args.root, all_records)
        errors.extend(stat_errors)
        rows = all_records["papers_final.jsonl"]
    network = {"status": "not_run"}
    if args.check_urls and not errors:
        # 与刚刚完成的采集也保持间隔，避免紧邻一次 API 调用。
        time.sleep(args.delay)
        network = check_urls(rows, args.check_urls, args.seed, args.delay, args.timeout)
        if not args.file:
            stats["url_access_check"] = network
            write_json(args.root / "data/data_stats.json", stats)
    elif not args.file:
        network = stats.get("url_access_check", network)
    result = {"validated_at_utc": utc_now(), "offline_validation": "passed" if not errors else "failed",
              "files": summaries, "errors": errors, "url_access_check": network,
              "note": "离线验收不能单独证明来源真实性；数据来自实际官方 API 调用，统计记录查询及采集过程。"}
    if args.report:
        report_path = args.report
    elif args.file:
        report_path = args.root / ".cache" / (args.file.stem + "_validation.json")
    else:
        report_path = args.root / "docs/validation_report.json"
    write_json(report_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # URL 网络验证未完成时也使用非零退出码，防止误认为全部通过。
    return 1 if errors else (2 if args.check_urls and network["status"] != "passed" else 0)


if __name__ == "__main__":
    sys.exit(main())
