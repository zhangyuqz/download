#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Align the frozen offline gate with PageSpec's internal isolated blob transport."""
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
    raise SystemExit("usage: pagespec_041_post_patch_fix_v6.py PLUGIN_ROOT")
root = Path(sys.argv[1]).resolve()
render_path = root / "tools/render_page.py"
text = render_path.read_text(encoding="utf-8")

anchor = '''        if meta.get("fatal"):
            raise ValueError(f"catalog volume {volume} failed: {meta['fatal']}")

        nonce_attr = html_escape(child_nonce, quote=True)
'''
replacement = '''        if meta.get("fatal"):
            raise ValueError(f"catalog volume {volume} failed: {meta['fatal']}")

        # The catalogue child is loaded from an in-memory blob URL by design.
        # Treat file: and this internal blob: transport as offline, while the
        # existing external-resource and policy-block lists remain mandatory.
        offline_pattern = re.compile(
            r"offlinePass\\s*=\\s*protocol\\s*===\\s*(['\\\"])file:\\1\\s*&&"
        )
        html, offline_count = offline_pattern.subn(
            "offlinePass=(protocol==='file:'||protocol==='blob:')&&",
            html,
            count=1,
        )
        if offline_count != 1:
            raise ValueError(
                f"catalog volume {volume} offline gate identity mismatch: {offline_count}"
            )

        nonce_attr = html_escape(child_nonce, quote=True)
'''
text = once(text, anchor, replacement, "catalog offline gate patch")
render_path.write_text(text, encoding="utf-8")
compile(text, str(render_path), "exec")

regression_path = root / "tests/test_pagespec_041_regressions.py"
regression = regression_path.read_text(encoding="utf-8")
needle = '''    def test_parent_has_native_and_pako_decompression_paths(self):
        source=(TOOLS/'pagespec.py').read_text(encoding='utf-8')
        self.assertIn("DecompressionStream",source);self.assertIn("pako.ungzip",source)
        self.assertIn("运行超过 60 秒没有进展",source);self.assertIn("运行超过 300 秒",source)
'''
new_method = '''    def test_catalog_offline_gate_accepts_only_file_or_blob(self):
        tool=render_page.RenderPageTool()
        for volume in (1,2,3,4):
            payload=tool._build_catalog_volume(volume,'test-catalog-nonce')
            html=gzip.decompress(base64.b64decode(payload['gzip_b64'])).decode('utf-8')
            self.assertEqual(1,html.count("offlinePass=(protocol==='file:'||protocol==='blob:')&&"),volume)
            self.assertNotIn("offlinePass=protocol==='file:'&&",html)
            self.assertIn("external",html);self.assertIn("blocked",html)

    def test_parent_has_native_and_pako_decompression_paths(self):
        source=(TOOLS/'pagespec.py').read_text(encoding='utf-8')
        self.assertIn("DecompressionStream",source);self.assertIn("pako.ungzip",source)
        self.assertIn("运行超过 60 秒没有进展",source);self.assertIn("运行超过 300 秒",source)
'''
regression = once(regression, needle, new_method, "offline gate regression")
regression_path.write_text(regression, encoding="utf-8")
compile(regression, str(regression_path), "exec")
print("catalog offline gate now accepts file and isolated blob protocols")
