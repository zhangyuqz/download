# -*- coding: utf-8 -*-
"""Regression matrix for Dify/template transports and tolerant guessing."""
from __future__ import annotations

import json
import os
import sys
import unittest


TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import pagespec_transport as transport
import pagespec_validate as validate


BASE = {"version": 1, "blocks": [{"type": "text", "text": "ok"}]}
BASE_JSON = json.dumps(BASE, ensure_ascii=False, separators=(",", ":"))


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, where, message, suggestion=""):
        self.items.append({"level": level, "where": where, "message": message,
                           "suggestion": suggestion})


def parsed(raw):
    outcome = transport.parse_spec(raw)
    if outcome.error:
        raise AssertionError(outcome.error)
    return outcome


class DifyTransportMatrixTests(unittest.TestCase):
    def test_native_runtime_types(self):
        self.assertEqual(BASE, parsed(BASE).value)
        self.assertEqual("text", parsed(BASE["blocks"]).value["blocks"][0]["type"])
        self.assertEqual(BASE, parsed(BASE_JSON.encode("utf-8")).value)
        none = parsed(None)
        self.assertEqual("callout", none.value["blocks"][0]["type"])
        self.assertTrue(none.events)
        native_nan = parsed({"version": 1, "blocks": [{"type": "text", "text": "ok"}],
                             "metric": float("nan")})
        self.assertIsNone(native_nan.value["metric"])
        self.assertTrue(any("native 非有限数字" in event["message"] for event in native_nan.events))

    def test_bytes_gb18030_and_nul_padded_ui_copy(self):
        gb = '{"version":1,"blocks":[{"type":"text","text":"中文"}]}'.encode("gb18030")
        outcome = parsed(gb)
        self.assertEqual("中文", outcome.value["blocks"][0]["text"])
        self.assertTrue(any("GB18030" in event["message"] for event in outcome.events))

        padded = '\x00{"version":1,"blocks":[{"type":"text","text":"ok"}]}\x00'
        outcome = parsed(padded)
        self.assertEqual("ok", outcome.value["blocks"][0]["text"])
        self.assertTrue(any("NUL" in event["message"] for event in outcome.events))

    def test_dify_named_wrapper_priority_and_nested_paths(self):
        forms = [
            {"output": BASE_JSON},
            {"outputs": {"output": BASE_JSON}},
            {"data": {"outputs": {"output": BASE_JSON}}},
            {"data": {"output": BASE_JSON}},
            {"result": {"output": BASE_JSON}},
            {"body": {"data": {"outputs": {"output": BASE_JSON}}}},
            {"spec": BASE_JSON, "status": "ok"},
            {"payload": {"page_spec": BASE_JSON}},
        ]
        for index, form in enumerate(forms):
            with self.subTest(index=index):
                outcome = parsed(form)
                self.assertEqual(BASE, outcome.value)
                self.assertTrue(any("包装" in event["message"] for event in outcome.events))

        conflict = parsed({"output": BASE_JSON,
                           "outputs": {"output": '{"version":1,"blocks":[]}'}})
        self.assertEqual(BASE, conflict.value)
        self.assertTrue(any("多个不同载荷" in event["message"] and "选择=output" in event["message"]
                            for event in conflict.events))

    def test_screenshot_shape_and_string_escape_layers(self):
        # Dify 1.7.1 screenshot shape: outer JSON object's output is a JSON string.
        outer = json.dumps({"output": json.dumps(BASE, ensure_ascii=False, indent=2)},
                           ensure_ascii=False)
        self.assertEqual(BASE, parsed(outer).value)

        double = json.dumps(json.dumps(BASE_JSON))
        self.assertEqual(BASE, parsed(double).value)

        escaped = BASE_JSON.replace('"', '\\"').replace("{", "{\\n", 1)
        self.assertEqual(BASE, parsed(escaped).value)

    def test_all_bounded_text_repair_families(self):
        variants = {
            "markdown fence": f"```json\n{BASE_JSON}\n```",
            "prose fragment": f"这是说明：{BASE_JSON}。",
            "line comment": "// generated\n" + BASE_JSON,
            "block comment": "/* generated */" + BASE_JSON,
            "html entities": BASE_JSON.replace('"', "&quot;"),
            "trailing commas": '{"version":1,"blocks":[{"type":"text","text":"ok",}],}',
            "python single quotes": "{'version':1,'blocks':[{'type':'text','text':'ok'}]}",
            "python bool/none": "{'version':1,'blocks':[{'type':'text','text':'ok'}],'x':None,'y':True}",
            "python tuple": "{'version':1,'blocks':({'type':'text','text':'ok'},)}",
            "bare keys": '{version:1,blocks:[{type:"text",text:"ok"}]}',
            "missing comma": '{"version":1 "blocks":[{"type":"text","text":"ok"}]}',
            "truncated closers": '{"version":1,"blocks":[{"type":"text","text":"ok"}',
            "smart/fullwidth": '｛“version”：1，“blocks”：［｛“type”：“text”，“text”：“ok”｝］｝',
        }
        for name, raw in variants.items():
            with self.subTest(name=name):
                outcome = parsed(raw)
                self.assertEqual(BASE["version"], outcome.value["version"])
                self.assertEqual(BASE["blocks"], outcome.value["blocks"])
                self.assertTrue(outcome.events, name)

    def test_duplicates_and_non_finite_are_last_wins_and_visible(self):
        duplicate = parsed('{"version":9,"version":1,"blocks":[]}')
        self.assertEqual(1, duplicate.value["version"])
        self.assertTrue(any("last-wins" in event["message"] for event in duplicate.events))

        non_finite = parsed(
            '{"version":1,"blocks":[{"type":"progress","items":['
            '{"label":"x","value":NaN},{"label":"y","value":Infinity}]}]}'
        )
        values = [item["value"] for item in non_finite.value["blocks"][0]["items"]]
        self.assertEqual([None, None], values)
        self.assertEqual(2, sum("归一为 null" in event["message"] for event in non_finite.events))

    def test_mixed_text_ambiguity_is_scored_not_rejected(self):
        raw = ('候选一 {"version":1,"blocks":[]} 候选二 '
               '{"version":1,"blocks":[{"type":"divider"}]}')
        outcome = parsed(raw)
        self.assertEqual([], outcome.value["blocks"])
        message = next(event["message"] for event in outcome.events if "多个可恢复结果" in event["message"])
        self.assertIn("候选=", message)
        self.assertIn("选择=", message)
        self.assertIn("原因=", message)
        self.assertIn("置信度=", message)

    def test_plain_human_text_is_content_not_a_rejection(self):
        outcome = parsed("这是一段普通正文，不是 JSON")
        self.assertEqual("text", outcome.value["blocks"][0]["type"])
        self.assertEqual("这是一段普通正文，不是 JSON", outcome.value["blocks"][0]["text"])
        self.assertTrue(any("正文文本" in event["message"] for event in outcome.events))

    def test_wrapper_layers_are_bounded_at_sixty_four_and_ninth_string_layer_works(self):
        wrapped = BASE
        for _ in range(transport.MAX_WRAPPER_LAYERS):
            wrapped = {"data": wrapped}
        self.assertIsNone(transport.parse_spec(wrapped).error)
        self.assertIn("包装层数超过", transport.parse_spec({"data": wrapped}).error)

        # A complete JSON string grows exponentially, so the independent
        # 2 MB input ceiling becomes effective before a synthetic 64-layer
        # PageSpec can exist.  The former failure boundary was layer nine.
        self.assertEqual(64, transport.MAX_STRING_LAYERS)
        encoded = BASE_JSON
        for _ in range(9):
            encoded = json.dumps(encoded)
        self.assertIsNone(transport.parse_spec(encoded).error)

    def test_executable_python_is_never_run(self):
        marker = os.path.join(os.path.dirname(__file__), "SHOULD_NOT_EXIST")
        if os.path.exists(marker):
            os.unlink(marker)
        raw = f"__import__('pathlib').Path({marker!r}).write_text('x')"
        outcome = parsed(raw)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual("text", outcome.value["blocks"][0]["type"])


