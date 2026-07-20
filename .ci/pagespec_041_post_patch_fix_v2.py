#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind catalog children to the inherited CSP nonce without rewriting JS source."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


if len(sys.argv) != 2:
    raise SystemExit("usage: pagespec_041_post_patch_fix_v2.py PLUGIN_ROOT")
root = Path(sys.argv[1]).resolve()
render_path = root / "tools/render_page.py"
text = render_path.read_text(encoding="utf-8")

text = once(
    text,
    "    def _build_catalog_volume(self, volume: int) -> dict[str, Any]:\n",
    "    def _build_catalog_volume(self, volume: int, inherited_nonce: str) -> dict[str, Any]:\n",
    "builder signature",
)
text = once(text, "        child_nonce = secrets.token_urlsafe(18)\n", "        child_nonce = inherited_nonce\n", "child nonce")
text = once(
    text,
    '            "build_catalog_volume": self._build_catalog_volume,\n',
    '            "build_catalog_volume": lambda volume: self._build_catalog_volume(volume, nonce),\n',
    "parent binding",
)

block = re.compile(
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
text, count = block.subn(lambda _match: replacement, text, count=1)
if count != 1:
    raise RuntimeError(f"catalog nonce block: expected one match, found {count}")
render_path.write_text(text, encoding="utf-8")
compile(text, str(render_path), "exec")

regression_path = root / "tests/test_pagespec_041_regressions.py"
regression = regression_path.read_text(encoding="utf-8")
regression = once(
    regression,
    "from pathlib import Path\n",
    "from pathlib import Path\nfrom html.parser import HTMLParser\n",
    "HTMLParser import",
)
test_pattern = re.compile(
    r"    def test_catalog_child_scripts_are_all_nonced\(self\):\n.*?(?=    def test_parent_has_native_and_pako_decompression_paths\(self\):)",
    re.S,
)
test_replacement = '''    def test_catalog_child_scripts_are_all_nonced(self):
        payload=render_page.RenderPageTool()._build_catalog_volume(1, 'test-catalog-nonce')
        html=gzip.decompress(base64.b64decode(payload['gzip_b64'])).decode('utf-8')
        class Audit(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False);self.nonces=[]
            def handle_starttag(self,tag,attrs):
                if str(tag).lower()=='script':
                    self.nonces.append(dict(attrs).get('nonce'))
        audit=Audit();audit.feed(html);audit.close()
        self.assertGreater(len(audit.nonces),10)
        self.assertTrue(all(value=='test-catalog-nonce' for value in audit.nonces),audit.nonces[:10])
        self.assertIn('pagespec-catalog-boot',html);self.assertIn('pagespec-catalog-progress',html)

'''
regression, count = test_pattern.subn(lambda _match: test_replacement, regression, count=1)
if count != 1:
    raise RuntimeError(f"regression method: expected one match, found {count}")
regression_path.write_text(regression, encoding="utf-8")
compile(regression, str(regression_path), "exec")
print("catalog inherited nonce binding and structural regression replacement applied")
