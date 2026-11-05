from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import search_api


class SearchApiTests(unittest.TestCase):
    def test_real_dataset_is_loaded(self) -> None:
        stats = search_api.engine_stats()
        self.assertEqual(stats["documents"], 800)
        self.assertEqual(stats["skipped_records"], 0)
        self.assertEqual(stats["data_file"], "papers_final.jsonl")

    def test_empty_query_browses_and_paginates(self) -> None:
        first = search_api.search("", page=1, page_size=10)
        second = search_api.search("", page=2, page_size=10)

        self.assertTrue(first["browse_mode"])
        self.assertEqual(first["total"], 800)
        self.assertEqual(len(first["hits"]), 10)
        self.assertEqual(len(second["hits"]), 10)
        self.assertTrue({p["id"] for p in first["hits"]}.isdisjoint(
            {p["id"] for p in second["hits"]}
        ))

    def test_keyword_filters_and_sorting(self) -> None:
        response = search_api.search(
            "dense retrieval",
            page=1,
            page_size=5,
            categories=["cs.IR"],
            sort_by="title",
        )

        self.assertGreater(response["total"], 0)
        self.assertLessEqual(len(response["hits"]), 5)
        self.assertTrue(all(p["categories"] == ["cs.IR"] for p in response["hits"]))
        titles = [p["title"].lower() for p in response["hits"]]
        self.assertEqual(titles, sorted(titles))

    def test_and_search_is_not_broader_than_or_search(self) -> None:
        or_result = search_api.search("dense retrieval", page_size=1)
        and_result = search_api.search(
            "dense retrieval",
            page_size=1,
            require_all=True,
        )
        self.assertLessEqual(and_result["total"], or_result["total"])

    def test_unknown_query_and_invalid_page(self) -> None:
        result = search_api.search("qxzvbnmqxzvbnm", page_size=10)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["hits"], [])

        with self.assertRaises(ValueError):
            search_api.search("", page=0)

    def test_detail_and_highlight(self) -> None:
        result = search_api.search("language model", page_size=1)
        hit = result["hits"][0]
        detail = search_api.get_paper(hit["id"])

        self.assertIsNotNone(detail)
        self.assertEqual(detail["id"], hit["id"])
        self.assertEqual(detail["abstract"], hit["abstract"])
        self.assertIsNone(search_api.get_paper("not-a-real-id"))

        highlighted = search_api._highlight("The AI chain is active.", ["ai"])
        self.assertEqual(highlighted, "The **AI** chain is active.")


if __name__ == "__main__":
    unittest.main()
