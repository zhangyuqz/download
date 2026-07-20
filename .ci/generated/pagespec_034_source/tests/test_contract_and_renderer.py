# -*- coding: utf-8 -*-
"""Release contract tests for PageSpec v1.

These tests deliberately cross the boundaries between the generated JSON
Schema, the tolerant normaliser, and the HTML renderer.  A green unit test in
only one of those layers is not sufficient evidence that the public contract
is coherent.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from html.parser import HTMLParser


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import pagespec
import pagespec_validate as validate


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_pagespec_schema", ROOT / "build_pagespec_schema.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SCHEMA = json.loads((ROOT / "pagespec.schema.json").read_text(encoding="utf-8"))


def schema_results(values):
    """Validate values with the bundled, version-locked Ajv build."""
    ajv_path = ROOT / "tests" / "assets" / "ajv.js"
    program = r"""
const fs = require('fs');
const Ajv = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ajv = new Ajv({allErrors:true, schemaId:'auto'});
const validate = ajv.compile(input.schema);
const results = input.values.map(function(value) {
  const valid = validate(value);
  return {valid:!!valid, errors:valid ? [] : JSON.parse(JSON.stringify(validate.errors || []))};
});
process.stdout.write(JSON.stringify(results));
"""
    proc = subprocess.run(
        ["node", "-e", program, str(ajv_path)],
        input=json.dumps({"schema": SCHEMA, "values": values}, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    if proc.returncode:
        raise AssertionError(f"Ajv failed ({proc.returncode}): {proc.stderr}")
    return json.loads(proc.stdout)


class _ScriptAudit(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.load_attrs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "script":
            self.scripts.append(attrs)
        for name in ("src", "srcset", "href", "poster", "action", "formaction"):
            value = attrs.get(name)
            if value and not value.lower().startswith(("data:", "blob:", "#")):
                self.load_attrs.append((tag, name, value))


def render(spec, *, pre_warnings=()):
    """Render without vendor bytes; contract tests inspect generated source."""
    nonce = "contract-test-nonce"
    html, report, meta = pagespec.render_document(
        spec,
        {
            "slots": {},
            "placeholder": lambda name: "data:image/svg+xml;base64,PHN2Zy8+",
            "load_libs": lambda need: ("", "", []),
            "nonce": nonce,
            "pre_warnings": list(pre_warnings),
        },
    )
    return html, report, meta, nonce


def minimum_blocks():
    """One semantic minimum for each of the 28 user-content block types."""
    return [
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
        {"type": "barcode", "text": "ABC", "format": "CODE128"},
        {"type": "section", "title": "s", "blocks": [{"type": "divider"}]},
        {"type": "card", "blocks": [{"type": "divider"}]},
        {"type": "columns", "blocks": [[{"type": "divider"}]]},
        {"type": "tabs", "items": [{"label": "t", "blocks": [{"type": "divider"}]}]},
        {"type": "collapse", "items": [{"label": "c", "blocks": [{"type": "divider"}]}]},
    ]


class SchemaContractTests(unittest.TestCase):
    def test_checked_in_schema_is_exact_builder_output(self):
        builder = _load_builder()
        self.assertEqual(builder.schema, SCHEMA)

    def test_schema_runtime_and_renderer_have_same_29_type_registry(self):
        schema_types = set(SCHEMA["definitions"]) - {"block"}
        renderer_types = {name for name in pagespec.RENDERERS if not name.startswith("__")}
        self.assertEqual(29, len(schema_types))
        self.assertEqual(schema_types, validate._KNOWN_TYPES)
        self.assertEqual(schema_types, renderer_types)
        for name in sorted(schema_types):
            with self.subTest(block=name):
                schema_fields = set(SCHEMA["definitions"][name]["properties"])
                self.assertEqual(schema_fields, validate._ALLOWED_FIELDS[name])

    def test_all_28_minimum_blocks_are_strict_schema_valid(self):
        values = [{"version": 1, "blocks": [block]} for block in minimum_blocks()]
        results = schema_results(values)
        failures = [
            (values[i]["blocks"][0]["type"], result["errors"])
            for i, result in enumerate(results) if not result["valid"]
        ]
        self.assertEqual([], failures)

    def test_fixed_catalog_profile_is_strict_and_cannot_mix_content(self):
        canonical = {"version": 1, "profile": "catalog-verification",
                     "blocks": [{"type": "catalog_demo", "volume": 1}]}
        mixed = {"version": 1, "profile": "catalog-verification",
                 "blocks": [{"type": "catalog_demo", "volume": 1},
                            {"type": "text", "text": "not allowed"}]}
        no_profile = {"version": 1,
                      "blocks": [{"type": "catalog_demo", "volume": 1}]}
        ordinary_with_profile = {"version": 1, "profile": "catalog-verification",
                                 "blocks": [{"type": "text", "text": "x"}]}
        results = schema_results([canonical, mixed, no_profile, ordinary_with_profile])
        self.assertEqual([True, False, False, False], [item["valid"] for item in results])
        report = pagespec.Report()
        value, error = validate.normalize_spec(canonical, report)
        self.assertIsNone(error)
        self.assertEqual(canonical, value)
        self.assertEqual([], report.items)

    def test_strict_canonical_examples_need_no_runtime_normalisation(self):
        # Passing the strict producer schema must mean exactly what its
        # documentation promises: the runtime does not rewrite that input.
        report = pagespec.Report()
        value, error = validate.normalize_spec(
            {"version": 1, "blocks": minimum_blocks()}, report
        )
        self.assertIsNone(error)
        self.assertEqual([], report.items)
        self.assertEqual(
            {block["type"] for block in minimum_blocks()},
            {block["type"] for block in value["blocks"]},
        )

    def test_strict_schema_rejects_closed_contract_violations(self):
        invalid = [
            {},
            {"version": 1, "blocks": []},
            {"version": True, "blocks": [{"type": "divider"}]},
            {"version": 1, "blocks": [{"type": "text"}]},
            {"version": 1, "blocks": [{"type": "text", "text": "   "}]},
            {"version": 1, "blocks": [{"type": "text", "text": "x", "mystery": 1}]},
            {"version": 1, "blocks": [{"type": "html", "html": "<b>x</b>"}]},
            {"version": 1, "blocks": [{"type": "image", "slot": 21}]},
            {"version": 1, "blocks": [{"type": "chart", "kind": "sankey",
                                           "nodes": [{"name": "a"}], "links": []}]},
        ]
        results = schema_results(invalid)
        self.assertTrue(all(not result["valid"] for result in results), results)

    def test_strict_schema_does_not_accept_a_width_that_runtime_changes(self):
        # The strict schema is advertised as the zero-normalisation contract.
        value = {"version": 1, "blocks": [{"type": "image", "slot": 1, "width": "48"}]}
        result = schema_results([value])[0]
        self.assertFalse(result["valid"], result)

    def test_normalized_sankey_still_satisfies_canonical_schema(self):
        report = pagespec.Report()
        value, error = validate.normalize_spec({
            "version": 1,
            "blocks": [{
                "type": "chart", "kind": "sankey",
                "nodes": [{"name": "a"}, {"name": "b"}],
                "links": [{"source": "a", "target": "b", "value": 1}],
            }],
        }, report)
        self.assertIsNone(error)
        result = schema_results([value])[0]
        self.assertTrue(result["valid"], result)


class RendererContractTests(unittest.TestCase):
    def test_script_close_payload_is_data_in_all_script_and_report_sinks(self):
        payload = '</script><script id="PS_BREAKOUT">globalThis.PS_PWN=1</script><script>'
        spec = {
            "version": 1,
            "doc": {
                "title": payload,
                "header": {"title": payload, "subtitle": payload, "badges": [payload]},
                "footer": payload,
            },
            "blocks": [
                {"type": "markdown", "text": payload},
                {"type": "formula", "latex": payload},
                {"type": "chart", "kind": "bar", "title": payload,
                 "categories": [payload], "series": [{"name": payload, "data": [1]}]},
                {"type": "wordcloud", "items": [{"text": payload, "weight": 1}]},
                {"type": "graph", "nodes": [{"id": payload, "label": payload}, {"id": "B"}],
                 "edges": [{"from": payload, "to": "B", "label": payload}]},
                {"type": "calendar", "events": [{"date": "2026-07-18", "title": payload}]},
                {"type": "qrcode", "text": payload},
                {"type": "barcode", "text": payload, "format": "CODE128"},
            ],
        }
        html, report, meta, nonce = render(
            json.dumps(spec, ensure_ascii=False),
            pre_warnings=[{"level": "WARN", "where": payload,
                           "message": payload, "suggestion": payload}],
        )
        self.assertIsNone(meta["fatal"])
        self.assertNotIn('<script id="PS_BREAKOUT">', html)
        self.assertNotIn("globalThis.PS_PWN=1</script>", html)
        self.assertIn(r"\u003c/script\u003e", html)
        self.assertEqual(3, html.lower().count("</script>"))
        audit = _ScriptAudit()
        audit.feed(html)
        executable = [s for s in audit.scripts if s.get("type") not in {"application/json", "application/ld+json"}]
        self.assertEqual(1, len(executable))
        self.assertEqual(nonce, executable[0].get("nonce"))
        self.assertTrue(any(item["level"] == "WARN" for item in report.items))

    def test_raw_markdown_html_is_only_literal_text_and_has_no_loadable_tag(self):
        raw = ('<style id="EVIL_STYLE">body{display:none}</style>'
               '<img id="EVIL_IMG" src="https://evil.invalid/a.png" onerror="PS_PWN=1">'
               '<script id="EVIL_SCRIPT">PS_PWN=2</script>')
        html, report, meta, _ = render({
            "version": 1,
            "blocks": [{"type": "markdown", "text": raw}],
        })
        self.assertIsNone(meta["fatal"])
        self.assertNotIn('<style id="EVIL_STYLE">', html)
        self.assertNotIn('<img id="EVIL_IMG"', html)
        self.assertNotIn('<script id="EVIL_SCRIPT">', html)
        self.assertIn(r"\u003cstyle id=\"EVIL_STYLE\"\u003e", html)
        self.assertIn("ALLOWED_ATTR:[]", html)
        audit = _ScriptAudit()
        audit.feed(html)
        self.assertEqual([], audit.load_attrs)
        self.assertTrue(any("Markdown" in item["message"] for item in report.items))

    def test_unicode_surrogate_and_controls_never_break_utf8_output(self):
        html, report, meta, _ = render({
            "version": 1,
            "blocks": [
                {"type": "text", "text": "A\ud800B\x00C\U0001fffeD"},
                {"type": "markdown", "text": "M\udfffN"},
            ],
        })
        encoded = html.encode("utf-8")
        self.assertTrue(encoded)
        self.assertNotIn("\ud800", html)
        self.assertNotIn("\udfff", html)
        self.assertNotIn("\x00", html)
        self.assertGreaterEqual(html.count("�"), 4)
        self.assertIsNone(meta["fatal"])
        self.assertGreaterEqual(sum(x["level"] == "WARN" for x in report.items), 2)

    def test_short_table_row_is_padded_and_following_content_survives(self):
        html, report, meta, _ = render({
            "version": 1,
            "blocks": [
                {"type": "table", "columns": ["a", "b"], "rows": [[1]]},
                {"type": "text", "text": "AFTER_TABLE"},
            ],
        })
        self.assertIsNone(meta["fatal"])
        self.assertNotIn('<div class="ps-errcard">', html)
        self.assertIn("ps-tb", html)
        self.assertIn("AFTER_TABLE", html)
        self.assertTrue(any(item["level"] == "WARN" and "行尾补" in item["message"]
                            for item in report.items))

    def test_unknown_block_fallback_is_visible_and_following_content_survives(self):
        html, report, meta, _ = render({
            "version": 1,
            "blocks": [
                {"type": "future_widget", "fallback": "FALLBACK_VISIBLE"},
                {"type": "text", "text": "AFTER_FALLBACK"},
            ],
        })
        self.assertIsNone(meta["fatal"])
        self.assertIn("ps-callout", html)
        self.assertIn("FALLBACK_VISIBLE", html)
        self.assertIn("AFTER_FALLBACK", html)
        self.assertTrue(any(item["level"] == "WARN" for item in report.items))
        self.assertFalse(any(item["level"] == "SKIP" for item in report.items))

    def test_all_28_user_blocks_reach_renderer_without_static_error_card(self):
        spec = {"version": 1, "blocks": minimum_blocks()}
        html, report, meta, _ = render(spec)
        self.assertIsNone(meta["fatal"])
        self.assertNotIn('<div class="ps-errcard">', html)
        self.assertEqual([], [x for x in report.items if x["level"] == "SKIP"])
        self.assertEqual(validate._KNOWN_TYPES - {"catalog_demo"},
                         {block["type"] for block in minimum_blocks()})
        self.assertIn("hljs.highlightElement", html)
        self.assertIn("securityLevel:\"strict\"", html)


if __name__ == "__main__":
    unittest.main()
