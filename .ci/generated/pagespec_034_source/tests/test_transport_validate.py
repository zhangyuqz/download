# -*- coding: utf-8 -*-
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


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, where, message, suggestion=""):
        self.items.append({
            "level": level,
            "where": where,
            "message": message,
            "suggestion": suggestion,
        })


def normalize(spec):
    report = Report()
    value, error = validate.normalize_spec(spec, report)
    return value, error, report


def walk_blocks(blocks):
    for block in blocks:
        yield block
        if block.get("type") in {"section", "card"}:
            yield from walk_blocks(block.get("blocks", []))
        elif block.get("type") == "columns":
            for group in block.get("blocks", []):
                yield from walk_blocks(group)
        elif block.get("type") in {"tabs", "collapse"}:
            for item in block.get("items", []):
                yield from walk_blocks(item.get("blocks", []))


class TransportTests(unittest.TestCase):
    def test_strict_json_is_zero_change(self):
        raw = '{"version":1,"blocks":[{"type":"text","text":"ok"}]}'
        outcome = transport.parse_spec(raw)
        self.assertIsNone(outcome.error)
        self.assertEqual([], outcome.events)
        self.assertEqual(1, outcome.value["version"])

    def test_json_duplicate_keys_are_typed_last_wins_and_audited(self):
        same = transport.parse_spec('{"version":1,"version":1,"blocks":[]}')
        self.assertIsNone(same.error)
        self.assertTrue(any("重复键" in item["message"] for item in same.events))

        for raw in (
            '{"version":1,"version":true,"blocks":[]}',
            '{"version":1,"version":1.0,"blocks":[]}',
        ):
            with self.subTest(raw=raw):
                outcome = transport.parse_spec(raw)
                self.assertIsNone(outcome.error)
                self.assertTrue(any("last-wins" in item["message"] for item in outcome.events))
        self.assertIs(True, transport.parse_spec(
            '{"version":1,"version":true,"blocks":[]}').value["version"])
        self.assertEqual(1.0, transport.parse_spec(
            '{"version":1,"version":1.0,"blocks":[]}').value["version"])

    def test_python_literal_duplicates_are_not_silently_collapsed(self):
        same = transport.parse_spec("{'version': 1, 'version': 1, 'blocks': []}")
        self.assertIsNone(same.error)
        self.assertTrue(any("重复键" in item["message"] for item in same.events))
        conflict = transport.parse_spec("{'version': 1, 'version': True, 'blocks': []}")
        self.assertIsNone(conflict.error)
        self.assertIs(True, conflict.value["version"])
        self.assertTrue(any("重复键冲突" in item["message"] and "选择=最后一个" in item["message"]
                            for item in conflict.events))

    def test_bounded_repairs_are_audited(self):
        fenced = transport.parse_spec('```json\n{"version":1,"blocks":[]}\n```')
        self.assertIsNone(fenced.error)
        self.assertTrue(fenced.events)
        trailing = transport.parse_spec('{"version":1,"blocks":[],}')
        self.assertIsNone(trailing.error)
        self.assertTrue(any("尾随逗号" in item["message"] for item in trailing.events))
        literal = transport.parse_spec("{'version': 1, 'blocks': []}")
        self.assertIsNone(literal.error)
        self.assertTrue(any("Python" in item["message"] for item in literal.events))

    def test_truncation_and_ambiguity_are_guessed_and_audited(self):
        truncated = transport.parse_spec('{"version":1,"blocks":[')
        self.assertIsNone(truncated.error)
        self.assertTrue(any("补全" in item["message"] for item in truncated.events))
        ambiguous = transport.parse_spec(
            '候选一 {"version":1,"blocks":[]} 候选二 {"version":1,"blocks":[{"type":"divider"}]}'
        )
        self.assertIsNone(ambiguous.error)
        self.assertEqual([], ambiguous.value["blocks"])
        self.assertTrue(any(all(token in item["message"] for token in
                                ("候选=", "选择=", "原因=", "置信度="))
                            for item in ambiguous.events))

    def test_wrapper_limits_are_exact_and_separate(self):
        base = {"version": 1, "blocks": []}
        value = base
        for _ in range(transport.MAX_WRAPPER_LAYERS):
            value = {"data": value}
        accepted = transport.parse_spec(json.dumps(value))
        self.assertIsNone(accepted.error)
        self.assertEqual(base, accepted.value)

        value = {"data": value}
        rejected = transport.parse_spec(json.dumps(value))
        self.assertIn("包装层数超过", rejected.error)

        # Complete JSON-string wrapping grows exponentially; verify the old
        # ninth-layer failure is gone while the independent 2 MB ceiling stays
        # authoritative for physically larger inputs.
        self.assertEqual(64, transport.MAX_STRING_LAYERS)
        text = json.dumps(base)
        for _ in range(9):
            text = json.dumps(text)
        accepted = transport.parse_spec(text)
        self.assertIsNone(accepted.error)
        self.assertEqual(base, accepted.value)

    def test_duplicate_conflict_inside_string_wrapper_is_not_hidden(self):
        wrapped = json.dumps('{"version":1,"version":true,"blocks":[]}')
        outcome = transport.parse_spec(wrapped)
        self.assertIsNone(outcome.error)
        self.assertIs(True, outcome.value["version"])
        self.assertTrue(any("last-wins" in item["message"] for item in outcome.events))

    def test_raw_dict_still_obeys_content_budget(self):
        outcome = transport.parse_spec({"version": 1, "blocks": [], "x": "a" * (transport.MAX_SPEC_BYTES + 1)})
        self.assertIn("超过", outcome.error)


