# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import os
import sys
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pagespec_resources as resources


PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class File:
    dify_model_identity = resources.DIFY_FILE_IDENTITY
    url = ""

    def __init__(self, filename: str, blob: bytes):
        self.filename = filename
        self._blob = blob
        self.size = len(blob)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    )


class PageSpecResourceRedTeamTests(unittest.TestCase):
    def test_file_array_uses_later_valid_candidate(self):
        slots, warnings = resources.collect_slots({
            "slot1": [File("bad.png", b"not an image"), File("pixel.png", PIXEL_PNG)]
        })
        self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"))
        self.assertTrue(any("已采用第 2 项" in warning for warning in warnings))

    def test_non_browser_tiff_and_heic_are_visible_placeholders(self):
        fake_tiff = b"II*\x00" + b"\x00" * 32
        ftyp_payload = b"heic" + b"\x00\x00\x00\x00" + b"mif1"
        fake_heic = (8 + len(ftyp_payload)).to_bytes(4, "big") + b"ftyp" + ftyp_payload
        for name, blob, detected in (
            ("image.tiff", fake_tiff, "image/tiff"),
            ("image.heic", fake_heic, "image/heic"),
        ):
            with self.subTest(name=name):
                self.assertEqual(detected, resources.sniff_image_mime(blob))
                slots, warnings = resources.collect_slots({"slot1": File(name, blob)})
                self.assertTrue(slots["slot1"].startswith("data:image/svg+xml"))
                self.assertTrue(any("目标浏览器不能稳定直接显示" in item for item in warnings))

    def test_decoded_pixel_budget_is_aggregate_across_slots(self):
        with patch.object(resources, "MAX_TOTAL_IMAGE_PIXELS", 1):
            slots, warnings = resources.collect_slots({
                "slot1": File("one.png", PIXEL_PNG),
                "slot2": File("two.png", PIXEL_PNG),
            })
        self.assertTrue(slots["slot1"].startswith("data:image/png;base64,"))
        self.assertTrue(slots["slot2"].startswith("data:image/svg+xml"))
        self.assertTrue(any("累计解码像素" in item for item in warnings))

    def test_animation_frames_count_toward_decoded_pixel_budget(self):
        archive = ROOT / "tests" / "assets" / "image_slots_mainstream.zip"
        with zipfile.ZipFile(archive) as zipped:
            name = next(item for item in zipped.namelist() if item.startswith("slot3"))
            gif = zipped.read(name)
        dimensions, error = resources.validate_image_blob(gif, "image/gif")
        self.assertIsNone(error)
        frames = resources.image_frame_count(gif, "image/gif")
        self.assertGreater(frames, 1)
        decoded_pixels = dimensions[0] * dimensions[1] * frames
        with patch.object(resources, "MAX_TOTAL_IMAGE_PIXELS", decoded_pixels - 1):
            slots, warnings = resources.collect_slots({"slot1": File(name, gif)})
        self.assertTrue(slots["slot1"].startswith("data:image/svg+xml"))
        self.assertTrue(any("累计解码像素" in item for item in warnings))

    def test_png_without_idat_is_rejected(self):
        ihdr = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes((8, 6, 0, 0, 0))
        incomplete = (
            b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", ihdr)
            + png_chunk(b"IEND", b"")
        )
        _dimensions, error = resources.validate_image_blob(incomplete, "image/png")
        self.assertIn("IDAT", error or "")
        slots, warnings = resources.collect_slots({"slot1": File("empty.png", incomplete)})
        self.assertTrue(slots["slot1"].startswith("data:image/svg+xml"))
        self.assertTrue(any("IDAT" in item for item in warnings))

    def test_svg_foreignobject_resource_attributes_are_rejected(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" '
            b'xmlns:xhtml="http://www.w3.org/1999/xhtml" width="8" height="8">'
            b'<foreignObject width="8" height="8">'
            b'<xhtml:video poster="https://example.invalid/poster.png"/>'
            b'</foreignObject></svg>'
        )
        _dimensions, error = resources.validate_image_blob(svg, "image/svg+xml")
        self.assertIn("poster", error or "")

    def test_remote_file_cache_is_shared_across_slots(self):
        url = (
            "/files/tools/12345678-1234-4234-8234-123456789abc.png"
            "?timestamp=1700000000&nonce=n&sign=s"
        )

        class RemoteFile:
            dify_model_identity = resources.DIFY_FILE_IDENTITY
            filename = "pixel.png"
            size = len(PIXEL_PNG)

            def __init__(self):
                self.url = url

        with patch.dict(os.environ, {"FILES_URL": "https://dify.test"}, clear=True):
            with patch.object(resources, "_bounded_download", return_value=PIXEL_PNG) as download:
                slots, warnings = resources.collect_slots({
                    "slot1": RemoteFile(),
                    "slot2": RemoteFile(),
                })
        self.assertEqual(1, download.call_count)
        self.assertEqual([], warnings)
        self.assertTrue(all(value.startswith("data:image/png;base64,") for value in slots.values()))

    def test_remote_fetch_deadline_is_shared_across_slots(self):
        def remote_url(index: int) -> str:
            return (
                f"/files/tools/12345678-1234-4234-8234-{index:012d}.png"
                "?timestamp=1700000000&nonce=n&sign=s"
            )

        class RemoteFile:
            dify_model_identity = resources.DIFY_FILE_IDENTITY
            filename = "missing.png"
            size = 1

            def __init__(self, index: int):
                self.url = remote_url(index)

        ticks = iter((0.0, 0.0, 0.0, 25.0, 25.0, 25.0, 25.0))
        with patch.dict(os.environ, {"FILES_URL": "https://dify.test"}, clear=True):
            with patch.object(resources.time, "monotonic", side_effect=lambda: next(ticks, 25.0)):
                with patch.object(resources, "_bounded_download", side_effect=OSError("offline")) as download:
                    _slots, warnings = resources.collect_slots({
                        "slot1": RemoteFile(1),
                        "slot2": RemoteFile(2),
                    })
        self.assertEqual(1, download.call_count)
        self.assertTrue(any("总时限预算已用完" in item for item in warnings))


if __name__ == "__main__":
    unittest.main()
