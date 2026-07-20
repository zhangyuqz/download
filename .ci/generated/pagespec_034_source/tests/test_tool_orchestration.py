# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import base64
import json
import os
import sys
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml


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
entity_tool = types.ModuleType("dify_plugin.entities.tool")
entity_tool.ToolInvokeMessage = dict
sys.modules.setdefault("dify_plugin.entities", entities)
sys.modules.setdefault("dify_plugin.entities.tool", entity_tool)

import render_page
import pagespec_resources


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def construct_unique(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise AssertionError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique)


class ToolOrchestrationTests(unittest.TestCase):
    def invoke(self, spec, filename=""):
        tool = render_page.RenderPageTool()
        messages = list(tool._invoke({"spec": json.dumps(spec, ensure_ascii=False),
                                     "filename": filename}))
        self.assertEqual(["text", "blob"], [m["kind"] for m in messages])
        return messages

    def test_real_tool_path_emits_audited_blob_and_doc_filename(self):
        messages = self.invoke({
            "version": 1,
            "doc": {"filename": "报告.html"},
            "blocks": [{"type": "text", "text": "ok"}],
        }, "page.html")
        text, blob = messages
        html = blob["blob"].decode("utf-8")
        self.assertEqual("报告.html", blob["meta"]["filename"])
        self.assertIn("容错三态：归一 0 · 警告 0 · 降级 0", text["text"])
        self.assertNotIn("data-offline-selfcheck", html)
        head = html.split("<head>", 1)[1].lstrip()
        self.assertTrue(head.startswith('<meta http-equiv="Content-Security-Policy"'))
        self.assertIn("connect-src &#x27;none&#x27;", head[:800])

    def test_breakout_payload_remains_data_through_actual_tool(self):
        payload = '</script><script id="PWN">globalThis.PWN=1</script><script>'
        _, blob = self.invoke({
            "version": 1,
            payload: payload,
            "blocks": [
                {"type": "markdown", "text": payload},
                {"type": "qrcode", "text": payload},
                {"type": "future", "fallback": payload},
            ],
        })
        html = blob["blob"].decode("utf-8")
        self.assertNotIn('<script id="PWN">', html)
        self.assertIn("\\u003c/script", html)
        self.assertIn("ps-callout", html)

    def test_final_auditor_rejects_external_and_unnonced_script(self):
        nonce = "n"
        html = ('<!DOCTYPE html><html><head>' + render_page._csp_meta(nonce) +
                '</head><body><img src="https://example.com/x"><script>1</script></body></html>')
        errors = render_page._validate_final_html(html, nonce)
        self.assertTrue(any("不是内联资源" in x for x in errors))
        self.assertTrue(any("nonce" in x for x in errors))

    def test_report_overflow_keeps_every_path_in_embedded_audit(self):
        report = render_page.pagespec.Report()
        for i in range(report.MAX_ITEMS + 7):
            report.add("INFO", f"/{i}", "item")
        html = render_page.pagespec._report_html(report)
        self.assertEqual(report.MAX_ITEMS + 7, len(report.items))
        self.assertEqual(0, report.omitted)
        self.assertIn("其余 7 条没有丢失", html)
        marker = '<script type="application/json" id="__ofx-report-data">'
        payload = html.split(marker, 1)[1].split("</script>", 1)[0]
        audit = json.loads(payload)
        self.assertEqual(report.MAX_ITEMS + 7, len(audit))
        self.assertEqual(f"/{report.MAX_ITEMS + 6}", audit[-1]["where"])
        self.assertEqual(report.MAX_ITEMS + 7, audit[-1]["id"])

    def test_eight_hundred_blocks_keep_all_unknown_field_warnings(self):
        blocks = []
        for index in range(800):
            block = {"type": "text", "text": f"row-{index}"}
            block.update({f"unknown_{field}": field for field in range(10)})
            blocks.append(block)
        _, blob = self.invoke({"version": 1, "blocks": blocks})
        html = blob["blob"].decode("utf-8")
        marker = '<script type="application/json" id="__ofx-report-data">'
        payload = html.split(marker, 1)[1].split("</script>", 1)[0]
        audit = json.loads(payload)
        self.assertEqual(8_000, len(audit))
        self.assertEqual(0, sum(item["level"] != "WARN" for item in audit))
        self.assertEqual("/blocks/799/unknown_9", audit[-1]["where"])
        self.assertEqual(8_000, audit[-1]["id"])
        self.assertLess(len(blob["blob"]), 2_000_000)

    def test_slot_pipeline_accepts_real_png_and_degrades_bad_file(self):
        class File:
            dify_model_identity = pagespec_resources.DIFY_FILE_IDENTITY
            filename = "pixel.png"
            size = 68
            url = ""

        good = File()
        good._blob = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        bad = File()
        bad.filename = "bad.png"
        bad._blob = b"not an image"
        tool = render_page.RenderPageTool()
        slots, errors = tool._collect_slots({"slot1": good, "slot2": bad})
        self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"))
        self.assertTrue(slots["slot2"].startswith("data:image/svg+xml"))
        self.assertEqual(1, len(errors))
        self.assertIn("占位图", errors[0])

    def test_nine_mainstream_image_assets_keep_byte_detected_mime(self):
        class File:
            dify_model_identity = pagespec_resources.DIFY_FILE_IDENTITY
            url = ""

        archive = ROOT / "tests" / "assets" / "image_slots_mainstream.zip"
        expected = {
            1: "image/png", 2: "image/jpeg", 3: "image/gif", 4: "image/webp",
            5: "image/svg+xml", 6: "image/avif", 7: "image/bmp", 8: "image/x-icon",
            9: "image/webp",
        }
        params = {}
        with zipfile.ZipFile(archive) as zf:
            for i in range(1, 10):
                name = next(name for name in zf.namelist() if name.startswith(f"slot{i}"))
                blob = zf.read(name)
                f = File()
                f.filename = name
                f.size = len(blob)
                f._blob = blob
                params[f"slot{i}"] = f
        slots, errors = render_page.RenderPageTool()._collect_slots(params)
        self.assertEqual([], errors)
        for i, mime in expected.items():
            with self.subTest(slot=i):
                self.assertTrue(slots[f"slot{i}"].startswith(f"data:{mime};base64,"),
                                slots[f"slot{i}"][:80])

    def test_svg_css_resource_attributes_cannot_bypass_offline_boundary(self):
        external = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
            b'<rect width="8" height="8" fill="url(https://example.invalid/p.png)"/>'
            b'</svg>'
        )
        _, error = pagespec_resources.validate_image_blob(external, "image/svg+xml")
        self.assertIn("外部/相对加载资源", error or "")

        safe_fragment = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
            b'<linearGradient id="g"><stop offset="1"/></linearGradient>'
            b'<rect width="8" height="8" fill="url(#g)"/>'
            b'</svg>'
        )
        dimensions, error = pagespec_resources.validate_image_blob(
            safe_fragment, "image/svg+xml"
        )
        self.assertEqual((8, 8), dimensions)
        self.assertIsNone(error)

    def test_plain_text_is_vendor_independent_and_library_failure_rolls_back(self):
        broken = ValueError("vendor map deliberately unavailable")
        with patch.object(pagespec_resources, "load_vendor_map", side_effect=broken):
            text_messages = self.invoke({
                "version": 1,
                "blocks": [{"type": "text", "text": "无需任何前端库"}],
            })
            text_html = text_messages[1]["blob"].decode("utf-8")
            self.assertIn("无需任何前端库", text_html)
            self.assertNotIn("未输出半成品", text_html)

            library_messages = self.invoke({
                "version": 1,
                "blocks": [{"type": "markdown", "text": "**需要库**"}],
            })
            library_html = library_messages[1]["blob"].decode("utf-8")
            self.assertIn("内部渲染事务失败", library_html)
            self.assertIn("输入被拒绝", library_messages[0]["text"])
            self.assertEqual("text/html", library_messages[1]["meta"]["mime_type"])

    def test_unreferenced_upload_cannot_consume_referenced_slot_budget(self):
        class File:
            dify_model_identity = pagespec_resources.DIFY_FILE_IDENTITY
            filename = "pixel.png"
            url = ""

            def __init__(self, blob):
                self._blob = blob
                self.size = len(blob)

        pixel = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        spec = {"version": 1, "blocks": [{"type": "image", "slot": 2}]}
        with patch.object(pagespec_resources, "MAX_TOTAL_SLOT_BYTES", len(pixel)):
            messages = list(render_page.RenderPageTool()._invoke({
                "spec": json.dumps(spec),
                "slot1": File(pixel),
                "slot2": File(pixel),
            }))
        self.assertEqual(["text", "blob"], [message["kind"] for message in messages])
        self.assertIn("图片插槽：1 个", messages[0]["text"])
        self.assertNotIn("累计图片", messages[0]["text"])
        self.assertIn("data:image/png;base64,", messages[1]["blob"].decode("utf-8"))

    def test_manifest_provider_and_tool_yaml_are_unique_and_current(self):
        manifest = yaml.load((ROOT / "manifest.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        provider = yaml.load((ROOT / "provider" / "html_offline_exporter.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        tool = yaml.load((ROOT / "tools" / "render_page.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        # Read the source manifest's current release value instead of freezing a
        # stale predecessor version into an unrelated Tool contract test.
        current_version = str(manifest["version"])
        self.assertRegex(current_version, r"^\d+\.\d+\.\d+$")
        self.assertIn("description", manifest)
        self.assertEqual(
            ["provider/html_offline_exporter.yaml"],
            manifest["plugins"]["tools"],
        )
        self.assertIn("PageSpec", provider["identity"]["description"]["en_US"])
        self.assertEqual(["tools/render_page.yaml"], provider["tools"])
        self.assertEqual(
            {"python": {"source": "tools/render_page.py"}},
            tool["extra"],
        )
        self.assertTrue((ROOT / tool["extra"]["python"]["source"]).is_file())
        names = [item["name"] for item in tool["parameters"]]
        self.assertEqual([f"slot{i}" for i in range(1, 21)],
                         [name for name in names if name.startswith("slot")])
        self.assertTrue(all(item["form"] == "form" for item in tool["parameters"]
                            if item["name"].startswith("slot")))
        llm = tool["description"]["llm"].lower()
        self.assertNotIn('|html"', llm)
        self.assertNotIn('"type":"html"', llm)

    def test_pagespec_tool_is_isolated_and_keeps_sdk_constructor(self):
        """Prevent the old cross-Tool dependency and constructor bug from returning."""
        tool_tree = ast.parse((TOOLS / "render_page.py").read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tool_tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tool_tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertNotIn("export_html", imported_modules)

        tool_subclasses = []
        for node in tool_tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if any(isinstance(base, ast.Name) and base.id == "Tool" for base in node.bases):
                tool_subclasses.append(node)
        self.assertEqual(["RenderPageTool"], [node.name for node in tool_subclasses])
        self.assertNotIn(
            "__init__",
            [item.name for item in tool_subclasses[0].body if isinstance(item, ast.FunctionDef)],
        )

        resource_tree = ast.parse(
            (TOOLS / "pagespec_resources.py").read_text(encoding="utf-8")
        )
        self.assertFalse(any(
            isinstance(node, ast.ImportFrom) and node.module == "dify_plugin"
            for node in ast.walk(resource_tree)
        ))
        self.assertNotIn("ExportHtmlTool", (TOOLS / "pagespec_resources.py").read_text(encoding="utf-8"))
        self.assertFalse((TOOLS / "export_html.py").exists())
        for relative in (
            "verification/catalog_generate.py",
            "verification/generate_browser_fixtures.py",
            "verification/scripts/catalog_generate.py",
            "verification/scripts/generate_browser_fixtures.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("import export_html", source)
            self.assertNotIn("_legacy", source)


if __name__ == "__main__":
    unittest.main()
