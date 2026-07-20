# -*- coding: utf-8 -*-
"""Transport regressions ported from the verified DOCX 0.0.19 decoder."""
from __future__ import annotations

import json
import os
import sys
import unittest


TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import pagespec_transport as transport


BASE = {"version": 1, "blocks": [{"type": "text", "text": "EXPECTED"}]}
BASE_JSON = json.dumps(BASE, ensure_ascii=False, separators=(",", ":"))


def parsed(raw):
    outcome = transport.parse_spec(raw)
    if outcome.error:
        raise AssertionError(outcome.error)
    return outcome


class Dify171AndLayerParityTests(unittest.TestCase):
    def test_exact_dify_171_runner_duplicate_outer_quote(self):
        raw = '""' + BASE_JSON.replace('"', '\\"') + '""'
        outcome = parsed(raw)
        self.assertEqual(BASE, outcome.value)
        self.assertTrue(any("Dify 1.7.1 runner" in event["message"]
                            for event in outcome.events))
        # The failure mode being guarded is a valid-looking HTML page whose
        # body contains escaped JSON rather than the requested report.
        self.assertNotEqual(raw, outcome.value["blocks"][0]["text"])

    def test_sixty_four_native_wrapper_layers_are_accepted_and_65_rejected(self):
        wrapped = BASE
        for _ in range(64):
            wrapped = {"data": wrapped}
        self.assertEqual(BASE, parsed(wrapped).value)
        rejected = transport.parse_spec({"data": wrapped})
        self.assertIn("包装层数超过 64", rejected.error)

    def test_ninth_complete_json_string_layer_is_no_longer_the_boundary(self):
        wrapped = BASE_JSON
        for _ in range(9):
            wrapped = json.dumps(wrapped, ensure_ascii=False)
        self.assertEqual(BASE, parsed(wrapped).value)
        self.assertEqual(64, transport.MAX_STRING_LAYERS)


class DirtyJsonParityTests(unittest.TestCase):
    def test_structural_unicode_japanese_quotes_and_backticks(self):
        variants = {
            "zero-width":
                '{\u200b"version"\u200c:\u200d1,"blocks":[{"type":"text","text":"EXPECTED"}]}',
            "nbsp-and-ideographic-space":
                '{\u00a0"version"\u3000:\u00a01,"blocks":[{"type":"text","text":"EXPECTED"}]}',
            "japanese-quotes-and-brackets":
                '｛『version』：1，『blocks』：【｛『type』：『text』，『text』：『EXPECTED』｝】｝',
            "backticks":
                '{`version`:1,`blocks`:[{`type`:`text`,`text`:`EXPECTED`}]}',
        }
        for name, raw in variants.items():
            with self.subTest(name=name):
                outcome = parsed(raw)
                self.assertEqual(BASE, outcome.value)
                self.assertTrue(any("DOCX 0.0.19" in event["message"]
                                    for event in outcome.events))

    def test_repeated_leading_and_trailing_commas(self):
        raw = '{,"version":1,,"blocks":[,{"type":"text","text":"EXPECTED",},],}'
        self.assertEqual(BASE, parsed(raw).value)

    def test_unescaped_internal_double_quotes_are_kept_as_content(self):
        raw = '{"version":1,"blocks":[{"type":"text","text":"他说"你好"。"}]}'
        outcome = parsed(raw)
        self.assertEqual('他说"你好"。', outcome.value["blocks"][0]["text"])

    def test_undefined_changes_only_bare_value_positions(self):
        raw = (
            '{"version":1,"blocks":[{"type":"text","text":undefined}],'
            '"undefined":"undefined","word":"preundefinedpost"}'
        )
        outcome = parsed(raw)
        self.assertIsNone(outcome.value["blocks"][0]["text"])
        self.assertEqual("undefined", outcome.value["undefined"])
        self.assertEqual("preundefinedpost", outcome.value["word"])
        self.assertTrue(any("裸 undefined" in event["message"] for event in outcome.events))

    def test_failed_pagespec_like_input_is_never_a_plain_text_success(self):
        raw = '{"version":1,"blocks":[__import__("pathlib").Path("x")]} '
        outcome = transport.parse_spec(raw)
        self.assertIsNotNone(outcome.error)
        self.assertIsNone(outcome.value)
        self.assertTrue(any("静默当成正文" in event["message"] for event in outcome.events))

    def test_plain_human_prose_still_remains_a_text_block(self):
        outcome = parsed("这是一段普通正文，不是 JSON")
        self.assertEqual("这是一段普通正文，不是 JSON",
                         outcome.value["blocks"][0]["text"])


class LatexTransportParityTests(unittest.TestCase):
    def test_single_slash_control_commands_are_recovered_only_in_latex(self):
        raw = (
            '{"version":1,"blocks":['
            '{"type":"formula","latex":"\\frac{1}{2}+\\beta+\\rho+\\times+\\nabla"},'
            '{"type":"text","text":"tab\\text stays a JSON tab in ordinary text"}'
            ']}'
        )
        outcome = parsed(raw)
        self.assertEqual(r"\frac{1}{2}+\beta+\rho+\times+\nabla",
                         outcome.value["blocks"][0]["latex"])
        self.assertIn("\t", outcome.value["blocks"][1]["text"])
        self.assertNotIn(r"\text", outcome.value["blocks"][1]["text"])
        self.assertTrue(any("冻结白名单" in event["message"] for event in outcome.events))

    def test_correct_double_slash_latex_is_zero_change(self):
        spec = {"version": 1, "blocks": [{"type": "formula", "latex": r"\frac{1}{2}"}]}
        outcome = parsed(json.dumps(spec, ensure_ascii=False))
        self.assertEqual(spec, outcome.value)
        self.assertFalse(any("latex 字段中的 JSON 控制转义" in event["message"]
                             for event in outcome.events))


if __name__ == "__main__":
    unittest.main()
