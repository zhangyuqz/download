#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prevent catalogue bridge injection from corrupting vendor script literals."""
from __future__ import annotations

import sys
from pathlib import Path


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


if len(sys.argv) != 2:
    raise SystemExit("usage: pagespec_041_post_patch_fix_v5.py PLUGIN_ROOT")
root = Path(sys.argv[1]).resolve()
render_path = root / "tools/render_page.py"
text = render_path.read_text(encoding="utf-8")
text = once(
    text,
    '        html = html.replace("</body>", final_bridge + "\\n</body>", 1)\n',
    '        if "</body>" not in html:\n'
    '            raise ValueError("catalog child is missing closing body tag")\n'
    '        body_prefix, body_suffix = html.rsplit("</body>", 1)\n'
    '        html = body_prefix + final_bridge + "\\n</body>" + body_suffix\n',
    "final body injection",
)
render_path.write_text(text, encoding="utf-8")
compile(text, str(render_path), "exec")

regression_path = root / "tests/test_pagespec_041_regressions.py"
regression = regression_path.read_text(encoding="utf-8")
needle = "        self.assertIn('pagespec-catalog-boot',html);self.assertIn('pagespec-catalog-progress',html)\n"
replacement = needle + (
    "        button=html.rfind('<button type=\"button\" id=\"ps-catalog-return\"')\n"
    "        closing_body=html.rfind('</body>')\n"
    "        self.assertGreater(button,0);self.assertLess(button,closing_body)\n"
    "        self.assertGreater(html.rfind('</script>',0,button),html.rfind('<script',0,button))\n"
)
regression = once(regression, needle, replacement, "bridge placement regression")
regression_path.write_text(regression, encoding="utf-8")
compile(regression, str(regression_path), "exec")
print("catalog bridge now targets the final closing body tag")
