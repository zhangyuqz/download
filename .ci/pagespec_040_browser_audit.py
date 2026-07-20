#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a final 172-library report from the released package and audit it in Chromium."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
import types
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

EXPECTED_COUNTS = [35, 63, 41, 33]


def extract_package(package: Path, target: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"package CRC failure: {bad}")
        archive.extractall(target)


def generate_html(package_root: Path, output: Path) -> dict:
    class StubTool:
        def __init__(self, *args, **kwargs):
            pass

        def create_text_message(self, text):
            return {"kind": "text", "text": text}

        def create_blob_message(self, blob, meta):
            return {"kind": "blob", "blob": blob, "meta": meta}

    dify = types.ModuleType("dify_plugin")
    dify.Tool = StubTool
    entities = types.ModuleType("dify_plugin.entities")
    entity_tool = types.ModuleType("dify_plugin.entities.tool")
    entity_tool.ToolInvokeMessage = dict
    sys.modules["dify_plugin"] = dify
    sys.modules["dify_plugin.entities"] = entities
    sys.modules["dify_plugin.entities.tool"] = entity_tool
    tools = package_root / "tools"
    sys.path.insert(0, str(tools))
    import render_page

    spec = {
        "version": 1,
        "doc": {
            "title": "PageSpec 0.4.0 成品浏览器复核",
            "theme": "dark",
            "toc": True,
            "header": {
                "title": "PageSpec 0.4.0 成品浏览器复核",
                "subtitle": "普通报告正文、宽表和四卷 172 库位于同一离线 HTML。",
                "badges": ["离线", "172 库", "桌面/移动端"],
            },
        },
        "blocks": [
            {"type": "heading", "text": "业务正文", "level": 1},
            {"type": "text", "text": "本段用于确认全库展示不会覆盖或破坏普通报告正文。"},
            {
                "type": "table",
                "columns": ["A", "B", "C", "D", "E", "F", "G", "H"],
                "rows": [[1, 2, 3, 4, 5, 6, 7, 8], [9, 10, 11, 12, 13, 14, 15, 16]],
                "features": ["search", "sort"],
            },
        ],
    }
    messages = list(render_page.RenderPageTool()._invoke({
        "spec": json.dumps(spec, ensure_ascii=False, separators=(",", ":")),
        "filename": "PageSpec_0.4.0_浏览器审计.html",
        "include_all_libraries": True,
    }))
    if [item.get("kind") for item in messages] != ["text", "blob"]:
        raise RuntimeError(f"unexpected tool messages: {messages!r}")
    blob = messages[1]["blob"]
    output.write_bytes(blob)
    return {
        "filename": messages[1]["meta"]["filename"],
        "bytes": len(blob),
        "text_message": messages[0]["text"],
    }


def root_overflow(page_or_frame) -> int:
    return page_or_frame.evaluate(
        "Math.max(0, document.documentElement.scrollWidth-document.documentElement.clientWidth, "
        "document.body ? document.body.scrollWidth-document.documentElement.clientWidth : 0)"
    )


