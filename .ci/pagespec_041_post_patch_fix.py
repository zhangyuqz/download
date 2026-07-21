#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize inherited catalog CSP nonce handling without touching script bodies."""
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

# blob: documents inherit the creator document's CSP.  Bind child generation to
# the same per-render nonce so the inherited parent policy and child meta policy
# both authorize the already-nonced generated script elements.
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

# Do not regex-rewrite the complete HTML: bundled libraries legitimately contain
# literal "<script>" text inside JavaScript strings.  HTMLParser understands raw
# script text and reports only real element start tags, so use it as a read-only
# integrity check and leave all library source bytes untouched.
pattern = re.compile(
    r"        # Frozen fixture bodies contain executable script tags that predate CSP\..*?"
    r"        if missing_nonce:\n"
    r"            raise ValueError\(f\"catalog child has \{len\(missing_nonce\)\} unnonced script tags\"\)\n",
    re.S,
)
replacement = '''        class CatalogScriptNonceAudit(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.script_count = 0
                self.missing = []

            def handle_starttag(self, tag, attrs):
                if str(tag).lower() != "script":
                    return
                self.script_count += 1
                attributes = {str(name).lower(): (value or "") for name, value in attrs}
                if attributes.get("nonce") != child_nonce:
                    self.missing.append(attributes.get("nonce", ""))

        nonce_audit = CatalogScriptNonceAudit()
        nonce_audit.feed(html)
        nonce_audit.close()
        if nonce_audit.script_count == 0:
            raise ValueError("catalog child has no executable script tags")
        if nonce_audit.missing:
            raise ValueError(
                f"catalog child has {len(nonce_audit.missing)} scripts without the inherited nonce"
            )
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
    "from pathlib import Path\n",
    "from pathlib import Path\nfrom html.parser import HTMLParser\n",
    "regression HTMLParser import",
)
regression_text = replace_once(
    regression_text,
    "payload=render_page.RenderPageTool()._build_catalog_volume(1)\n",
    "payload=render_page.RenderPageTool()._build_catalog_volume(1, 'test-catalog-nonce')\n",
    "regression inherited nonce call",
)
old_check = '''        tags=re.findall(r'<script\\b[^>]*>',html,flags=re.I)
        self.assertGreater(len(tags),10)
        self.assertTrue(all(re.search(r'\\bnonce\\s*=',tag,re.I) for tag in tags))
'''
new_check = '''        class Audit(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False);self.nonces=[]
            def handle_starttag(self,tag,attrs):
                if str(tag).lower()=='script':
                    self.nonces.append(dict(attrs).get('nonce'))
        audit=Audit();audit.feed(html);audit.close()
        self.assertGreater(len(audit.nonces),10)
        self.assertTrue(all(value=='test-catalog-nonce' for value in audit.nonces),audit.nonces[:10])
'''
regression_text = replace_once(
    regression_text,
    old_check,
    new_check,
    "regression real script nonce audit",
)
regression.write_text(regression_text, encoding="utf-8")
compile(regression_text, str(regression), "exec")
print("catalog inherited CSP nonce binding verified without source rewriting")
