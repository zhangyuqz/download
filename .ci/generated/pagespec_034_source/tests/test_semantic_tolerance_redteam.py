# -*- coding: utf-8 -*-
"""Red-team regressions for deterministic semantic tolerance."""
from __future__ import annotations

import os
import sys
import unittest


TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

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


def normalize(blocks):
    report = Report()
    value, error = validate.normalize_spec({"version": 1, "blocks": blocks}, report)
    if error:
        raise AssertionError(error)
    return value, report.items


def assert_decision(test, items, fragment):
    message = next(item["message"] for item in items if fragment in item["message"])
    for token in ("候选=", "选择=", "原因=", "置信度="):
        test.assertIn(token, message)
    return message


class SemanticToleranceRedTeamTests(unittest.TestCase):
    def test_decimal_comma_is_guessed_without_breaking_thousands_grouping(self):
        value, audit = normalize([
            {"type": "progress", "items": [
                {"label": "decimal", "value": "1,2", "max": 10},
                {"label": "thousands", "value": "1,234", "max": 2000},
            ]},
        ])
        items = value["blocks"][0]["items"]
        self.assertEqual(1.2, items[0]["value"])
        self.assertEqual(1234.0, items[1]["value"])
        message = assert_decision(self, audit, "存在歧义")
        self.assertIn("小数逗号", message)
        self.assertEqual(1, sum("存在歧义" in item["message"] for item in audit))

    def test_table_key_collision_is_renamed_and_rows_are_mapped_by_exact_type(self):
        value, audit = normalize([{
            "type": "table",
            "columns": [
                {"key": 1, "label": "number"},
                {"key": "1", "label": "string"},
            ],
            "rows": [
                {"1": "string-only"},
                {"1__2": "explicit-renamed"},
                ["number", "string"],
            ],
        }])
        table = value["blocks"][0]
        self.assertEqual([1, "1__2"], [column["key"] for column in table["columns"]])
        self.assertEqual({1: "", "1__2": "string-only"}, table["rows"][0])
        self.assertEqual({1: "", "1__2": "explicit-renamed"}, table["rows"][1])
        self.assertEqual(["number", "string"], table["rows"][2])
        assert_decision(self, audit, "表格列 key 冲突")
        assert_decision(self, audit, "可对应多列")
        self.assertNotEqual("__error__", table["type"])

    def test_array_rows_are_padded_or_truncated_not_rejected(self):
        value, audit = normalize([{
            "type": "table",
            "columns": ["A", "B"],
            "rows": [["short"], ["one", "two", "discard"]],
        }])
        table = value["blocks"][0]
        self.assertEqual([["short", ""], ["one", "two"]], table["rows"])
        self.assertTrue(any("行尾补" in item["message"] for item in audit))
        self.assertTrue(any("截断其余" in item["message"] for item in audit))
        self.assertEqual("table", table["type"])

    def test_near_enum_typos_are_fuzzy_guessed_with_full_audit(self):
        value, audit = normalize([
            {"type": "callout", "text": "x", "style": "warnng"},
            {"type": "chart", "kind": "scater", "series": [{"data": [[1, 2]]}]},
            {"type": "table", "columns": [{"label": "A", "align": "rigth"}],
             "rows": [[1]], "features": ["serach"]},
        ])
        self.assertEqual("warning", value["blocks"][0]["style"])
        self.assertEqual("scatter", value["blocks"][1]["kind"])
        self.assertEqual("right", value["blocks"][2]["columns"][0]["align"])
        self.assertEqual(["search"], value["blocks"][2]["features"])
        guessed = [item for item in audit if "枚举值" in item["message"] and "需要猜测" in item["message"]]
        self.assertEqual(4, len(guessed))
        for item in guessed:
            for token in ("候选=", "选择=", "原因=", "置信度="):
                self.assertIn(token, item["message"])

    def test_arbitrary_doc_enum_and_boolean_never_poison_the_page(self):
        report = Report()
        value, error = validate.normalize_spec({
            "version": 1,
            "doc": {"theme": "solarized", "toc": "maybe", "unknown": 1},
            "blocks": [{"type": "callout", "text": "alive", "style": "purple"}],
        }, report)
        self.assertIsNone(error)
        self.assertEqual("dark", value["doc"]["theme"])
        self.assertFalse(value["doc"]["toc"])
        self.assertEqual("info", value["blocks"][0]["style"])
        for where in ("/doc/theme", "/doc/toc", "/doc/unknown", "/blocks/0/style"):
            self.assertTrue(any(item["level"] == "WARN" and item["where"] == where
                                for item in report.items), where)
        self.assertFalse(any(block["type"].startswith("__") for block in value["blocks"]))

    def test_huge_integer_and_numeric_bounds_are_bounded_and_audited(self):
        huge = 10 ** 10_000
        report = Report()
        value, error = validate.normalize_spec({
            "version": huge,
            "doc": {"title": huge},
            "blocks": [
                {"type": "heading", "text": "x", "level": huge},
                {"type": "image", "slot": -huge},
                {"type": "qrcode", "text": "x", "size": "999999 px"},
            ],
        }, report)
        self.assertIsNone(error)
        self.assertEqual(1, value["version"])
        self.assertEqual(4, value["blocks"][0]["level"])
        self.assertEqual(1, value["blocks"][1]["slot"])
        self.assertEqual(512, value["blocks"][2]["size"])
        self.assertIn("超大整数", value["doc"]["title"])
        self.assertGreaterEqual(sum(item["level"] == "WARN" for item in report.items), 5)

    def test_huge_integer_boolean_and_width_use_safe_defaults(self):
        huge = 10 ** 10_000
        report = Report()
        value, error = validate.normalize_spec({
            "version": 1,
            "doc": {"toc": huge},
            "blocks": [{"type": "image", "slot": 1, "width": huge}],
        }, report)
        self.assertIsNone(error)
        self.assertFalse(value["doc"]["toc"])
        self.assertNotIn("width", value["blocks"][0])
        for where in ("/doc/toc", "/blocks/0/width"):
            self.assertTrue(any(item["level"] == "WARN" and item["where"] == where
                                for item in report.items), where)

    def test_huge_integer_unknown_type_with_fallback_never_crashes_audit(self):
        huge = 10 ** 10_000
        value, audit = normalize([
            {"type": huge, "fallback": "保留这个位置"},
            {"type": "text", "text": "tail survives"},
        ])
        self.assertEqual("callout", value["blocks"][0]["type"])
        self.assertIn("保留这个位置", value["blocks"][0]["text"])
        self.assertEqual("tail survives", value["blocks"][1]["text"])
        self.assertTrue(any("未知块类型" in item["message"] for item in audit))

    def test_huge_integer_table_key_becomes_exact_decimal_string(self):
        huge = 10 ** 10_000
        decimal = "1" + "0" * 10_000
        value, audit = normalize([{
            "type": "table",
            "columns": [{"label": "huge key", "key": huge}],
            "rows": [["kept"]],
        }])
        table = value["blocks"][0]
        self.assertEqual(decimal, table["columns"][0]["key"])
        self.assertEqual([["kept"]], table["rows"])
        self.assertTrue(any("超大整数列 key" in item["message"] for item in audit))

    def test_cross_field_mismatches_are_repaired_not_error_cards(self):
        value, audit = normalize([
            {"type": "columns", "blocks": [
                [{"type": "text", "text": "A"}],
                [{"type": "text", "text": "B"}],
            ], "ratio": [2]},
            {"type": "progress", "items": [{"label": "p", "value": 150, "max": 100}]},
            {"type": "chart", "kind": "bar", "categories": ["A", "B", "C"],
             "series": [{"data": [7]}]},
            {"type": "chart", "kind": "pie",
             "series": [{"data": [1]}, {"data": [2]}]},
            {"type": "chart", "kind": "gauge",
             "series": [{"data": [10, 20]}, {"data": [30]}]},
        ])
        blocks = value["blocks"]
        self.assertEqual([2, 1], blocks[0]["ratio"])
        self.assertEqual(100, blocks[1]["items"][0]["value"])
        self.assertEqual([7, 0, 0], blocks[2]["series"][0]["data"])
        self.assertEqual([1, 2], blocks[3]["series"][0]["data"])
        self.assertEqual([10], blocks[4]["series"][0]["data"])
        self.assertFalse(any(block["type"].startswith("__") for block in blocks))
        for fragment in ("ratio 少于栏数", "progress value", "数据少于 categories",
                         "合并为一个系列", "稳定采用第一个系列", "稳定采用第一个值"):
            self.assertTrue(any(fragment in item["message"] for item in audit), fragment)

    def test_barcode_formats_and_payloads_are_best_effort(self):
        value, audit = normalize([
            {"type": "barcode", "format": "totally-new", "text": "中文 A"},
            {"type": "barcode", "format": "ean-13", "text": "abc 123"},
            {"type": "barcode", "format": 39, "text": ["a", "b"]},
        ])
        first, second, third = value["blocks"]
        self.assertEqual(("CODE128", "?? A"), (first["format"], first["text"]))
        self.assertEqual("EAN13", second["format"])
        self.assertEqual(13, len(second["text"]))
        self.assertTrue(second["text"].isdigit())
        self.assertEqual("CODE39", third["format"])
        self.assertFalse(any(block["type"].startswith("__") for block in value["blocks"]))
        self.assertGreaterEqual(sum(item["level"] == "WARN" for item in audit), 5)

    def test_unknown_fields_empty_blocks_and_resource_limits_remain_visible(self):
        blocks = [{}]
        blocks.extend({"type": "divider"} for _ in range(validate.MAX_BLOCKS + 5))
        report = Report()
        value, error = validate.normalize_spec({
            "version": 1,
            "futureTop": "x",
            "doc": {"futureDoc": "x"},
            "blocks": blocks,
        }, report)
        self.assertIsNone(error)
        self.assertEqual("callout", value["blocks"][0]["type"])
        self.assertEqual("callout", value["blocks"][-1]["type"])
        self.assertFalse(any(item["level"] == "SKIP" for item in report.items))
        unknown = [item for item in report.items if "未知" in item["message"]]
        self.assertTrue(unknown)
        self.assertTrue(all(item["level"] == "WARN" for item in unknown))

    def test_resource_collections_are_stably_truncated(self):
        value, audit = normalize([
            {"type": "tags", "items": [str(i) for i in range(2_010)]},
            {"type": "table", "columns": [f"c{i}" for i in range(60)], "rows": [[1] * 60]},
            {"type": "calendar", "events": [
                {"date": "2026-01-01", "title": str(i)} for i in range(validate.MAX_EVENTS + 1)
            ]},
        ])
        self.assertEqual(2_000, len(value["blocks"][0]["items"]))
        self.assertEqual(validate.MAX_TABLE_COLUMNS, len(value["blocks"][1]["columns"]))
        self.assertEqual(validate.MAX_EVENTS, len(value["blocks"][2]["events"]))
        self.assertFalse(any(block["type"].startswith("__") for block in value["blocks"]))
        self.assertGreaterEqual(sum("截断" in item["message"] for item in audit), 3)


if __name__ == "__main__":
    unittest.main()
