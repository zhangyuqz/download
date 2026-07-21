#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sys
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


if len(sys.argv) != 2:
    raise SystemExit("usage: pagespec_041_post_patch_fix_v3.py PLUGIN_ROOT")
root = Path(sys.argv[1]).resolve()
render = root / "tools/render_page.py"
text = render.read_text(encoding="utf-8")
text = one(text, "    def _build_catalog_volume(self, volume: int) -> dict[str, Any]:\n", "    def _build_catalog_volume(self, volume: int, inherited_nonce: str) -> dict[str, Any]:\n", "builder signature")
text = one(text, "        child_nonce = secrets.token_urlsafe(18)\n", "        child_nonce = inherited_nonce\n", "child nonce")
text = one(text, '            "build_catalog_volume": self._build_catalog_volume,\n', '            "build_catalog_volume": lambda volume: self._build_catalog_volume(volume, nonce),\n', "parent binding")

pattern = re.compile(
    r"        # Frozen fixture bodies contain executable script tags that predate CSP\..*?"
    r"        if missing_nonce:\n"
    r"            raise ValueError\(f\"catalog child has \{len\(missing_nonce\)\} unnonced script tags\"\)\n",
    re.S,
)
replacement = '''        class CatalogScriptNonceAudit(HTMLParser):
            NON_EXECUTABLE = {"application/json", "application/ld+json"}
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.script_count = 0
                self.missing = []
            def handle_starttag(self, tag, attrs):
                if str(tag).lower() != "script":
                    return
                attributes = {str(name).lower(): (value or "") for name, value in attrs}
                script_type = attributes.get("type", "").split(";", 1)[0].strip().lower()
                if script_type in self.NON_EXECUTABLE:
                    return
                self.script_count += 1
                if attributes.get("nonce") != child_nonce:
                    self.missing.append(attributes.get("nonce", ""))
        nonce_audit = CatalogScriptNonceAudit()
        nonce_audit.feed(html)
        nonce_audit.close()
        if nonce_audit.script_count == 0:
            raise ValueError("catalog child has no executable script tags")
        if nonce_audit.missing:
            raise ValueError(f"catalog child has {len(nonce_audit.missing)} executable scripts without the inherited nonce")
'''
text, count = pattern.subn(lambda _: replacement, text, count=1)
if count != 1:
    raise RuntimeError(f"catalog nonce block: expected one match, found {count}")
render.write_text(text, encoding="utf-8")
compile(text, str(render), "exec")

regression_path = root / "tests/test_pagespec_041_regressions.py"
regression = regression_path.read_text(encoding="utf-8")
regression = one(regression, "from pathlib import Path\n", "from pathlib import Path\nfrom html.parser import HTMLParser\n", "HTMLParser import")
method = re.compile(
    r"    def test_catalog_child_scripts_are_all_nonced\(self\):\n.*?(?=    def test_parent_has_native_and_pako_decompression_paths\(self\):)",
    re.S,
)
method_body = '''    def test_catalog_child_scripts_are_all_nonced(self):
        payload=render_page.RenderPageTool()._build_catalog_volume(1, 'test-catalog-nonce')
        html=gzip.decompress(base64.b64decode(payload['gzip_b64'])).decode('utf-8')
        class Audit(HTMLParser):
            NON_EXECUTABLE={'application/json','application/ld+json'}
            def __init__(self):
                super().__init__(convert_charrefs=False);self.nonces=[]
            def handle_starttag(self,tag,attrs):
                if str(tag).lower()!='script': return
                attributes=dict(attrs)
                script_type=(attributes.get('type') or '').split(';',1)[0].strip().lower()
                if script_type not in self.NON_EXECUTABLE:
                    self.nonces.append(attributes.get('nonce'))
        audit=Audit();audit.feed(html);audit.close()
        self.assertGreater(len(audit.nonces),10)
        self.assertTrue(all(value=='test-catalog-nonce' for value in audit.nonces),audit.nonces[:10])
        self.assertIn('pagespec-catalog-boot',html);self.assertIn('pagespec-catalog-progress',html)

'''
regression, count = method.subn(lambda _: method_body, regression, count=1)
if count != 1:
    raise RuntimeError(f"regression method: expected one match, found {count}")
regression_path.write_text(regression, encoding="utf-8")
compile(regression, str(regression_path), "exec")
print("catalog executable-script nonce audit applied")