def audit_viewport(browser, html_path: Path, viewport: dict, name: str) -> dict:
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    network = []
    console_errors = []
    page_errors = []
    page.on("request", lambda request: network.append(request.url) if re.match(r"^https?://", request.url, re.I) else None)
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(html_path.as_uri(), wait_until="load", timeout=180_000)
    page.wait_for_timeout(1000)

    result = {
        "viewport": name,
        "cards": page.locator(".ps-catalog-card").count(),
        "index_count": page.locator(".ps-catalog-index span").count(),
        "parent_root_overflow": root_overflow(page),
        "volumes": [],
        "network": network,
        "console_errors": console_errors,
        "page_errors": page_errors,
    }
    if "本段用于确认" not in page.locator("body").inner_text():
        raise AssertionError(f"{name}: ordinary report body missing")
    if result["cards"] != 4:
        raise AssertionError(f"{name}: expected 4 cards, got {result['cards']}")
    if result["index_count"] != 172:
        raise AssertionError(f"{name}: expected 172 index items, got {result['index_count']}")
    if result["parent_root_overflow"] > 1:
        raise AssertionError(f"{name}: parent root overflow {result['parent_root_overflow']}")

    for index, expected_count in enumerate(EXPECTED_COUNTS):
        card = page.locator(".ps-catalog-card").nth(index)
        status = card.locator("em")
        card.click()
        page.wait_for_selector(".ps-catalog-shell iframe", state="attached", timeout=180_000)
        status_handle = status.element_handle()
        page.wait_for_function(
            "el => ['ready','fail'].includes(el.getAttribute('data-kind'))",
            arg=status_handle,
            timeout=180_000,
        )
        kind = status.get_attribute("data-kind")
        status_text = status.inner_text()
        frame_element = page.locator(".ps-catalog-shell iframe").element_handle()
        frame = frame_element.content_frame() if frame_element else None
        if frame is None:
            raise AssertionError(f"{name} volume {index+1}: iframe content frame missing")
        suite = frame.evaluate("window.__MEANINGFUL_SUITE__ || null")
        child_overflow = root_overflow(frame)
        volume_result = {
            "volume": index + 1,
            "expected": expected_count,
            "status_kind": kind,
            "status_text": status_text,
            "suite": suite,
            "root_overflow": child_overflow,
        }
        result["volumes"].append(volume_result)
        if kind != "ready":
            raise AssertionError(f"{name} volume {index+1}: {kind} {status_text}")
        if not isinstance(suite, dict):
            raise AssertionError(f"{name} volume {index+1}: suite missing")
        if suite.get("total") != expected_count or suite.get("passed") != expected_count or suite.get("failed") != 0:
            raise AssertionError(f"{name} volume {index+1}: suite counts {suite}")
        if suite.get("final_gate_pass") is not True:
            raise AssertionError(f"{name} volume {index+1}: final gate is not true")
        if child_overflow > 1:
            raise AssertionError(f"{name} volume {index+1}: child root overflow {child_overflow}")
        page.wait_for_timeout(11_000)
        late_suite = frame.evaluate("window.__MEANINGFUL_SUITE__ || null")
        if late_suite.get("final_gate_pass") is not True:
            raise AssertionError(f"{name} volume {index+1}: late stability failed")
        frame.locator("#ps-catalog-return").click()
        page.wait_for_selector(".ps-catalog-shell iframe", state="detached", timeout=30_000)
        if page.locator(".ps-catalog-overlay").count() != 0:
            raise AssertionError(f"{name} volume {index+1}: overlay remained after close")
        if root_overflow(page) > 1:
            raise AssertionError(f"{name} volume {index+1}: parent overflow after close")

    result["network"] = network
    result["console_errors"] = console_errors
    result["page_errors"] = page_errors
    if network:
        raise AssertionError(f"{name}: external network requests: {network[:10]}")
    if console_errors:
        raise AssertionError(f"{name}: console errors: {console_errors[:10]}")
    if page_errors:
        raise AssertionError(f"{name}: page errors: {page_errors[:10]}")
    context.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    args = parser.parse_args()
    report = {"generated": None, "viewports": [], "passed": False, "error": None}
    try:
        with tempfile.TemporaryDirectory(prefix="pagespec-browser-package-") as temporary:
            package_root = Path(temporary)
            extract_package(args.package.resolve(), package_root)
            report["generated"] = generate_html(package_root, args.html.resolve())
        if report["generated"]["bytes"] >= 30_000_000:
            raise AssertionError(f"generated HTML exceeds 30,000,000 bytes: {report['generated']['bytes']}")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            report["browser_version"] = browser.version
            report["decompression_stream"] = None
            probe = browser.new_page()
            report["decompression_stream"] = probe.evaluate("typeof DecompressionStream === 'function'")
            probe.close()
            if report["decompression_stream"] is not True:
                raise AssertionError("DecompressionStream is unavailable")
            report["viewports"].append(audit_viewport(browser, args.html.resolve(), {"width": 1366, "height": 900}, "desktop"))
            report["viewports"].append(audit_viewport(browser, args.html.resolve(), {"width": 390, "height": 844}, "mobile-390"))
            browser.close()
        report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.read_text(encoding="utf-8"))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