class ValidationTests(unittest.TestCase):
    def test_canonical_minimal_text_has_no_audit_noise(self):
        result, error, report = normalize({
            "version": 1,
            "blocks": [{"type": "text", "text": "hello"}],
        })
        self.assertIsNone(error)
        self.assertEqual([], report.items)
        self.assertEqual("hello", result["blocks"][0]["text"])

    def test_version_is_tolerantly_guessed_to_v1(self):
        result, error, report = normalize({"version": True, "blocks": [{"type": "divider"}]})
        self.assertIsNone(error)
        self.assertEqual(1, result["version"])
        self.assertTrue(any("猜测" in item["message"] for item in report.items))
        result, error, report = normalize({"version": 1, "blocks": [{"type": "divider"}]})
        self.assertIsNone(error)
        self.assertEqual([], report.items)
        self.assertEqual(1, result["version"])
        result, error, report = normalize({"version": "1", "blocks": [{"type": "divider"}]})
        self.assertIsNone(error)
        self.assertTrue(report.items)

    def test_unicode_noncharacters_and_key_collisions(self):
        result, error, report = normalize({
            "version": 1,
            "blocks": [{"type": "text", "text": "x\U0001fffey\ud800z"}],
        })
        self.assertIsNone(error)
        self.assertEqual("x�y�z", result["blocks"][0]["text"])
        self.assertTrue(any(item["level"] == "WARN" for item in report.items))

        same_keys = {"version": 1, "blocks": [{"type": "divider"}], "\x00": 1, "�": 1}
        result, error, report = normalize(same_keys)
        self.assertIsNone(error)
        self.assertTrue(any("重复键" in item["message"] for item in report.items))
        conflict_keys = {"version": 1, "blocks": [{"type": "divider"}], "\x00": 1, "�": 2}
        result, error, report = normalize(conflict_keys)
        self.assertIsNone(error)
        self.assertTrue(any("冲突" in item["message"] and "选择=" in item["message"]
                            for item in report.items))

    def test_all_28_block_types_have_a_valid_minimum(self):
        blocks = [
            {"type": "heading", "text": "H"},
            {"type": "text", "text": "T"},
            {"type": "markdown", "text": "**M**"},
            {"type": "callout", "text": "C"},
            {"type": "quote", "text": "Q"},
            {"type": "kv", "items": [{"label": "a", "value": "b"}]},
            {"type": "tags", "items": ["a"]},
            {"type": "code", "code": "x=1"},
            {"type": "formula", "latex": "x^2"},
            {"type": "divider"},
            {"type": "stat_row", "items": [{"label": "a", "value": "1"}]},
            {"type": "table", "columns": ["a"], "rows": [[1]]},
            {"type": "chart", "kind": "bar", "categories": ["a"],
             "series": [{"data": [1]}]},
            {"type": "wordcloud", "items": [{"text": "a", "weight": 1}]},
            {"type": "graph", "nodes": [{"id": "a"}], "edges": []},
            {"type": "mermaid", "code": "graph LR; A-->B"},
            {"type": "timeline", "items": [{"time": "now", "title": "t"}]},
            {"type": "progress", "items": [{"label": "p", "value": 1}]},
            {"type": "calendar", "events": [{"date": "2026-07-18", "title": "e"}]},
            {"type": "image", "slot": 1},
            {"type": "gallery", "slots": [1]},
            {"type": "qrcode", "text": "q"},
            {"type": "barcode", "text": "ABC"},
            {"type": "section", "title": "s", "blocks": [{"type": "divider"}]},
            {"type": "card", "blocks": [{"type": "divider"}]},
            {"type": "columns", "blocks": [[{"type": "divider"}]]},
            {"type": "tabs", "items": [{"label": "t", "blocks": [{"type": "divider"}]}]},
            {"type": "collapse", "items": [{"label": "c", "blocks": [{"type": "divider"}]}]},
        ]
        self.assertEqual(validate._KNOWN_TYPES - {"catalog_demo"},
                         {block["type"] for block in blocks})
        result, error, report = normalize({"version": 1, "blocks": blocks})
        self.assertIsNone(error)
        self.assertFalse([b for b in walk_blocks(result["blocks"]) if b["type"].startswith("__")])
        self.assertEqual([], report.items)

    def test_invalid_block_is_local_and_fallback_is_visible(self):
        result, error, report = normalize({
            "version": 1,
            "blocks": [
                {"type": "future", "fallback": "old client text"},
                {"type": "text", "text": "still alive"},
            ],
        })
        self.assertIsNone(error)
        self.assertEqual("callout", result["blocks"][0]["type"])
        self.assertEqual("old client text", result["blocks"][0]["text"])
        self.assertEqual("text", result["blocks"][1]["type"])
        self.assertTrue(any(item["level"] == "WARN" for item in report.items))
        self.assertFalse(any(item["level"] == "SKIP" for item in report.items))

    def test_semantic_mismatches_become_error_blocks(self):
        bad_blocks = [
            {"type": "table", "columns": ["a", "b"], "rows": [[1]]},
            {"type": "progress", "items": [{"label": "p", "value": 101, "max": 100}]},
            {"type": "graph", "nodes": [], "edges": []},
            {"type": "calendar", "events": [{"date": "2026-02-30", "title": "x"}]},
            {"type": "image", "slot": 1, "width": "1px;display:none"},
            {"type": "chart", "kind": "radar", "series": [{"data": [1]}]},
            {"type": "chart", "kind": "pie", "series": [{"data": [1]}, {"data": [2]}]},
            {"type": "barcode", "format": "EAN13", "text": "1234567890123"},
            {"type": "mermaid", "code": 'graph LR; click A "javascript :alert(1)"'},
        ]
        result, error, report = normalize({"version": 1, "blocks": bad_blocks})
        self.assertIsNone(error)
        # Ambiguous semantic values are repaired; only the Mermaid network/
        # script escape is a hard safety rejection.
        self.assertEqual(["table", "progress", "graph", "calendar", "image",
                          "chart", "chart", "barcode", "__error__"],
                         [block["type"] for block in result["blocks"]])
        self.assertEqual(1, sum(i["level"] == "SKIP" for i in report.items))
        self.assertGreaterEqual(sum(i["level"] == "WARN" for i in report.items), 8)

    def test_number_strings_and_width_normalization_are_unambiguous(self):
        result, error, report = normalize({
            "version": 1,
            "blocks": [
                {"type": "progress", "items": [{"label": "p", "value": "1,234", "max": 2000}]},
                {"type": "image", "slot": 1, "width": "480"},
            ],
        })
        self.assertIsNone(error)
        self.assertEqual(1234.0, result["blocks"][0]["items"][0]["value"])
        self.assertEqual("480px", result["blocks"][1]["width"])
        self.assertGreaterEqual(len(report.items), 2)

        result, error, report = normalize({
            "version": 1,
            "blocks": [{"type": "progress", "items": [{"label": "p", "value": "1,2"}]}],
        })
        self.assertIsNone(error)
        self.assertEqual("progress", result["blocks"][0]["type"])
        self.assertEqual(1.2, result["blocks"][0]["items"][0]["value"])
        self.assertTrue(any(all(token in item["message"] for token in
                                ("候选=", "选择=", "原因=", "置信度="))
                            for item in report.items))

    def test_table_object_rows_are_complete_and_scalar(self):
        result, error, report = normalize({
            "version": 1,
            "blocks": [{
                "type": "table",
                "columns": [{"key": 1, "label": "one"}, {"key": "two", "label": "two"}],
                "rows": [{"1": "x"}],
            }],
        })
        self.assertIsNone(error)
        row = result["blocks"][0]["rows"][0]
        self.assertEqual("x", row[1])
        self.assertEqual("", row["two"])
        self.assertTrue(any(item["level"] == "WARN" for item in report.items))

        result, error, _ = normalize({
            "version": 1,
            "blocks": [{"type": "table", "columns": ["a"], "rows": [[{"x": 1}]]}],
        })
        self.assertIsNone(error)
        self.assertEqual("table", result["blocks"][0]["type"])
        self.assertEqual('{"x":1}', result["blocks"][0]["rows"][0][0])

        result, error, report = normalize({
            "version": 1,
            "blocks": [{
                "type": "table",
                "columns": [{"key": 1, "label": "number"}, {"key": "1", "label": "string"}],
                "rows": [{"1": "ambiguous"}],
            }],
        })
        self.assertIsNone(error)
        self.assertEqual("table", result["blocks"][0]["type"])
        self.assertEqual([1, "1__2"],
                         [column["key"] for column in result["blocks"][0]["columns"]])
        self.assertEqual({1: "", "1__2": "ambiguous"}, result["blocks"][0]["rows"][0])
        self.assertTrue(any("表格列 key 冲突" in item["message"] for item in report.items))

    def test_semantic_type_aliases_preserve_their_meaning(self):
        result, error, report = normalize({
            "version": 1,
            "blocks": [
                {"type": "chart_bar", "categories": ["a"], "series": [{"data": [1]}]},
                {"type": "graph_dagre", "nodes": [{"id": "a"}], "edges": []},
            ],
        })
        self.assertIsNone(error)
        self.assertEqual("bar", result["blocks"][0]["kind"])
        self.assertEqual("dagre", result["blocks"][1]["layout"])
        self.assertTrue(report.items)

    def test_nested_alias_conflicts_choose_canonical_and_are_audited(self):
        result, error, report = normalize({
            "version": 1,
            "blocks": [{
                "type": "graph",
                "nodes": [{"id": "a"}, {"id": "b"}],
                "edges": [{"from": "a", "source": "b", "to": "b"}],
            }],
        })
        self.assertIsNone(error)
        self.assertEqual("graph", result["blocks"][0]["type"])
        self.assertEqual("a", result["blocks"][0]["edges"][0]["from"])
        self.assertTrue(any("别名冲突" in item["message"] and "选择=" in item["message"]
                            for item in report.items))

    def test_chart_ignored_fields_warn_without_poisoning_valid_data(self):
        result, error, report = normalize({
            "version": 1,
            "blocks": [{
                "type": "chart", "kind": "scatter",
                "categories": {"not": "an array"},
                "series": [{"data": [[1, 2]]}],
            }],
        })
        self.assertIsNone(error)
        self.assertEqual("chart", result["blocks"][0]["type"])
        self.assertNotIn("categories", result["blocks"][0])
        self.assertTrue(any(item["level"] == "WARN" and "categories" in item["where"]
                            for item in report.items))

    def test_global_block_budget_truncates_once(self):
        blocks = [{"type": "divider"} for _ in range(validate.MAX_BLOCKS + 50)]
        result, error, report = normalize({"version": 1, "blocks": blocks})
        self.assertIsNone(error)
        self.assertEqual(validate.MAX_BLOCKS + 1, len(result["blocks"]))
        self.assertEqual(0, sum(item["level"] == "SKIP" for item in report.items))
        self.assertEqual("callout", result["blocks"][-1]["type"])
        self.assertTrue(any(item["level"] == "WARN" and "后续块未处理" in item["message"]
                            for item in report.items))


if __name__ == "__main__":
    unittest.main()
