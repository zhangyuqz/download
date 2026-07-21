#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def extract_package(package: Path, destination: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"package CRC failure: {bad}")
        archive.extractall(destination)


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
    tool_entity = types.ModuleType("dify_plugin.entities.tool")
    tool_entity.ToolInvokeMessage = dict
    sys.modules["dify_plugin"] = dify
    sys.modules["dify_plugin.entities"] = entities
    sys.modules["dify_plugin.entities.tool"] = tool_entity
    sys.path.insert(0, str(package_root / "tools"))
    import render_page

    spec = {
        "version": 1,
        "doc": {"title": "PageSpec 0.4.1 syntax diagnostic", "theme": "dark"},
        "blocks": [{"type": "text", "text": "syntax diagnostic"}],
    }
    messages = list(render_page.RenderPageTool()._invoke({
        "spec": json.dumps(spec, ensure_ascii=False, separators=(",", ":")),
        "filename": "pagespec_041_syntax_diagnostic.html",
        "include_all_libraries": True,
    }))
    if [item.get("kind") for item in messages] != ["text", "blob"]:
        raise RuntimeError(messages)
    output.write_bytes(messages[1]["blob"])
    return {"bytes": len(messages[1]["blob"]), "message": messages[0]["text"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report: dict = {
        "generated": None,
        "status": None,
        "failed_to_parse": [],
        "runtime_exceptions": [],
        "console": [],
        "page_errors": [],
        "frame_diagnostics": [],
        "error": None,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="pagespec041-syntax-package-") as temporary:
            root = Path(temporary)
            extract_package(args.package.resolve(), root)
            report["generated"] = generate_html(root, args.html.resolve())

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1366, "height": 900})
            context.add_init_script("""
            (() => {
              globalThis.__PAGESPEC_DIAGNOSTIC_EVENTS__ = [];
              addEventListener('error', event => {
                globalThis.__PAGESPEC_DIAGNOSTIC_EVENTS__.push({
                  kind: 'error', message: String(event.message || ''),
                  filename: String(event.filename || ''), line: event.lineno || 0,
                  column: event.colno || 0,
                  stack: event.error && event.error.stack ? String(event.error.stack) : ''
                });
              }, true);
              addEventListener('unhandledrejection', event => {
                const reason = event.reason;
                globalThis.__PAGESPEC_DIAGNOSTIC_EVENTS__.push({
                  kind: 'unhandledrejection', message: String(reason && reason.message || reason || ''),
                  stack: reason && reason.stack ? String(reason.stack) : ''
                });
              });
            })();
            """)
            page = context.new_page()
            page.on("console", lambda message: report["console"].append({
                "type": message.type,
                "text": message.text,
                "location": message.location,
            }))
            page.on("pageerror", lambda error: report["page_errors"].append({
                "message": str(error),
                "stack": getattr(error, "stack", None),
            }))

            cdp = context.new_cdp_session(page)
            cdp.send("Runtime.enable")
            cdp.send("Debugger.enable")
            cdp.on("Debugger.scriptFailedToParse", lambda event: report["failed_to_parse"].append(dict(event)))
            cdp.on("Runtime.exceptionThrown", lambda event: report["runtime_exceptions"].append(dict(event)))

            page.goto(args.html.resolve().as_uri(), wait_until="load", timeout=180_000)
            page.wait_for_timeout(1000)
            page.locator(".ps-catalog-card").first.click()
            page.wait_for_selector(".ps-catalog-shell iframe", state="attached", timeout=180_000)
            status = page.locator(".ps-catalog-card").first.locator("em")
            handle = status.element_handle()
            try:
                page.wait_for_function(
                    "el => ['ready','fail'].includes(el.getAttribute('data-kind'))",
                    arg=handle,
                    timeout=150_000,
                )
            except Exception:
                pass
            report["status"] = {
                "kind": status.get_attribute("data-kind"),
                "text": status.inner_text(),
            }

            for frame in page.frames:
                try:
                    frame_info = {
                        "url": frame.url,
                        "title": frame.title(),
                        "events": frame.evaluate("globalThis.__PAGESPEC_DIAGNOSTIC_EVENTS__ || []"),
                        "scripts": frame.evaluate("""
                        Array.from(document.scripts).map((script, index) => {
                          const type = (script.type || '').split(';', 1)[0].trim().toLowerCase();
                          let parseError = '';
                          if (!['application/json','application/ld+json'].includes(type)) {
                            try { new Function(script.textContent || ''); }
                            catch (error) { parseError = String(error && error.stack || error); }
                          }
                          const text = script.textContent || '';
                          return {
                            index: index + 1, type, nonce: script.getAttribute('nonce') || '',
                            bytes: new TextEncoder().encode(text).length,
                            parseError,
                            prefix: text.slice(0, 240), suffix: text.slice(-240)
                          };
                        })
                        """),
                    }
                    report["frame_diagnostics"].append(frame_info)
                except Exception as exc:
                    report["frame_diagnostics"].append({"url": frame.url, "read_error": str(exc)})

            for event in report["failed_to_parse"]:
                script_id = event.get("scriptId")
                if not script_id:
                    continue
                try:
                    source = cdp.send("Debugger.getScriptSource", {"scriptId": script_id}).get("scriptSource", "")
                    line = int(event.get("startLine", event.get("lineNumber", 0)) or 0)
                    lines = source.splitlines()
                    event["source_bytes"] = len(source.encode("utf-8"))
                    event["source_excerpt"] = "\n".join(lines[max(0, line - 3): min(len(lines), line + 4)])
                except Exception as exc:
                    event["source_read_error"] = str(exc)

            context.close()
            browser.close()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"

    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
