# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


class StubTool:
    def __init__(self, *args, **kwargs):
        pass

    def create_text_message(self, text):
        return {"kind": "text", "text": text}

    def create_blob_message(self, blob, meta):
        return {"kind": "blob", "blob": blob, "meta": meta}


dify = types.ModuleType("dify_plugin")
dify.Tool = StubTool
sys.modules.setdefault("dify_plugin", dify)
entities = types.ModuleType("dify_plugin.entities")
tool_module = types.ModuleType("dify_plugin.entities.tool")
tool_module.ToolInvokeMessage = dict
sys.modules.setdefault("dify_plugin.entities", entities)
sys.modules.setdefault("dify_plugin.entities.tool", tool_module)

import render_page


class ShowcaseTests(unittest.TestCase):
    def test_business_report_can_embed_all_172_libraries(self):
        tool = render_page.RenderPageTool()
        spec = {
            "version": 1,
            "doc": {"title": "年度报告"},
            "blocks": [{"type": "text", "text": "正文"}],
        }
        messages = list(
            tool._invoke(
                {
                    "spec": json.dumps(spec, ensure_ascii=False),
                    "include_all_libraries": True,
                    "filename": "report.html",
                }
            )
        )
        self.assertEqual(["text", "blob"], [item["kind"] for item in messages])
        html = messages[1]["blob"].decode("utf-8")
        self.assertIn("ps-showcase", html)
        self.assertIn("ps-catalog-overlay", html)
        self.assertIn("172 个库全部封装在本报告中", html)
        self.assertIn("DecompressionStream", html)
        self.assertIn("pagespec-catalog-ready", html)
        self.assertIn("pagespec-catalog-fail", html)
        self.assertLess(len(messages[1]["blob"]), 30_000_000)
        self.assertNotIn("html,body{overflow-x:hidden", html)
        self.assertIn("正文", html)

    def test_wide_table_is_local_scroll_not_root_clipping(self):
        tool = render_page.RenderPageTool()
        spec = {
            "version": 1,
            "blocks": [
                {
                    "type": "table",
                    "columns": ["A", "B", "C", "D", "E", "F"],
                    "rows": [[1, 2]],
                    "features": ["search", "sort"],
                }
            ],
        }
        messages = list(tool._invoke({"spec": json.dumps(spec)}))
        html = messages[1]["blob"].decode("utf-8")
        self.assertIn("overscroll-behavior-inline:contain", html)
        self.assertIn("min-width:840px", html)
        self.assertIn("行字段数 2 与列数 6 不一致", html)
        self.assertNotIn("html,body{overflow-x:hidden", html)


if __name__ == "__main__":
    unittest.main()
