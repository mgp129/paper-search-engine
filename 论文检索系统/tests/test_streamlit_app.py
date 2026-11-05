from __future__ import annotations

import unittest
from pathlib import Path

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # Allows the pure data/API tests to run before the UI dependency is installed.
    AppTest = None


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


@unittest.skipIf(AppTest is None, "streamlit is not installed")
class StreamlitAppTests(unittest.TestCase):
    def new_app(self) -> AppTest:
        app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
        self.assertFalse(app.exception)
        return app

    def test_initial_page_browses_real_dataset(self) -> None:
        app = self.new_app()
        self.assertEqual(app.text_input[0].value, "")
        self.assertTrue(
            any("浏览全部 800 篇" in item.value for item in app.info)
        )
        self.assertTrue(app.button)

    def test_keyword_search_renders_cards(self) -> None:
        app = self.new_app()
        app.text_input[0].set_value("dense retrieval")
        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(any("共找到" in item.value and "篇" in item.value for item in app.success))
        self.assertTrue(any(button.label == "查看详情" for button in app.button))
        self.assertFalse(any("没有匹配的论文" in item.value for item in app.warning))

    def test_no_result_message(self) -> None:
        app = self.new_app()
        app.text_input[0].set_value("qxzvbnmqxzvbnm")
        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any("没有匹配的论文" in item.value for item in app.warning)
        )

    def test_category_filter_browse(self) -> None:
        app = self.new_app()
        app.multiselect[0].set_value(["cs.IR"])
        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any("浏览全部 200 篇" in item.value for item in app.info)
        )

    def test_pagination_and_detail_page(self) -> None:
        app = self.new_app()
        app.text_input[0].set_value("language model")
        app.run()

        next_button = next(button for button in app.button if button.label == "下一页")
        next_button.click()
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("2 /" in item.value for item in app.markdown))

        detail_button = next(button for button in app.button if button.label == "查看详情")
        detail_button.click()
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("返回结果列表" in button.label for button in app.button))
        self.assertTrue(any(markdown.value.startswith("**arXiv ID**") for markdown in app.markdown))
        self.assertTrue(any(markdown.value.startswith("**原文链接**") for markdown in app.markdown))

    def test_out_of_range_page_is_clamped(self) -> None:
        app = self.new_app()
        app.session_state["page"] = 999
        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["page"], 80)
        self.assertEqual(
            sum(button.label == "查看详情" for button in app.button),
            10,
        )


if __name__ == "__main__":
    unittest.main()
