# -*- coding: utf-8 -*-
"""Compatibility corpus ported from the DOCX 0.0.19 file transport gate.

The DOCX corpus is intentionally broader than one SDK model: Dify template,
code and tool nodes have emitted File objects, dicts, repr strings, JSON
strings and root-relative URLs across releases.  PageSpec must consume those
values as user input while keeping download budgets and metadata SSRF blocks.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pagespec_resources as resources


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
QUERY = "?timestamp=1700000000&nonce=a%2Fb&sign=x%3D&sign=x%3D"
PATH = "/files/tools/12345678-1234-4234-8234-123456789abc.png" + QUERY


class PageSpecDocx019FileParityTests(unittest.TestCase):
    def test_bytes_data_uri_base64_file_like_and_future_dicts(self):
        forms = (
            PNG,
            bytearray(PNG),
            memoryview(PNG),
            BytesIO(PNG),
            "data:image/png;base64," + base64.b64encode(PNG).decode("ascii"),
            base64.b64encode(PNG).decode("ascii"),
            {"content": base64.b64encode(PNG).decode("ascii")},
            {"future_wrapper": {"payload": {"bytes": PNG}}},
        )
        for index, value in enumerate(forms):
            with self.subTest(index=index):
                slots, warnings = resources.collect_slots({"slot1": value})
                self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"), warnings)

    def test_all_historical_url_fields_and_nested_wrappers(self):
        for field in resources.FILE_URL_FIELDS:
            value = {"output": {"result": {"files": [{field: PATH}]}}}
            with self.subTest(field=field):
                with patch.dict(os.environ, {"FILES_URL": "https://dify.test"}, clear=True):
                    with patch.object(resources, "_bounded_download", return_value=PNG) as get:
                        slots, warnings = resources.collect_slots({"slot1": value})
                self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"), warnings)
                self.assertGreaterEqual(get.call_count, 1)

    def test_json_wrappers_and_file_repr_are_recognized(self):
        payload = {"files": [{"filename": "a.png", "remote_url": PATH}]}
        wrapped = json.dumps(json.dumps(json.dumps(payload, ensure_ascii=False)))
        repr_value = "INFO files=[File(filename='a.png', remote_url='" + PATH + "')] status=success"
        for value in (wrapped, repr_value):
            with self.subTest(value=value[:20]):
                with patch.dict(os.environ, {"FILES_URL": "https://dify.test"}, clear=True):
                    with patch.object(resources, "_bounded_download", return_value=PNG):
                        slots, warnings = resources.collect_slots({"slot1": value})
                self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"), warnings)

    def test_url_spelling_matrix_preserves_signed_query(self):
        shapes = (
            PATH,
            "files/tools/12345678-1234-4234-8234-123456789abc.png" + QUERY,
            "//dify.test" + PATH,
            "dify.test" + PATH,
            "https:/dify.test" + PATH,
            r"https:\\dify.test\files\tools\12345678-1234-4234-8234-123456789abc.png" + QUERY,
            "https：／／dify.test／files／tools／12345678-1234-4234-8234-123456789abc.png" + QUERY,
            "https://dify.test/files/tools/12345678-1234-4234-8234-123456789abc.png" + QUERY.replace("&", "&amp;"),
            "https%3A%2F%2Fdify.test%2Ffiles%2Ftools%2F12345678-1234-4234-8234-123456789abc.png" + QUERY,
            "![图片](" + PATH + ")",
            '<img src="' + PATH + '">',
            "图片地址：" + PATH + " 请读取",
        )
        with patch.dict(os.environ, {"FILES_URL": "https://dify.test"}, clear=True):
            for index, value in enumerate(shapes):
                with self.subTest(index=index, value=value):
                    candidates = resources._file_url_candidates(value)
                    self.assertTrue(candidates, value)
                    self.assertTrue(any("/files/tools/" in item for item in candidates), candidates[:5])
                    self.assertTrue(any("nonce=a%2Fb" in item for item in candidates), candidates[:5])

    def test_first_bad_blob_or_url_falls_through_to_later_candidate(self):
        fallback = "https://dify.test" + PATH
        first = {"filename": "bad.png", "_blob": b"<html>bad</html>", "url": fallback}
        second_wrapper = {"files": [{"url": "https://bad.test/no.png"}, {"url": fallback}]}
        with patch.dict(os.environ, {"FILES_URL": "https://dify.test"}, clear=True):
            with patch.object(resources, "_bounded_download", side_effect=[PNG]) as get:
                slots, warnings = resources.collect_slots({"slot1": first})
            self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"), warnings)
            self.assertEqual(1, get.call_count)
            with patch.object(resources, "_bounded_download", side_effect=[OSError("bad"), PNG]):
                slots, warnings = resources.collect_slots({"slot1": second_wrapper})
            self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"), warnings)

    def test_relative_official_file_object_and_missing_identity_work(self):
        # Public Dify discussions show remote_url as a root-relative /files path;
        # old versions used a plain local_file dict without SDK identity.
        value = {
            "type": "image", "transfer_method": "local_file", "remote_url": "",
            "filename": "a.png", "size": len(PNG), "url": PATH,
        }
        with patch.dict(os.environ, {"DIFY_INNER_API_URL": "http://api:5001"}, clear=True):
            with patch.object(resources, "_bounded_download", return_value=PNG):
                slots, warnings = resources.collect_slots({"slot1": value})
        self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"), warnings)

    def test_metadata_network_and_credentials_remain_forbidden(self):
        forbidden = (
            "http://169.254.169.254/latest/meta-data",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://100.100.100.200/latest/meta-data",
            "http://user:pass@dify.test/files/x",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertEqual([], resources._file_url_candidates(value))


if __name__ == "__main__":
    unittest.main()