class SemanticGuessingTests(unittest.TestCase):
    def normalize(self, spec):
        report = Report()
        value, error = validate.normalize_spec(spec, report)
        self.assertIsNone(error)
        return value, report.items

    def test_alias_conflict_prefers_canonical_and_audits_decision(self):
        value, audit = self.normalize({
            "version": 1,
            "blocks": [{"type": "text", "text": "canonical", "内容": "alias"}],
        })
        self.assertEqual("canonical", value["blocks"][0]["text"])
        message = next(item["message"] for item in audit if "字段别名冲突" in item["message"])
        self.assertIn("候选=", message)
        self.assertIn("选择=", message)
        self.assertIn("原因=", message)
        self.assertIn("置信度=", message)

    def test_missing_and_misspelled_types_are_inferred(self):
        value, audit = self.normalize({
            "version": "PageSpec/1",
            "blocks": [
                {"text": "plain"},
                {"tyep": "haeding", "title": "Heading", "level": "2"},
                {"rows": [[1]], "columns": ["A"]},
                {"code": "graph LR; A-->B"},
                {"items": [{"label": "p", "value": 10, "max": 100}]},
            ],
        })
        self.assertEqual(["text", "heading", "table", "mermaid", "progress"],
                         [block["type"] for block in value["blocks"]])
        self.assertGreaterEqual(sum("块类型需要猜测" in item["message"] for item in audit), 5)

    def test_alias_conflict_inside_graph_no_longer_kills_block(self):
        value, audit = self.normalize({
            "version": 1,
            "blocks": [{"type": "graph", "nodes": [{"id": "a"}, {"id": "b"}],
                        "edges": [{"from": "a", "source": "b", "to": "b"}]}],
        })
        self.assertEqual("graph", value["blocks"][0]["type"])
        self.assertEqual("a", value["blocks"][0]["edges"][0]["from"])
        self.assertTrue(any("规范字段优先" in item["message"] for item in audit))

    def test_empty_and_future_version_still_make_a_page(self):
        value, audit = self.normalize({"version": 99, "blocks": []})
        self.assertEqual(1, value["version"])
        self.assertEqual("callout", value["blocks"][0]["type"])
        self.assertTrue(any(item["level"] in {"WARN", "SKIP"} for item in audit))


if __name__ == "__main__":
    unittest.main()
