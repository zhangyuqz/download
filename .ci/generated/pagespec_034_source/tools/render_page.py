# -*- coding: utf-8 -*-
"""Dify Tool entry point for compiling PageSpec JSON into offline HTML.

The Tool owns orchestration and Dify messages only.  PageSpec transport,
rendering, and resource handling live in PageSpec-specific sibling modules; no
arbitrary-HTML Tool class or helper crosses this task boundary.
"""
from __future__ import annotations

import re
import secrets
import sys
from collections.abc import Generator
from html import escape as html_escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

# Dify loads a Tool from its configured file path rather than as a Python
# package.  Add only this sibling directory so the PageSpec modules resolve in
# both Dify and direct source tests.
TOOLS_DIR = str(Path(__file__).resolve().parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import pagespec
import pagespec_resources as resources


def _csp_meta(nonce: str) -> str:
    policy = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}' 'unsafe-eval'; "
        "style-src 'unsafe-inline' data:; "
        "img-src data: blob:; font-src data: blob:; media-src data: blob:; "
        "connect-src 'none'; worker-src 'none'; child-src 'none'; "
        "frame-src 'none'; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; manifest-src data: blob:"
    )
    return '<meta http-equiv="Content-Security-Policy" content="' + html_escape(policy, quote=True) + '">'


class _FinalHTMLAudit(HTMLParser):
    """Small independent gate over the compiler output, not over user HTML."""

    NON_EXECUTABLE_SCRIPT_TYPES = {"application/json", "application/ld+json"}
    FORBIDDEN_TAGS = {"base", "iframe", "object", "embed", "link"}
    LOAD_ATTRS = {"src", "srcset", "poster", "background", "action", "formaction", "xlink:href"}

    def __init__(self, nonce: str):
        super().__init__(convert_charrefs=False)
        self.nonce = nonce
        self.errors: list[str] = []
        self.scripts = 0
        self.csp = 0
        self.in_head = False
        self.first_head_child = None
        self.first_head_is_csp = False
        self.start_counts = {"html": 0, "body": 0}
        self.end_counts = {"html": 0, "body": 0}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        amap = {str(k).lower(): (v or "") for k, v in attrs}
        if tag in self.start_counts:
            self.start_counts[tag] += 1
        if tag == "head":
            self.in_head = True
            return
        if tag == "body":
            self.in_head = False
        if self.in_head and self.first_head_child is None:
            self.first_head_child = tag
            self.first_head_is_csp = (
                tag == "meta" and amap.get("http-equiv", "").lower() == "content-security-policy"
            )
        if tag in self.FORBIDDEN_TAGS:
            self.errors.append(f"出现禁止标签 <{tag}>")
        for name, value in amap.items():
            lower = value.strip().lower()
            if name.startswith("on"):
                self.errors.append(f"出现事件属性 {name}")
            if name in self.LOAD_ATTRS and lower and not lower.startswith(("data:", "blob:", "#")):
                self.errors.append(f"{tag}.{name} 不是内联资源")
            if name == "href" and lower and not lower.startswith("#"):
                self.errors.append(f"{tag}.href 不是页内锚点")
        if tag == "meta" and amap.get("http-equiv", "").lower() == "content-security-policy":
            self.csp += 1
        if tag == "script":
            script_type = amap.get("type", "").split(";", 1)[0].strip().lower()
            if script_type not in self.NON_EXECUTABLE_SCRIPT_TYPES:
                self.scripts += 1
                if amap.get("nonce") != self.nonce:
                    self.errors.append("存在没有正确 nonce 的可执行脚本")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "head":
            self.in_head = False
        if tag in self.end_counts:
            self.end_counts[tag] += 1


def _validate_final_html(html: str, nonce: str) -> list[str]:
    errors: list[str] = []
    if not html.lstrip().lower().startswith("<!doctype html>"):
        errors.append("缺少 HTML5 doctype")
    parser = _FinalHTMLAudit(nonce)
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        errors.append(f"HTML 终审解析失败：{exc}")
    errors.extend(parser.errors)
    if any(parser.start_counts[tag] != 1 or parser.end_counts[tag] != 1 for tag in ("html", "body")):
        errors.append("HTML 根节点或 body 未正确闭合")
    if parser.csp != 1:
        errors.append(f"CSP 数量应为 1，实际 {parser.csp}")
    if not parser.first_head_is_csp:
        errors.append("CSP 不是 head 的第一个元素")
    return list(dict.fromkeys(errors))


