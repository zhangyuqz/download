#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize catalog nonce handling after the generated 0.4.1 patch."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


if len(sys.argv) != 2:
    raise SystemExit("usage: pagespec_041_post_patch_fix.py PLUGIN_ROOT")
root = Path(sys.argv[1]).resolve()
path = root / "tools/render_page.py"
text = path.read_text(encoding="utf-8")

# blob: documents inherit a snapshot of the creator document's CSP.  Using a
# second random nonce in the child makes every child script fail the inherited
# parent policy.  Bind child generation to the same per-render nonce instead.
text = replace_once(
    text,
    "    def _build_catalog_volume(self, volume: int) -> dict[str, Any]:\n",
    "    def _build_catalog_volume(self, volume: int, inherited_nonce: str) -> dict[str, Any]:\n",
    "catalog builder inherited nonce signature",
)
text = replace_once(
    text,
    "        child_nonce = secrets.token_urlsafe(18)\n",
    "        child_nonce = inherited_nonce\n",
    "catalog builder inherited nonce value",
)
text = replace_once(
    text,
    '            "build_catalog_volume": self._build_catalog_volume,\n',
    '            "build_catalog_volume": lambda volume: self._build_catalog_volume(volume, nonce),\n',
    "catalog builder parent nonce binding",
)

pattern = re.compile(
    r"        # Frozen fixture bodies contain executable script tags that predate CSP\..*?"
    r"        if missing_nonce:\n"
    r"            raise ValueError\(f\"catalog child has \{len\(missing_nonce\)\} unnonced script tags\"\)\n",
    re.S,
)
replacement = '''        # Frozen fixture bodies contain executable script tags that predate CSP.
        # Rewrite complete opening tags, not script bodies, so minified source is
        # preserved byte-for-byte and every script matches the inherited nonce.
        def add_script_nonce(match):
            tag = match.group(0)
            if re.search(r"\\bnonce\\s*=", tag, flags=re.I):
                return tag
            return tag[:-1] + f' nonce="{nonce_attr}">'

        html = re.sub(r"<script\\b[^>]*>", add_script_nonce, html, flags=re.I)
        script_tags = re.findall(r"<script\\b[^>]*>", html, flags=re.I)
        missing_nonce = [
            tag[:200] for tag in script_tags
            if not re.search(r"\\bnonce\\s*=", tag, flags=re.I)
        ]
        if not script_tags:
            raise ValueError("catalog child has no executable script tags")
        if missing_nonce:
            raise ValueError(f"catalog child has {len(missing_nonce)} unnonced script tags")
'''
text, count = pattern.subn(lambda _match: replacement, text, count=1)
if count != 1:
    raise RuntimeError(f"catalog nonce block: expected one match, found {count}")
path.write_text(text, encoding="utf-8")
compile(text, str(path), "exec")

regression = root / "tests/test_pagespec_041_regressions.py"
regression_text = regression.read_text(encoding="utf-8")
regression_text = replace_once(
    regression_text,
    "payload=render_page.RenderPageTool()._build_catalog_volume(1)\n",
    "payload=render_page.RenderPageTool()._build_catalog_volume(1, 'test-catalog-nonce')\n",
    "regression inherited nonce call",
)
regression.write_text(regression_text, encoding="utf-8")
compile(regression_text, str(regression), "exec")
print("catalog nonce block and inherited CSP binding normalized")
