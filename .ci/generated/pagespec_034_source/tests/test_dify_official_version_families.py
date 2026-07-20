# -*- coding: utf-8 -*-
"""Frozen official Dify Template-node implementation-family coverage.

The 112 labels and their three implementation families are inherited from the
DOCX 0.0.19 corpus, whose rows freeze the official tag commit, source path and
source SHA-256.  Testing every label with the same bytes would be cosmetic;
the gate instead tests each distinct transport implementation and asserts that
every researched label maps to one of those tested implementations.  No claim
is made for future/private/modified builds.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from pagespec_transport import parse_spec


FAMILIES = {
    "template_triple_quoted_inputs_raw": """
        0.6.0 0.6.0-fix1 0.6.0-preview-workflow.1 0.6.0-preview-workflow.2
        0.6.1 0.6.2 0.6.3 0.6.4
    """.split(),
    "template_triple_quoted_inputs_base64": """
        0.6.5 0.6.6 0.6.7 0.6.8 0.6.9 0.6.10 0.6.11 0.6.12 0.6.12-fix1
        0.6.13 0.6.14 0.6.15 0.6.16 0.7.0 0.7.1 0.7.2 0.7.3 0.8.0
        0.8.0-beta1 0.8.1 0.8.2 0.8.3 0.9.0 0.9.1 0.9.1-fix1 0.9.2
        0.10.0 0.10.0-beta1 0.10.0-beta2 0.10.0-beta3 0.10.1 0.10.2
        0.10.2-fix1 0.11.0 0.11.1 0.11.2 0.12.0 0.12.1 0.13.0 0.13.1
        0.13.2 0.14.0 0.14.1 0.14.2 0.15.0 0.15.1 0.15.2 0.15.3 0.15.4
        0.15.5 0.15.6 0.15.6-alpha.1 0.15.7 0.15.8 1.0.0 1.0.0-beta.1
        1.0.1 1.1.0 1.1.1 1.1.2 1.1.3 1.2.0 1.3.0 1.3.1 1.4.0 1.4.1
        1.4.2 1.4.3 1.5.0 1.5.1 1.6.0 1.7.0 1.7.1 1.7.2 1.8.0 1.8.1
        1.9.0 1.9.1 1.9.2 1.10.0 1.10.0-rc1 1.10.1 1.10.1-fix.1 1.11.0
        1.11.1 1.11.2 2.0.0-beta.1 2.0.0-beta.2 v0.8.3-fix1
    """.split(),
    "template_and_inputs_base64": """
        1.11.3 1.11.4 1.12.0 1.12.1 1.13.0 1.13.1 1.13.2 1.13.3 1.14.0
        1.14.0-rc1 1.14.1 1.14.2 1.15.0 1.16.0 1.16.0-rc1
    """.split(),
}


def _legacy_duplicate_outer_quote(canonical: str) -> str:
    # Exact family behavior that produced the Dify 1.7.1 screenshot form.
    source = json.dumps(json.dumps(canonical, ensure_ascii=False), ensure_ascii=False)
    namespace = {}
    exec(compile("def value():\n return '''" + source + "'''\n", "<dify-template>", "exec"), namespace)
    return namespace["value"]()


class DifyOfficialVersionFamilyTests(unittest.TestCase):
    def test_frozen_112_labels_form_three_disjoint_families(self):
        labels = [label for values in FAMILIES.values() for label in values]
        self.assertEqual(112, len(labels))
        self.assertEqual(112, len(set(labels)))
        self.assertEqual([8, 89, 15], [len(values) for values in FAMILIES.values()])
        self.assertIn("1.7.1", FAMILIES["template_triple_quoted_inputs_base64"])
        self.assertIn("1.14.2", FAMILIES["template_and_inputs_base64"])

    def test_each_distinct_family_reaches_the_same_business_pagespec(self):
        spec = {
            "version": 1,
            "doc": {"title": "Dify版本族业务报告"},
            "blocks": [{"type": "text", "text": "第一行\n第二行“引号”"}],
        }
        canonical = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
        values = {
            "template_triple_quoted_inputs_raw": _legacy_duplicate_outer_quote(canonical),
            "template_triple_quoted_inputs_base64": json.dumps({"output": canonical}, ensure_ascii=False),
            "template_and_inputs_base64": {"output": canonical},
        }
        for family, value in values.items():
            with self.subTest(family=family):
                outcome = parse_spec(value)
                self.assertEqual("Dify版本族业务报告", outcome.value["doc"]["title"])
                self.assertEqual("第一行\n第二行“引号”", outcome.value["blocks"][0]["text"])


if __name__ == "__main__":
    unittest.main()
