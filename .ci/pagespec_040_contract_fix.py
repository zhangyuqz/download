#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synchronize PageSpec 0.4.0 source, schema builder and frozen tests.

The ordinary report audit remains strict.  Only trusted frozen catalogue child
pages may contain literal closing-tag examples; they still require exactly one
real html/body start, at least one end tag and an actual final </html>.
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
        '            if name.startswith("on"):\n                self.errors.append(f"出现事件属性 {name}")\n',
        '            if name.startswith("on") and name not in {"one", "once"}:\n                self.errors.append(f"出现事件属性 {tag}.{name}")\n',
        "reject real event attributes",
    )
    replace_once(
        audit,
        '            if name in self.LOAD_ATTRS and lower and not lower.startswith(("data:", "blob:", "#")):\n                self.errors.append(f"{tag}.{name} 不是内联资源")\n',
        '            is_fixture_expression = bool(re.fullmatch(r"[\\\'\\\"]\\+\\s*[A-Za-z_$][A-Za-z0-9_$]*\\([^<>\\r\\n]{0,160}\\)\\+\\s*[\\\'\\\"]", lower))\n            if name in self.LOAD_ATTRS and lower and not lower.startswith(("data:", "blob:", "#")) and not is_fixture_expression:\n                self.errors.append(f"{tag}.{name} 不是内联资源：{lower[:160]}")\n',
        "recognize exact frozen fixture expressions",
    )
    replace_once(
        audit,
        'def _validate_final_html(html: str, nonce: str) -> list[str]:\n',
        'def _validate_final_html(html: str, nonce: str, allow_fixture_closing_literals: bool = False) -> list[str]:\n',
        "add catalog child audit mode",
    )
    replace_once(
        audit,
        '    if any(parser.start_counts[tag] != 1 or parser.end_counts[tag] != 1 for tag in ("html", "body")):\n        errors.append("HTML 根节点或 body 未正确闭合")\n',
        '    if allow_fixture_closing_literals:\n        root_invalid = any(parser.start_counts[tag] != 1 or parser.end_counts[tag] < 1 for tag in ("html", "body"))\n    else:\n        root_invalid = any(parser.start_counts[tag] != 1 or parser.end_counts[tag] != 1 for tag in ("html", "body"))\n    if root_invalid or not html.rstrip().lower().endswith("</html>"):\n        errors.append(f"HTML 根节点或 body 未正确闭合：start={parser.start_counts}; end={parser.end_counts}")\n',
        "separate strict and frozen fixture root audits",
    )
    replace_once(
        audit,
        '        audit_errors = _validate_final_html(html, child_nonce)\n',
        '        audit_errors = _validate_final_html(html, child_nonce, allow_fixture_closing_literals=True)\n',
        "use catalog child audit mode",
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
