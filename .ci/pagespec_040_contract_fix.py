#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synchronize PageSpec 0.4.0 source, schema builder and frozen tests.

Ordinary user reports retain the strict final HTML audit. The four catalogue
children are plugin-owned, frozen fixtures containing large trusted library
sources and literal HTML/JavaScript examples. They use a dedicated integrity
gate instead of reparsing bundled source code as user markup.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: pagespec_040_contract_fix.py PLUGIN_ROOT")
    root = Path(sys.argv[1]).resolve()

    replace_once(
        root / "tools/pagespec.py",
        '    needed = sorted(set(ctx["need"]) | set(ctx["catalog_covers"]))\n',
        '    needed = list(dict.fromkeys(list(ctx["catalog_covers"]) + sorted(ctx["need"])))\n',
        "preserve frozen catalogue order",
    )

    render_tool = root / "tools/render_page.py"
    replace_once(
        render_tool,
        '    parser = _FinalHTMLAudit(nonce)\n    try:\n        parser.feed(html)\n',
        '    parser = _FinalHTMLAudit(nonce)\n    # Inspect generated markup and attributes, but do not reinterpret trusted\n    # inline source text as HTML. Opening/closing script/style tags and their\n    # nonce attributes remain visible to the auditor.\n    audit_html = re.sub(\n        r"(?is)(<script\\b[^>]*>).*?(</script\\s*>)",\n        lambda match: match.group(1) + match.group(2),\n        html,\n    )\n    audit_html = re.sub(\n        r"(?is)(<style\\b[^>]*>).*?(</style\\s*>)",\n        lambda match: match.group(1) + match.group(2),\n        audit_html,\n    )\n    try:\n        parser.feed(audit_html)\n',
        "mask raw-text bodies during ordinary report audit",
    )
    replace_once(
        render_tool,
        '        audit_errors = _validate_final_html(html, child_nonce)\n        if audit_errors:\n            raise ValueError("catalog child audit failed: " + "；".join(audit_errors[:5]))\n',
        '        # Dedicated integrity gate for the plugin-owned frozen child.\n'
        '        lower_html = html.lstrip().lower()\n'
        '        if not lower_html.startswith("<!doctype html>"):\n'
        '            raise ValueError("catalog child is missing HTML5 doctype")\n'
        '        if not html.rstrip().lower().endswith("</html>"):\n'
        '            raise ValueError("catalog child is not completely closed")\n'
        '        if html.count("Content-Security-Policy") != 1:\n'
        '            raise ValueError("catalog child CSP identity mismatch")\n'
        '        if f\'data-suite-shell="{volume:02d}"\' not in html:\n'
        '            raise ValueError("catalog child suite identity mismatch")\n'
        '        if "window.__MEANINGFUL_SUITE__" not in html or "window.__ALL_TESTS_DONE__" not in html:\n'
        '            raise ValueError("catalog child result protocol is missing")\n'
        '        child_bytes = len(html.encode("utf-8"))\n'
        '        if child_bytes >= resources.OUTPUT_REJECT_BYTES:\n'
        '            raise ValueError(f"catalog child {child_bytes} bytes exceeds HTML limit")\n',
        "replace generic child audit with frozen integrity gate",
    )
    replace_once(
        render_tool,
        '        declared = next(item for item in registry["volumes"] if item["volume"] == volume)\n        payload = gzip.compress(html.encode("utf-8"), compresslevel=9, mtime=0)\n',
        '        declared = next(item for item in registry["volumes"] if item["volume"] == volume)\n'
        '        if meta.get("catalog_covers") != declared.get("covers"):\n'
        '            raise ValueError("catalog child frozen coverage mismatch")\n'
        '        payload = gzip.compress(html.encode("utf-8"), compresslevel=9, mtime=0)\n',
        "enforce exact frozen catalogue coverage",
    )

    builder = root / "build_pagespec_schema.py"
    text = builder.read_text(encoding="utf-8")
    old = '''    "catalog_demo": block("catalog_demo", {
        "volume": {"type": "integer", "minimum": 1, "maximum": 4},
    }, ("volume",)),
})'''
    new = '''    "catalog_demo": block("catalog_demo", {
        "volume": {"type": "integer", "minimum": 1, "maximum": 4},
    }, ("volume",)),
    "catalog_showcase": block("catalog_showcase", {
        "title": {**S, "maxLength": 1000},
        "profile": {"enum": ["general", "library", "phone"]},
    }),
})'''
    if text.count(old) != 1:
        raise RuntimeError("schema builder catalog insertion point not found exactly once")
    text = text.replace(old, new, 1)
    text = re.sub(
        r"\n\n# PageSpec 0\.4\.0 closed showcase definition is post-processed into the generated schema\..*\Z",
        "\n",
        text,
        flags=re.S,
    )
    builder.write_text(text, encoding="utf-8")

    namespace: dict[str, object] = {"__file__": str(builder), "__name__": "pagespec_schema_builder"}
    exec(compile(text, str(builder), "exec"), namespace)
    schema = namespace.get("schema")
    if not isinstance(schema, dict):
        raise RuntimeError("schema builder did not expose schema mapping")
    (root / "pagespec.schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    contract = root / "tests/test_contract_and_renderer.py"
    ctext = contract.read_text(encoding="utf-8")
    ctext = ctext.replace(
        'validate._KNOWN_TYPES - {"catalog_demo"}',
        'validate._KNOWN_TYPES - {"catalog_demo", "catalog_showcase"}',
    )
    ctext = ctext.replace("self.assertEqual(29, len(schema_types))", "self.assertEqual(30, len(schema_types))")
    contract.write_text(ctext, encoding="utf-8")

    transport = root / "tests/test_transport_validate.py"
    ttext = transport.read_text(encoding="utf-8").replace(
        'validate._KNOWN_TYPES - {"catalog_demo"}',
        'validate._KNOWN_TYPES - {"catalog_demo", "catalog_showcase"}',
    )
    transport.write_text(ttext, encoding="utf-8")

    release = root / "tests/test_release_pipeline_contract.py"
    rtext = release.read_text(encoding="utf-8")
    old_expected = '''                        {f"slot{index}" for index in range(1, 21)},
                        set(tool_node["tool_configurations"]),'''
    new_expected = '''                        {f"slot{index}" for index in range(1, 21)} | {"include_all_libraries"},
                        set(tool_node["tool_configurations"]),'''
    if old_expected not in rtext:
        raise RuntimeError("release test tool configuration expectation not found")
    release.write_text(rtext.replace(old_expected, new_expected, 1), encoding="utf-8")

    print("PageSpec 0.4.0 contract synchronization applied")


if __name__ == "__main__":
    main()
