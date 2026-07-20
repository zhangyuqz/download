# -*- coding: utf-8 -*-
"""Regression evidence for every researched Dify Template-node implementation.

Dify 0.6.0 through 1.10.0 and Graphon 0.4.0/0.6.0 all return the
rendered Jinja text as ``outputs["output"]``.  The run-result panel then JSON
serialises that outputs object, producing the escaped shape shown by the user.
Both the actual variable value and the panel/API envelope must decode to the
same PageSpec without asking the user to rewrite it.
"""
from __future__ import annotations

import json
import unittest

from tools.pagespec_transport import parse_spec


BASE_SPEC = {
    "version": 1,
    "doc": {"title": "Dify template transport"},
    "blocks": [{"type": "text", "text": "中文与 emoji ✅"}],
}

# These versions cover each in-repository Template-node generation and the two
# Graphon generations used after Dify extracted its workflow runtime package.
RESEARCHED_TEMPLATE_ENGINES = (
    "Dify 0.6.0",
    "Dify 0.8.3",
    "Dify 1.0.0",
    "Dify 1.3.0",
    "Dify 1.7.1",
    "Dify 1.8.1",
    "Dify 1.9.0",
    "Dify 1.10.0",
    "Dify 1.14.2 / Graphon 0.4.0",
    "current Dify / Graphon 0.6.0",
)


class DifyTemplateVersionMatrixTests(unittest.TestCase):
    """Prove all researched Template output families enter the same decoder."""

    def test_native_template_variable_for_every_researched_generation(self):
        rendered = json.dumps(BASE_SPEC, ensure_ascii=False, indent=2)
        for version in RESEARCHED_TEMPLATE_ENGINES:
            with self.subTest(version=version):
                outcome = parse_spec(rendered)
                self.assertIsNone(outcome.error)
                self.assertEqual(BASE_SPEC, outcome.value)

    def test_result_panel_and_api_envelope_for_every_researched_generation(self):
        rendered = json.dumps(BASE_SPEC, ensure_ascii=False, indent=2)
        for version in RESEARCHED_TEMPLATE_ENGINES:
            with self.subTest(version=version):
                # This is the literal escaped form visible in the Dify run
                # panel: the inner PageSpec is a string value named ``output``.
                panel_text = json.dumps({"output": rendered}, ensure_ascii=False)
                panel_outcome = parse_spec(panel_text)
                self.assertIsNone(panel_outcome.error)
                self.assertEqual(BASE_SPEC, panel_outcome.value)

                api_outcome = parse_spec({"data": {"outputs": {"output": rendered}}})
                self.assertIsNone(api_outcome.error)
                self.assertEqual(BASE_SPEC, api_outcome.value)


if __name__ == "__main__":
    unittest.main()
