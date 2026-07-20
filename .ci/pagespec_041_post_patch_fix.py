#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replace the generated catalog script-nonce block with deterministic logic."""
from __future__ import annotations

import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: pagespec_041_post_patch_fix.py PLUGIN_ROOT")
root = Path(sys.argv[1]).resolve()
path = root / "tools/render_page.py"
text = path.read_text(encoding="utf-8")

pattern = re.compile(
    r"        # Frozen fixture bodies contain executable script tags that predate CSP\..*?"
    r"        if missing_nonce:\n"
    r"            raise ValueError\(f\"catalog child has \{len\(missing_nonce\)\} unnonced script tags\"\)\n",
    re.S,
)
replacement = '''        # Frozen fixture bodies contain executable script tags that predate CSP.
        # Rewrite complete opening tags, not script bodies, so minified source is
        # preserved byte-for-byte and regex escaping cannot suppress matching.
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
print("catalog nonce block normalized")