def _referenced_slot_names(raw_spec: Any) -> set[str]:
    """Best-effort pre-scan so unrelated uploads cannot consume page budgets.

    The authoritative parser/normalizer still runs inside ``render_document``.
    This pre-scan only narrows file I/O to numeric values under the canonical
    or documented Chinese image-slot keys; a missed value safely degrades to
    the renderer's ordinary missing-slot placeholder.
    """
    try:
        value, hard_error, _events = pagespec.parse_spec(raw_spec)
    except Exception:
        return set()
    if hard_error:
        return set()

    found: set[str] = set()
    slot_keys = {"slot", "slots", "插槽", "插槽们"}

    def add_candidate(candidate: Any) -> None:
        if isinstance(candidate, bool):
            return
        if isinstance(candidate, (list, tuple, set)):
            for item in candidate:
                add_candidate(item)
            return
        try:
            number = float(candidate)
        except (TypeError, ValueError, OverflowError):
            return
        if number.is_integer() and 1 <= number <= 20:
            found.add(f"slot{int(number)}")

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if str(key) in slot_keys:
                    add_candidate(child)
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(value)
    return found


class RenderPageTool(Tool):
    """Orchestrate one PageSpec render without replacing ``Tool.__init__``."""

    def _collect_slots(
        self,
        params: dict,
        needed_slots: set[str] | None = None,
    ) -> tuple[dict[str, str], list[str]]:
        """Map Dify file parameters to PageSpec data URIs and visible warnings."""
        return resources.collect_slots(params, needed_slots=needed_slots)

    # ---- library loader: version-locked + SHA-verified --------------------
    def _make_lib_loader(self, nonce: str):
        """Build a loader for only the libraries requested by normal blocks."""
        def load(need: set[str]):
            data = resources.load_vendor_map()
            libs = {
                k: v
                for k, v in (data.get("libs") or data).items()
                if not k.startswith("_")
            }
            css_out: list[str] = []
            js_out: list[str] = []
            missing: list[str] = []
            done: set[str] = set()

            def emit(key: str):
                if key in done or key not in libs:
                    if key not in libs:
                        missing.append(key)
                    return
                done.add(key)
                spec = libs[key]
                for dep in spec.get("deps", []):
                    emit(dep)
                for css in spec.get("css", []):
                    try:
                        css_src = resources.read_verified_vendor(spec, css)
                        css_src = resources.sanitize_css(css_src)
                        css_out.append(f'<style data-lib="{key}">{css_src}</style>')
                    except Exception:
                        missing.append(css)
                f = spec.get("file")
                if f:
                    try:
                        js = resources.read_verified_vendor(spec, f).replace("</script", "<\\/script")
                        js_out.append(
                            f'<script nonce="{html_escape(nonce, quote=True)}" data-lib="{html_escape(key, quote=True)}">{js}</script>'
                        )
                    except Exception:
                        missing.append(f)

            for k in sorted(need):
                emit(k)
            return "\n".join(css_out), "\n".join(js_out), missing

        return load

    def _make_catalog_loader(self, nonce: str):
        """Load a trusted ordered fixture while preserving captured globals.

        The old all-library suites intentionally load colliding globals in a
        defined order (for example lodash/underscore and Moment-Timezone/
        Moment).  After each exact asset is loaded we capture its declared
        global under a stable catalogue key.  No user-provided expression is
        evaluated.
        """
        def handle_block(key: str, spec: dict) -> str:
            path = str(spec.get("global") or "").split(".")
            path = [part for part in path if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", part)]
            if not path:
                return ""
            k = html_escape(key, quote=True)
            return (
                f'<script nonce="{html_escape(nonce, quote=True)}" data-lib-handle="{k}">'
                f'(function(){{var v=window,p={pagespec.js_json(path)};for(var i=0;i<p.length;i++)'
                f'{{v=v&&v[p[i]];}}window.__LIB_HANDLES__[{pagespec.js_json(key)}]=v;}})();</script>'
            )

        def load(assets: list[dict]):
            data = resources.load_vendor_map()
            libs = data["libs"]
            css_out: list[str] = []
            js_out: list[str] = []
            missing: list[str] = []
            dep_done: set[str] = set()
            css_done: set[str] = set()
            explicit_css = {
                css
                for item in assets if item.get("kind") == "css"
                for css in (libs.get(item.get("key"), {}).get("css") or [])
            }

            def emit_css(key: str, *, companion: bool):
                spec = libs.get(key)
                if not spec:
                    missing.append(key)
                    return
                for css in spec.get("css") or []:
                    if css in css_done or (companion and css in explicit_css):
                        continue
                    try:
                        source = resources.sanitize_css(
                            resources.read_verified_vendor(spec, css)
                        )
                        css_out.append(f'<style data-lib="{html_escape(key, quote=True)}">{source}</style>')
                        css_done.add(css)
                    except Exception as exc:
                        missing.append(f"{css}: {type(exc).__name__}")

            def emit_dependency(key: str):
                if key in dep_done:
                    return
                spec = libs.get(key)
                if not spec:
                    missing.append(key)
                    return
                for dep in spec.get("deps") or []:
                    emit_dependency(dep)
                emit_css(key, companion=True)
                filename = spec.get("file")
                if filename and str(filename).endswith(".js"):
                    try:
                        source = resources.read_verified_vendor(spec, filename).replace(
                            "</script", "<\\/script"
                        )
                        js_out.append(
                            f'<script nonce="{html_escape(nonce, quote=True)}" '
                            f'data-lib="{html_escape(key, quote=True)}">{source}</script>'
                        )
                        block = handle_block(key, spec)
                        if block:
                            js_out.append(block)
                    except Exception as exc:
                        missing.append(f"{filename}: {type(exc).__name__}")
                dep_done.add(key)

            for item in assets:
                key, kind = str(item.get("key") or ""), item.get("kind")
                if key not in libs:
                    missing.append(key or "(empty catalog key)")
                    continue
                if kind == "css":
                    emit_css(key, companion=False)
                    continue
                if kind != "js":
                    missing.append(f"{key}: unknown asset kind {kind!r}")
                    continue
                spec = libs[key]
                for dep in spec.get("deps") or []:
                    emit_dependency(dep)
                emit_css(key, companion=True)
                filename = spec.get("file")
                try:
                    source = resources.read_verified_vendor(spec, filename).replace(
                        "</script", "<\\/script"
                    )
                    js_out.append(
                        f'<script nonce="{html_escape(nonce, quote=True)}" '
                        f'data-lib="{html_escape(key, quote=True)}">{source}</script>'
                    )
                    block = handle_block(key, spec)
                    if block:
                        js_out.append(block)
                    dep_done.add(key)
                except Exception as exc:
                    missing.append(f"{filename}: {type(exc).__name__}")
            return "\n".join(css_out), "\n".join(js_out), missing

        return load

    # ---- main -------------------------------------------------------------
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        spec_raw = tool_parameters.get("spec")
        requested_filename = pagespec._safe_text(tool_parameters.get("filename") or "").strip()
        nonce = secrets.token_urlsafe(18)

        needed_slots = _referenced_slot_names(spec_raw)
        slots, slot_errors = self._collect_slots(
            tool_parameters,
            needed_slots=needed_slots,
        )

        ctx_inputs = {
            "slots": slots,
            "placeholder": resources.slot_placeholder,
            "load_libs": self._make_lib_loader(nonce),
            "load_catalog_assets": self._make_catalog_loader(nonce),
            "nonce": nonce,
            "pre_warnings": [
                {"level": "WARN", "where": "image-slot", "message": message,
                 "suggestion": "重新上传可识别图片；页面已使用带标签占位图"}
                for message in slot_errors
            ],
        }
        try:
            html, report, meta = pagespec.render_document(spec_raw, ctx_inputs)
        except Exception as exc:
            # A compiler bug must still produce a valid explanation file, never
            # a truncated/broken HTML document or a silent tool crash.
            report = pagespec.Report()
            report.add("SKIP", "compiler", f"内部渲染事务已回滚：{type(exc).__name__}",
                       "请保留输入并报告此错误；未交付部分生成的页面")
            reason = f"内部渲染事务失败（{type(exc).__name__}）；未输出半成品"
            html = pagespec._hard_error_page(reason)
            meta = {"fatal": reason, "needed_libs": [], "missing": []}

        doc_filename = pagespec._safe_text(meta.get("doc_filename") or "").strip()
        filename = requested_filename or doc_filename or "page.html"
        if requested_filename in ("page.html", "page") and doc_filename:
            filename = doc_filename
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename).strip(" .") or "page.html"
        if not filename.lower().endswith(".html"):
            filename += ".html"
        if len(filename) > 240:
            filename = filename[:235].rstrip(" .") + ".html"

        # Install the unique nonce CSP as the first head element.
        if "<head>" in html:
            html = html.replace("<head>", "<head>\n" + _csp_meta(nonce), 1)

        audit_errors = _validate_final_html(html, nonce)
        if audit_errors:
            joined = "；".join(audit_errors[:8])
            report.add("SKIP", "output-audit", f"最终 HTML 终审失败：{joined}",
                       "生成事务已回滚，只交付说明页")
            reason = f"最终 HTML 终审未通过：{joined}。内容页未交付，避免产生坏文件。"
            html = pagespec._hard_error_page(reason)
            html = html.replace("<head>", "<head>\n" + _csp_meta(nonce), 1)
            meta["fatal"] = reason
            filename = re.sub(r"\.html$", "_未生成说明.html", filename, flags=re.I)
            second_errors = _validate_final_html(html, nonce)
            if second_errors:
                yield self.create_text_message("安全说明页自身终审失败，已停止文件输出：" + "；".join(second_errors))
                return

        blob = html.encode("utf-8")
        size = len(blob)
        if meta.get("fatal") is None and size > resources.OUTPUT_REJECT_BYTES:
            report.add("SKIP", "output-size",
                       f"候选页面 {size/resources.MIB:.2f} MiB 超过 "
                       f"{resources.OUTPUT_REJECT_BYTES//resources.MIB} MiB 拒绝线",
                       "减少块、图片或大型图表数据")
            reason = (
                f"候选页面体积 {size/resources.MIB:.2f} MiB 超过 "
                f"{resources.OUTPUT_REJECT_BYTES//resources.MIB} MiB 拒绝线；内容页未交付。"
            )
            html = pagespec._hard_error_page(reason)
            html = html.replace("<head>", "<head>\n" + _csp_meta(nonce), 1)
            meta["fatal"] = reason
            filename = re.sub(r"\.html$", "_未生成说明.html", filename, flags=re.I)
            blob = html.encode("utf-8")
            size = len(blob)

        # ---- summary ----
        c = report.counts
        lines = [
            f"已生成离线 HTML：{filename}",
            f"内容块渲染完成 · 内联库：{len(meta.get('needed_libs') or [])} 个"
            f"（{'、'.join(meta.get('needed_libs') or []) or '无'}）· 图片插槽：{len(slots)} 个",
            f"最终 UTF-8 体积：{size/resources.MIB:.2f} MiB"
            + (
                f"（已超 {resources.OUTPUT_WARN_BYTES//resources.MIB} MiB 警告线）"
                if size > resources.OUTPUT_WARN_BYTES else ""
            ),
            f"容错三态：归一 {c['INFO']} · 警告 {c['WARN']} · 降级 {c['SKIP']}",
        ]
        if meta.get("fatal"):
            lines.append(f"⚠ 输入被拒绝（生成了说明页）：{meta['fatal']}")
        if slot_errors:
            lines.append("图片插槽记录：\n  • " + "\n  • ".join(slot_errors))
        if report.items:
            shown = report.items[:20]
            lines.append("容错记录：\n  • " + "\n  • ".join(
                f"[{it['level']}] {it['where']}：{it['message']}"
                + (f" → {it['suggestion']}" if it["suggestion"] else "")
                for it in shown))
            if len(report.items) > 20:
                lines.append(f"  （另有 {len(report.items)-20} 条，详见文件内报告面板）")

        yield self.create_text_message("\n".join(lines))
        yield self.create_blob_message(
            blob=blob, meta={"mime_type": "text/html", "filename": filename}
        )
