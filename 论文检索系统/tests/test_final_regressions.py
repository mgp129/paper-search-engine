from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import search_api
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class FinalRegressions(unittest.TestCase):
    def test_detail_return_preserves_search_filters_and_page(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        app.text_input(key="query").set_value("dense retrieval").run()
        app.multiselect(key="categories").set_value(["cs.IR"]).run()
        app.checkbox(key="require_all").check().run()
        app.selectbox(key="sort").set_value("title").run()
        app.selectbox(key="page_size").set_value(5).run()
        next(b for b in app.button if b.label == "下一页").click().run()
        expected_ids = [b.key for b in app.button if b.label == "查看详情"]
        next(b for b in app.button if b.label == "查看详情").click().run()
        self.assertFalse(app.exception)
        next(b for b in app.button if "返回结果列表" in b.label).click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key="query").value, "dense retrieval")
        self.assertEqual(app.multiselect(key="categories").value, ["cs.IR"])
        self.assertTrue(app.checkbox(key="require_all").value)
        self.assertEqual(app.selectbox(key="sort").value, "title")
        self.assertEqual(app.selectbox(key="page_size").value, 5)
        self.assertEqual(app.session_state["page"], 2)
        self.assertEqual([b.key for b in app.button if b.label == "查看详情"], expected_ids)

    def test_missing_formal_data_does_not_fall_back_to_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "papers_final.jsonl"
            with patch.object(search_api, "_DATA_PATH", None), patch.object(search_api, "DATA_CANDIDATES", [missing]):
                with self.assertRaises(search_api.EngineInitError):
                    search_api.data_path()
        self.assertNotIn("sample_papers.jsonl", [p.name for p in search_api.DATA_CANDIDATES])

    def test_browse_does_not_display_fake_zero_relevance(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertFalse(app.metric)
        app.text_input(key="query").set_value("dense retrieval").run()
        self.assertTrue(app.metric)


if __name__ == "__main__":
    unittest.main()
