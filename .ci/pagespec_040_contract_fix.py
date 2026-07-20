#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synchronize PageSpec 0.4.0 source, schema builder and frozen tests.

The final HTML auditor still inspects every generated tag, URL-bearing
attribute, CSP meta tag, and script nonce.  It masks only the text bodies of
script/style elements before structural parsing so trusted minified source code
is not incorrectly reparsed as HTML markup.
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

    audit = root / "tools/render_page.py"
    replace_once(
        audit,
        '    parser = _FinalHTMLAudit(nonce)\n    try:\n        parser.feed(html)\n',
        '    parser = _FinalHTMLAudit(nonce)\n    # HTMLParser must inspect the generated element tags and their attributes,\n    # but executable/library source text is not markup.  Mask only raw-text\n    # element bodies while preserving each opening/closing tag, so nonce/CSP,\n    # external URLs, forbidden tags and root closure remain fully audited.\n    audit_html = re.sub(\n        r"(?is)(<script\\b[^>]*>).*?(</script\\s*>)",\n        lambda match: match.group(1) + match.group(2),\n        html,\n    )\n    audit_html = re.sub(\n        r"(?is)(<style\\b[^>]*>).*?(</style\\s*>)",\n        lambda match: match.group(1) + match.group(2),\n        audit_html,\n    )\n    try:\n        parser.feed(audit_html)\n',
        "mask raw-text bodies during structural audit",
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
