"""仅用于边界/故障测试的合成 Atom；不会写入 data/ 或真实采集缓存。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_arxiv as fetch
from validate_data import validate_jsonl


def entry(pid="2401.12345v2", category="cs.IR", abstract="Complete abstract."):
    return f'''<entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <id>http://arxiv.org/abs/{pid}</id><title>Test Title</title><summary>{abstract}</summary>
      <published>2024-01-20T00:00:00Z</published><updated>2025-02-01T00:00:00Z</updated>
      <author><name>Alice A.</name></author><author><name>Bob B.</name></author>
      <category term="cs.IR"/><arxiv:primary_category term="{category}"/>
    </entry>'''


def feed(entries, start=0, total=None):
    if total is None:
        total = len(entries)
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">'
            f'<o:totalResults>{total}</o:totalResults><o:startIndex>{start}</o:startIndex>'
            + "".join(entries) + '</feed>').encode("utf-8")


class PipelineTests(unittest.TestCase):
    def test_cleaning_preserves_technical_content(self):
        self.assertEqual(fetch.clean_text("  A &amp;amp; B\n<div>Mixed Case</div>  x < y  <T> "),
                         "A & B Mixed Case x < y <T>")
        self.assertFalse(fetch.has_text_problem("x < y; 日本語; naïve; $\\alpha$"))
        self.assertTrue(fetch.has_text_problem("A &amp; B"))

    def test_version_and_first_submission_year(self):
        item, error = fetch.extract_record(ET.fromstring(entry()), "202611042359")
        self.assertIsNone(error)
        self.assertEqual(item["record"]["paper_id"], "2401.12345")
        self.assertEqual(item["record"]["year"], 2024)  # 不能误用 updated 的 2025
        self.assertEqual(item["record"]["authors"], ["Alice A.", "Bob B."])

    def test_primary_category_is_not_guessed_from_cross_listing(self):
        item, error = fetch.extract_record(ET.fromstring(entry(category="stat.ML")), "202611042359")
        self.assertIsNone(item)
        self.assertEqual(error, "outside_primary_category_removed")
        item, error = fetch.extract_record(ET.fromstring(entry(category="cs.CL")), "202611042359")
        self.assertEqual(item["record"]["category"], "cs.CL")

    def test_missing_abstract_is_rejected(self):
        item, error = fetch.extract_record(ET.fromstring(entry(abstract=" \n ")), "202611042359")
        self.assertIsNone(item)
        self.assertEqual(error, "missing_abstract_removed")

    def test_cache_resume_and_real_duplicate_accounting(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = fetch.Collector(directory)
            collector.state["query_cutoff_utc"] = "202611042359"
            raw = feed([entry(), entry(pid="2401.12345v1"), entry(category="stat.ML")])
            page = {"category": "cs.IR", "start": 0, "file": "test.xml",
                    "sha256": hashlib.sha256(raw).hexdigest()}
            (collector.cache / page["file"]).write_bytes(raw)
            collector.state["pages"].append(page)
            collector.save()
            resumed = fetch.Collector(directory, opener=lambda *a, **k: self.fail("缓存恢复不能请求网络"))
            self.assertEqual(len(resumed.pool), 1)
            self.assertEqual(resumed.counts["raw_entries_received"], 3)
            self.assertEqual(resumed.counts["duplicates_removed"], 1)
            self.assertEqual(resumed.counts["outside_primary_category_removed"], 1)
            (collector.cache / page["file"]).write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                fetch.Collector(directory)

    def test_retry_limit_and_nonretryable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            def unavailable(*args, **kwargs):
                raise HTTPError(fetch.API, 503, "unavailable", {}, None)
            collector = fetch.Collector(directory, retries=2, opener=unavailable)
            with patch.object(collector, "wait_for_request"), patch.object(fetch.time, "sleep"):
                with self.assertRaises(RuntimeError):
                    collector.request(fetch.API, 0)
            self.assertEqual(len(collector.state["attempts"]), 3)
        with tempfile.TemporaryDirectory() as directory:
            def denied(*args, **kwargs):
                raise HTTPError(fetch.API, 403, "denied", {}, None)
            collector = fetch.Collector(directory, retries=2, opener=denied)
            with patch.object(collector, "wait_for_request"), patch.object(fetch.time, "sleep"):
                with self.assertRaises(RuntimeError):
                    collector.request(fetch.API, 0)
            self.assertEqual(len(collector.state["attempts"]), 1)

    def test_request_spacing_survives_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = fetch.Collector(directory)
            # 模拟另一个进程 2.5 秒前结束，内存中的单调时钟不可复用。
            collector.state["last_request_finished_at_utc"] = (
                datetime.now(timezone.utc) - timedelta(seconds=2.5)).isoformat()
            with patch.object(fetch.time, "sleep") as sleep:
                collector.wait_for_request()
            self.assertGreaterEqual(sleep.call_args.args[0], 1.5)

    def test_validator_rejects_corruption_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.jsonl"
            item, _ = fetch.extract_record(ET.fromstring(entry()), "202611042359")
            row = item["record"]
            row["year"] = True
            row["authors"] = "Alice"
            row["url"] = "https://example.com/"
            path.write_text(json.dumps(row) + "\n\n" + json.dumps(row) + "\n", encoding="utf-8")
            _, errors = validate_jsonl(path, 1, 10)
            for message in ("year", "authors", "URL", "空行", "重复 paper_id"):
                self.assertTrue(any(message in e for e in errors), message)
            path.write_text('{"paper_id":"a","paper_id":"b"}\n', encoding="utf-8")
            _, errors = validate_jsonl(path, 1, 10)
            self.assertTrue(any("重复键" in e for e in errors))
            path.write_bytes(b"\xef\xbb\xbf{}\n")
            _, errors = validate_jsonl(path, 1, 10)
            self.assertTrue(any("BOM" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
