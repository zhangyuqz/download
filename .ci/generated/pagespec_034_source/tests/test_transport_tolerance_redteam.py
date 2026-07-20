# -*- coding: utf-8 -*-
"""Adversarial regressions for user-first Dify transport recovery."""
from __future__ import annotations

import json
import html
import os
import sys
import unittest


TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import pagespec_transport as transport


BASE = {"version": 1, "blocks": [{"type": "text", "text": "EXPECTED"}]}
BASE_JSON = json.dumps(BASE, ensure_ascii=False, separators=(",", ":"))


def parse(raw):
    outcome = transport.parse_spec(raw)
    if outcome.error:
        raise AssertionError(outcome.error)
    return outcome


class HistoricalDifyEnvelopeTests(unittest.TestCase):
    def test_recursive_unknown_envelope_is_scored_and_unwrapped(self):
        outcome = parse({"meta": {"build": "old"},
                         "foo": {"bar": {"output": BASE_JSON}}})
        self.assertEqual(BASE, outcome.value)
        message = " ".join(event["message"] for event in outcome.events)
        self.assertIn("foo.bar.output", message)

    def test_structured_output_beats_conflicting_llm_text(self):
        outcome = parse({
            "data": {"outputs": {"text": "只是摘要", "structured_output": BASE}},
        })
        self.assertEqual(BASE, outcome.value)
        decision = next(event["message"] for event in outcome.events
                        if event["level"] == "WARN" and
                        ("多个不同载荷" in event["message"] or "解码需要猜测" in event["message"]))
        for token in ("候选=", "选择=", "原因=", "置信度="):
            self.assertIn(token, decision)
        self.assertIn("structured_output", decision)

    def test_answer_message_content_result_and_choices_are_recovered(self):
        forms = [
            {"answer": BASE_JSON, "metadata": {}},
            {"message": BASE_JSON, "status": "ok"},
            {"message": {"content": BASE_JSON}, "status": "ok"},
            {"content": BASE_JSON, "usage": {}},
            {"result": BASE_JSON, "elapsed_time": 1},
            {"choices": [{"message": {"content": BASE_JSON}}], "usage": {}},
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertEqual(BASE, parse(form).value)

    def test_arbitrary_code_output_name_with_metadata_is_recovered(self):
        forms = [
            {"data": {"outputs": {"my_pagespec": BASE_JSON, "status": "ok"}}},
            {"outputs": {"custom_result": BASE, "metadata": {"source": "code"}}},
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertEqual(BASE, parse(form).value)

    def test_api_version_or_event_type_does_not_mask_real_output(self):
        self.assertEqual(BASE, parse({"version": "api-v2", "output": BASE_JSON}).value)
        self.assertEqual(BASE, parse({"type": "workflow_finished", "output": BASE_JSON}).value)

    def test_metadata_root_does_not_mask_arbitrarily_named_deep_payload(self):
        forms = [
            {"version": "api-v2", "futurePayload": BASE_JSON},
            {"type": "workflow_finished", "futurePayload": BASE},
            {"version": "api-v3", "type": "event",
             "unknown": {"nested": {"customPage": BASE_JSON}}},
            {"version": "bridge-v1", "futurePayload": BASE["blocks"][0]},
            {"type": "template_result", "futurePayload": [
                {"type": "text", "text": "EXPECTED"}, {"type": "divider"},
            ]},
        ]
        for form in forms:
            with self.subTest(form=form):
                outcome = parse(form)
                self.assertEqual("EXPECTED", outcome.value["blocks"][0]["text"])
                self.assertTrue(any("包装路径" in event["message"]
                                    for event in outcome.events))

    def test_unknown_or_field_discarding_envelopes_are_visible_warnings(self):
        forms = [
            {"futurePayload": BASE_JSON},
            {"version": "api2", "metadata": {"trace": "x"},
             "futurePayload": BASE_JSON},
            {"output": BASE_JSON, "metadata": {"trace": "x"}},
        ]
        for form in forms:
            with self.subTest(form=form):
                outcome = parse(form)
                self.assertEqual(BASE, outcome.value)
                warning = next(event["message"] for event in outcome.events
                               if event["level"] == "WARN" and "解码需要猜测" in event["message"])
                for token in ("候选=", "选择=", "原因=", "置信度="):
                    self.assertIn(token, warning)

    def test_standard_single_payload_envelopes_remain_info_only(self):
        forms = [
            {"output": BASE_JSON},
            {"data": {"outputs": {"output": BASE_JSON}}},
        ]
        for form in forms:
            with self.subTest(form=form):
                outcome = parse(form)
                self.assertEqual(BASE, outcome.value)
                self.assertFalse(any(event["level"] == "WARN" for event in outcome.events))
                self.assertTrue(any("标准单一包装路径" in event["message"]
                                    for event in outcome.events))

    def test_real_text_block_containing_json_is_not_stolen_as_envelope(self):
        block = {"type": "text", "text": BASE_JSON}
        outcome = parse(block)
        self.assertEqual({"version": 1, "blocks": [block]}, outcome.value)

    def test_singleton_array_transport_is_not_mistaken_for_a_block(self):
        forms = [[BASE], [BASE_JSON], {"output": [BASE_JSON]}, ((BASE,),)]
        for form in forms:
            with self.subTest(form=form):
                self.assertEqual(BASE, parse(form).value)


class NearJsonRepairTests(unittest.TestCase):
    def test_mixed_prose_fragments_use_full_near_json_and_python_repair(self):
        forms = [
            "生成结果如下：{version:1,blocks:[{type:text,text:EXPECTED}]}，请查收。",
            ("模板说明：{'version':1,'blocks':[{'type':'text',"
             "'text':'EXPECTED } 仍是正文'}]}；以上。"),
        ]
        expected = ["EXPECTED", "EXPECTED } 仍是正文"]
        for raw, text in zip(forms, expected):
            with self.subTest(raw=raw):
                outcome = parse(raw)
                self.assertEqual(text, outcome.value["blocks"][0]["text"])
                messages = " ".join(event["message"] for event in outcome.events)
                self.assertIn("混合说明文字", messages)

    def test_mixed_prose_python_fragment_never_executes_calls(self):
        marker = os.path.join(os.path.dirname(__file__), "MIXED_FRAGMENT_MUST_NOT_EXIST")
        if os.path.exists(marker):
            os.unlink(marker)
        raw = (
            "说明：{'version':1,'blocks':[{'type':'text','text':'safe'}],"
            f"'x':__import__('pathlib').Path({marker!r}).write_text('bad')}}；结束"
        )
        outcome = transport.parse_spec(raw)
        self.assertFalse(os.path.exists(marker))
        self.assertIsNotNone(outcome.error)
        self.assertIsNone(outcome.value)
        self.assertTrue(any("静默当成正文" in event["message"] for event in outcome.events))

    def test_html_entities_are_recovered_for_one_through_sixty_four_layers(self):
        raw = BASE_JSON
        for layers in range(1, transport.MAX_STRING_LAYERS + 1):
            raw = html.escape(raw, quote=True)
            with self.subTest(layers=layers):
                outcome = parse(raw)
                self.assertEqual(BASE, outcome.value)
                self.assertTrue(any(
                    f"第 {layers} 层 HTML 实体编码" in event["message"]
                    for event in outcome.events
                ))

        # Layer 65 is explicitly rejected instead of silently becoming a text
        # block containing escaped PageSpec source.
        too_deep = html.escape(raw, quote=True)
        outcome = transport.parse_spec(too_deep)
        self.assertIsNotNone(outcome.error)
        self.assertIsNone(outcome.value)
        self.assertTrue(any("静默当成正文" in event["message"] for event in outcome.events))

    def test_valid_json_body_entities_are_not_decoded(self):
        value = {"version": 1, "blocks": [{"type": "text", "text": "&amp;quot;原文&amp;quot;"}]}
        outcome = parse(json.dumps(value, ensure_ascii=False))
        self.assertEqual(value, outcome.value)

    def test_ninth_outer_quote_stripped_escape_layer_is_recovered(self):
        # Some Template/UI transports stringify a value, then expose the
        # string contents without its surrounding quotes.  That leaves one
        # complete backslash layer for every hop.
        raw = BASE_JSON
        # Nine is the former implementation boundary.  Higher complete-string
        # layers are still bounded by MAX_STRING_LAYERS=64 and the 2 MB input
        # budget; constructing all 64 would grow exponentially.
        self.assertEqual(64, transport.MAX_STRING_LAYERS)
        for layers in range(1, 10):
            raw = json.dumps(raw, ensure_ascii=False)[1:-1]
            with self.subTest(layers=layers):
                outcome = parse(raw)
                self.assertEqual(BASE, outcome.value)
                self.assertTrue(any(
                    f"第 {layers} 层模板/Dify 反斜杠转义" in event["message"]
                    for event in outcome.events
                ))

    def test_literal_newline_inside_string_is_preserved_as_text(self):
        raw = '{"version":1,"blocks":[{"type":"text","text":"line 1\nline 2"}]}'
        outcome = parse(raw)
        self.assertEqual("line 1\nline 2", outcome.value["blocks"][0]["text"])
        self.assertTrue(any("原始换行" in event["message"] for event in outcome.events))

    def test_invalid_windows_and_regex_backslashes_are_kept_literal(self):
        raw = r'{"version":1,"blocks":[{"type":"code","code":"C:\Users\me\d+"}]}'
        outcome = parse(raw)
        self.assertEqual(r"C:\Users\me\d+", outcome.value["blocks"][0]["code"])
        self.assertTrue(any("无效 JSON 反斜杠" in event["message"] for event in outcome.events))

    def test_bare_keys_and_bare_values_are_deterministically_quoted(self):
        outcome = parse('{version:1,blocks:[{type:text,text:EXPECTED}]}')
        self.assertEqual(BASE, outcome.value)
        decision = next(event["message"] for event in outcome.events if "简单裸值" in event["message"])
        for token in ("候选=", "选择=", "原因=", "置信度="):
            self.assertIn(token, decision)

    def test_unquoted_free_text_with_spaces_and_colons_is_kept_as_one_value(self):
        forms = [
            ("{version:1,blocks:[{type:text,text:中文 报告}]}", "中文 报告"),
            ("{version:1,blocks:[{type:text,text:2026-07-20 12:30}]}",
             "2026-07-20 12:30"),
            ("{version:1,blocks:[{type:text,text:hello world}]}", "hello world"),
        ]
        for raw, expected in forms:
            with self.subTest(raw=raw):
                outcome = parse(raw)
                self.assertEqual(expected, outcome.value["blocks"][0]["text"])
                decision = next(event["message"] for event in outcome.events
                                if "自由文本值" in event["message"])
                for token in ("候选=", "选择=", "原因=", "置信度="):
                    self.assertIn(token, decision)

    def test_single_missing_key_or_value_quote_and_colon_are_guessed(self):
        forms = [
            '{version":1,"blocks":[{"type":"text","text":"EXPECTED"}]}',
            '{"version:1,"blocks":[{"type":"text","text":"EXPECTED"}]}',
            '{"version"1,"blocks":[{"type":"text","text":"EXPECTED"}]}',
            '{"version":1,"blocks":[{"type":text","text":"EXPECTED"}]}',
            '{"version":1,"blocks":[{"type":"text","text""EXPECTED"}]}',
        ]
        for raw in forms:
            with self.subTest(raw=raw):
                outcome = parse(raw)
                self.assertEqual("EXPECTED", outcome.value["blocks"][0]["text"])
                warning = next(event["message"] for event in outcome.events
                               if event["level"] == "WARN")
                for token in ("候选=", "选择=", "原因=", "置信度="):
                    self.assertIn(token, warning)

    def test_python_quotes_mixed_with_json_constants_are_supported(self):
        raw = ("{'version':1,'blocks':[{'type':'text','text':'EXPECTED',"
               "'enabled':true,'missing':null}]}")
        outcome = parse(raw)
        self.assertEqual("EXPECTED", outcome.value["blocks"][0]["text"])
        self.assertIs(outcome.value["blocks"][0]["enabled"], True)
        self.assertIsNone(outcome.value["blocks"][0]["missing"])
        self.assertTrue(any("混用" in event["message"] for event in outcome.events))

    def test_strict_json_huge_integer_keeps_structure_for_field_tolerance(self):
        decimal = "1" + "0" * 10_000
        raw = (
            '{"version":1,"blocks":[{"type":"heading","text":"kept","level":'
            + decimal + "}]}"
        )
        outcome = parse(raw)
        self.assertEqual("heading", outcome.value["blocks"][0]["type"])
        self.assertEqual("kept", outcome.value["blocks"][0]["text"])
        self.assertEqual(decimal, outcome.value["blocks"][0]["level"])
        self.assertTrue(any("超过运行时直接整数转换上限" in event["message"]
                            for event in outcome.events))


class NativeBridgeTests(unittest.TestCase):
    def test_python_repr_scalar_object_keys_match_native_stringification(self):
        raw = (
            "{'version':1,'blocks':[{'type':'table','rows':["
            "{None:'none',True:'bool',-2:'int',1.5:'float','1.5':'last'}]}]}"
        )
        outcome = parse(raw)
        row = outcome.value["blocks"][0]["rows"][0]
        self.assertEqual(
            {"None": "none", "True": "bool", "-2": "int", "1.5": "last"}, row
        )
        messages = " ".join(event["message"] for event in outcome.events)
        self.assertGreaterEqual(messages.count("非字符串键已转为字符串"), 4)
        self.assertIn("键字符串化后冲突", messages)

    def test_tuple_nested_bytes_and_non_string_keys_are_json_normalized(self):
        raw = {
            "version": 1,
            "blocks": (
                {"type": "table", "columns": [{"label": "A", "key": "1"}],
                 "rows": [{1: b"EXPECTED"}]},
            ),
        }
        outcome = parse(raw)
        block = outcome.value["blocks"][0]
        self.assertIsInstance(outcome.value["blocks"], list)
        self.assertEqual("EXPECTED", block["rows"][0]["1"])
        messages = " ".join(event["message"] for event in outcome.events)
        self.assertIn("tuple", messages)
        self.assertIn("非字符串键", messages)
        self.assertIn("UTF-8", messages)

    def test_utf16_bom_bytes_keep_chinese_and_emoji(self):
        value = {"version": 1, "blocks": [{"type": "text", "text": "中文😀"}]}
        outcome = parse(json.dumps(value, ensure_ascii=False).encode("utf-16"))
        self.assertEqual(value, outcome.value)
        self.assertTrue(any("UTF-16 BOM" in event["message"] for event in outcome.events))

    def test_native_huge_integer_key_is_exact_and_collision_is_audited(self):
        huge = 10 ** 10_000
        decimal = "1" + "0" * 10_000
        raw = {
            huge: "first",
            decimal: "last",
            "version": 1,
            "blocks": [{"type": "text", "text": "EXPECTED"}],
        }
        outcome = parse(raw)
        self.assertEqual("EXPECTED", outcome.value["blocks"][0]["text"])
        self.assertEqual("last", outcome.value[decimal])
        messages = " ".join(event["message"] for event in outcome.events)
        self.assertIn("native 非字符串键", messages)
        self.assertIn("字符串化后冲突", messages)


if __name__ == "__main__":
    unittest.main()
