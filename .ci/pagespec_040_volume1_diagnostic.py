#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnose the released PageSpec 0.4.0 volume-01 timeout without changing it."""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def load_browser_audit(path: Path):
    spec = importlib.util.spec_from_file_location("pagespec_browser_audit_base", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def snapshot(page, elapsed: float) -> dict:
    status = page.locator('.ps-catalog-card').nth(0).locator('em')
    frame_locator = page.locator('.ps-catalog-shell iframe')
    frame = None
    if frame_locator.count():
        element = frame_locator.element_handle()
        frame = element.content_frame() if element else None
    result = {
        "elapsed_seconds": round(elapsed, 1),
        "parent_status_kind": status.get_attribute('data-kind') if status.count() else None,
        "parent_status_text": status.inner_text() if status.count() else None,
        "iframe_count": frame_locator.count(),
    }
    if frame:
        try:
            result.update(frame.evaluate("""() => {
              const suite=window.__MEANINGFUL_SUITE__||null;
              const rows=suite&&Array.isArray(suite.rows)?suite.rows:[];
              const unfinished=rows.filter(r => !(r && (r.pass===true || r.pass===false))).slice(0,20).map(r => r && (r.key||r.library||r.name||JSON.stringify(r).slice(0,120)));
              const failed=rows.filter(r => r && r.pass===false).slice(0,10).map(r => ({key:r.key||r.library||r.name||'', error:r.error||r.message||r.actual||''}));
              return {
                child_title:document.title,
                child_ready_state:document.readyState,
                all_tests_done:window.__ALL_TESTS_DONE__===true,
                suite_total:suite&&suite.total,
                suite_done:suite&&suite.done,
                suite_passed:suite&&suite.passed,
                suite_failed:suite&&suite.failed,
                final_gate:suite&&suite.final_gate_pass,
                presentation_gate:suite&&suite.presentation_gate_pass,
                runtime_gate:suite&&suite.runtime_gate_pass,
                rows_length:rows.length,
                result_table_rows:document.querySelectorAll('#results tr').length,
                unfinished:unfinished,
                failed_rows:failed,
                runtime_errors:window.__RUNTIME_GATE__||window.__ps_runtime_errors||null,
                body_height:Math.max(document.documentElement.scrollHeight,document.body?document.body.scrollHeight:0),
                active_element:document.activeElement&&document.activeElement.tagName,
              };
            }"""))
        except Exception as exc:
            result["frame_evaluate_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--html',type=Path,required=True)
    args=parser.parse_args()
    base=load_browser_audit(Path(__file__).with_name('pagespec_040_browser_audit.py'))
    report={"snapshots":[],"console":[],"page_errors":[],"network":[],"error":None}
    try:
        with tempfile.TemporaryDirectory(prefix='pagespec-volume1-diag-') as temporary:
            root=Path(temporary)
            base.extract_package(args.package.resolve(),root)
            report['generated']=base.generate_html(root,args.html.resolve())
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            report['browser_version']=browser.version
            context=browser.new_context(viewport={"width":1366,"height":900})
            page=context.new_page()
            page.on('console',lambda msg: report['console'].append({"type":msg.type,"text":msg.text}) if msg.type in {'error','warning'} else None)
            page.on('pageerror',lambda error: report['page_errors'].append(str(error)))
            page.on('request',lambda request: report['network'].append(request.url) if request.url.startswith(('http://','https://')) else None)
            page.goto(args.html.resolve().as_uri(),wait_until='load',timeout=180_000)
            page.locator('.ps-catalog-card').nth(0).click()
            start=time.monotonic()
            next_mark=0
            while time.monotonic()-start <= 200:
                elapsed=time.monotonic()-start
                if elapsed >= next_mark:
                    report['snapshots'].append(snapshot(page,elapsed))
                    next_mark += 10
                kind=page.locator('.ps-catalog-card').nth(0).locator('em').get_attribute('data-kind')
                if kind=='ready':
                    report['terminal']='ready'
                    break
                if kind=='fail' and elapsed>=125:
                    report['terminal']='fail'
                    report['snapshots'].append(snapshot(page,elapsed))
                    break
                page.wait_for_timeout(500)
            else:
                report['terminal']='diagnostic-timeout'
            context.close();browser.close()
    except Exception as exc:
        report['error']=f"{type(exc).__name__}: {exc}"
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(args.output.read_text(encoding='utf-8'))
    raise SystemExit(0 if report.get('terminal')=='ready' else 1)


if __name__=='__main__':
    main()
