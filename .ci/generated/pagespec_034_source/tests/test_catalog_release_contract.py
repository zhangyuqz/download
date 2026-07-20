"""Release-contract tests for the 172-library PageSpec catalogue profile."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "verification"))
sys.path.insert(0, str(ROOT / "dev_sources"))
import catalog_generate  # noqa: E402
import build_catalog_fixtures  # noqa: E402
import pagespec_resources  # noqa: E402


class CatalogRegistryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vendor_map = json.loads((ROOT / "vendor" / "vendor_map.json").read_text(encoding="utf-8"))
        cls.registry = json.loads((ROOT / "catalog" / "registry.json").read_text(encoding="utf-8"))
        cls.fixtures = {
            volume: json.loads((ROOT / "catalog" / f"volume{volume:02d}.json").read_text(encoding="utf-8"))
            for volume in range(1, 5)
        }

    def test_registry_is_exactly_172_unique_vendor_keys(self):
        libraries = self.vendor_map["libs"]
        covers = [key for volume in self.registry["volumes"] for key in volume["covers"]]
        self.assertEqual(172, self.registry["catalog_count"])
        self.assertEqual(172, len(libraries))
        self.assertEqual(172, len(covers))
        self.assertEqual(set(libraries), set(covers))
        self.assertEqual([], [key for key, count in Counter(covers).items() if count != 1])
        self.assertEqual("\n".join(sorted(covers)),
                         self.registry["coverage_sha256_input"].rstrip("\n"))

    def test_frozen_volume_partition_is_35_63_41_33(self):
        self.assertEqual([35, 63, 41, 33], [x["count"] for x in self.registry["volumes"]])
        for volume, expected in catalog_generate.EXPECTED_COUNTS.items():
            fixture = self.fixtures[volume]
            registry_row = self.registry["volumes"][volume - 1]
            self.assertEqual("catalog-fixture/v1", fixture["schema"])
            self.assertEqual(volume, fixture["volume"])
            self.assertEqual(expected, fixture["count"])
            self.assertEqual(expected, len(fixture["covers"]))
            self.assertEqual(registry_row["covers"], fixture["covers"])

    def test_every_covered_library_is_reached_by_declared_assets_or_dependencies(self):
        libraries = self.vendor_map["libs"]
        for volume, fixture in self.fixtures.items():
            reached = set()

            def visit(key):
                self.assertIn(key, libraries, f"volume {volume} references unknown {key}")
                if key in reached:
                    return
                reached.add(key)
                for dependency in libraries[key].get("deps", []):
                    visit(dependency)

            for asset in fixture["assets"]:
                self.assertIn(asset.get("kind"), ("js", "css"))
                visit(asset.get("key"))
            self.assertTrue(set(fixture["covers"]).issubset(reached),
                            f"volume {volume} has a cover with no load path")

    def test_all_declared_vendor_files_exist_with_exact_bytes_and_sha256(self):
        checked = set()
        for key, spec in self.vendor_map["libs"].items():
            names = ([spec["file"]] if spec.get("file") else []) + list(spec.get("css") or [])
            for name in names:
                path = ROOT / "vendor" / name
                self.assertTrue(path.is_file(), f"{key}: missing {name}")
                blob = path.read_bytes()
                self.assertEqual(spec["bytes"][name], len(blob), f"{key}: byte count {name}")
                self.assertEqual(spec["sha256"][name], hashlib.sha256(blob).hexdigest(),
                                 f"{key}: SHA-256 {name}")
                checked.add(name)
        self.assertGreater(len(checked), 172)

    def test_fixture_code_has_structured_checks_and_10_8_second_stability_gate(self):
        for volume, fixture in self.fixtures.items():
            javascript = "\n".join(fixture["prelude_js"]) + "\n" + fixture["runner_js"]
            self.assertIn("window.__MEANINGFUL_SUITE__", javascript, f"volume {volume}")
            self.assertIn("window.__ALL_TESTS_DONE__", javascript, f"volume {volume}")
            self.assertIn("10800", javascript, f"volume {volume}")
            self.assertIn("checks", javascript, f"volume {volume}")
            self.assertRegex(javascript, r"\b(actual|expected)\b", f"volume {volume}")

    def test_frozen_fixtures_are_exactly_reproducible_from_checked_in_yml(self):
        """Release evidence and the four importable workflows cannot drift."""
        for volume, frozen in self.fixtures.items():
            workflow = ROOT / "dev_sources" / f"全库有意义测试_卷0{volume}.yml"
            rebuilt = build_catalog_fixtures.build_volume(workflow, volume, self.vendor_map)
            self.assertEqual(frozen, rebuilt, f"volume {volume} fixture/YML drift")

    def test_retina_canvas_samples_convert_logical_to_backing_store_coordinates(self):
        javascript = self.fixtures[3]["runner_js"]
        fabric_case = javascript.split("{key:'fabric'", 1)[1].split("{key:'bulma'", 1)[0]
        paper_case = javascript.split("{key:'paper'", 1)[1].split("{key:'p5'", 1)[0]
        self.assertRegex(
            fabric_case,
            r"var (?P<backing>[A-Za-z_$][\w$]*)=fc\.lowerCanvasEl,"
            r"(?P<ratio_x>[A-Za-z_$][\w$]*)=(?P=backing)\.width/fc\.getWidth\(\),"
            r"(?P<ratio_y>[A-Za-z_$][\w$]*)=(?P=backing)\.height/fc\.getHeight\(\),"
            r"(?P<sample>[A-Za-z_$][\w$]*)=\[Math\.round\(22\*(?P=ratio_x)\),"
            r"Math\.round\(18\*(?P=ratio_y)\)\].*"
            r"getImageData\((?P=sample)\[0\],(?P=sample)\[1\],1,1\).*"
            r"backingRatio:\[(?P=ratio_x),(?P=ratio_y)\]",
        )
        self.assertRegex(
            paper_case,
            r"(?P<ratio_x>[A-Za-z_$][\w$]*)=c\.width/s\.view\.viewSize\.width,"
            r"(?P<ratio_y>[A-Za-z_$][\w$]*)=c\.height/s\.view\.viewSize\.height,"
            r"(?P<sample>[A-Za-z_$][\w$]*)=\[Math\.round\(22\*(?P=ratio_x)\),"
            r"Math\.round\(18\*(?P=ratio_y)\)\].*"
            r"getImageData\((?P=sample)\[0\],(?P=sample)\[1\],1,1\).*"
            r"backingRatio:\[(?P=ratio_x),(?P=ratio_y)\]",
        )
        self.assertNotIn("getImageData(15,15,1,1)", fabric_case)
        self.assertNotIn("getImageData(15,15,1,1)", paper_case)


class CatalogCompilerContractTests(unittest.TestCase):
    def test_all_four_pages_compile_with_zero_static_failures_and_under_30m(self):
        all_covers = []
        for volume, expected in catalog_generate.EXPECTED_COUNTS.items():
            blob, record = catalog_generate.generate_one(volume)
            self.assertEqual([], record["failures"], f"volume {volume}: {record['failures']}")
            self.assertEqual(expected, record["library_count"])
            self.assertLess(len(blob), 30_000_000)
            self.assertEqual([], record["missing"])
            self.assertEqual([], record["static_html_audit"])
            self.assertEqual({"INFO": 0, "WARN": 0, "SKIP": 0}, record["report"])
            text = blob.decode("utf-8")
            self.assertEqual(1, len(re.findall(
                r'<meta\s+http-equiv="Content-Security-Policy"', text, flags=re.I)))
            self.assertIn("default-src &#x27;none&#x27;", text[:2000])
            self.assertIn("connect-src &#x27;none&#x27;", text[:2000])
            self.assertIn("worker-src &#x27;none&#x27;", text[:2000])
            all_covers.extend(record["libraries"])
        self.assertEqual(172, len(all_covers))
        self.assertEqual(172, len(set(all_covers)))

    def test_catalog_html_installs_human_readability_and_geometry_gates(self):
        required = (
            "ps-catalog-checks", "ps-catalog-full", "summary_limit:120",
            "no_clipped_elements", "items_at_most_760px",
            "page_height_budget_per_library_px:650", "static_warn_and_skip_zero",
            "presentation_gate_pass", "Object.defineProperty(window,'__ALL_TESTS_DONE__'",
        )
        for volume in catalog_generate.EXPECTED_COUNTS:
            blob, record = catalog_generate.generate_one(volume)
            self.assertEqual([], record["failures"])
            html = blob.decode("utf-8")
            for marker in required:
                self.assertIn(marker, html, f"volume {volume}: missing {marker}")
            self.assertLess(
                html.index("Object.defineProperty(window,'__ALL_TESTS_DONE__'"),
                html.index("window.__MEANINGFUL_SUITE__={"),
                f"volume {volume}: presentation gate must be installed before the suite",
            )

    def test_doc_language_aliases_are_normalized_without_static_warning(self):
        tool = catalog_generate.compiler_tool()
        for key in ("lang", "language", "language_code", "locale", "语言"):
            spec = {"version": 1, "doc": {key: "ZH_cn"},
                    "blocks": [{"type": "text", "text": "语言测试"}]}
            html, report, meta = catalog_generate.pagespec.render_document(
                spec,
                {
                    "nonce": "catalog-language-test",
                    "slots": {},
                    "placeholder": lambda _name: "",
                    "load_libs": tool._make_lib_loader("catalog-language-test"),
                },
            )
            self.assertIsNone(meta["fatal"], key)
            self.assertEqual(0, report.counts["WARN"], key)
            self.assertIn('<html lang="zh-CN"', html, key)

    def test_hash_mismatch_is_detected_not_silently_used(self):
        spec = {"sha256": {"demo.js": hashlib.sha256(b"original").hexdigest()}}
        with mock.patch.object(pagespec_resources, "_read_vendor", return_value="tampered"):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                pagespec_resources.read_verified_vendor(spec, "demo.js")

    def test_missing_and_io_error_are_localized_by_catalog_loader(self):
        vendor_map = {"libs": {"broken": {
            "file": "broken.js", "css": [], "deps": [], "global": "Broken"
        }}}

        for error in (FileNotFoundError("missing"), OSError("read failure"), ValueError("bad hash")):
            tool = catalog_generate.render_page.RenderPageTool.__new__(
                catalog_generate.render_page.RenderPageTool)
            with mock.patch.object(
                pagespec_resources, "load_vendor_map", return_value=vendor_map
            ), mock.patch.object(
                pagespec_resources, "read_verified_vendor", side_effect=error
            ):
                css, javascript, missing = tool._make_catalog_loader("audit")(
                    [{"kind": "js", "key": "broken"}]
                )
            self.assertEqual("", css)
            self.assertEqual("", javascript)
            self.assertEqual(1, len(missing))
            self.assertIn(type(error).__name__, missing[0])

    def test_unknown_asset_and_unknown_kind_do_not_crash_loader(self):
        tool = catalog_generate.compiler_tool()
        css, javascript, missing = tool._make_catalog_loader("audit")([
            {"kind": "js", "key": "not-in-map"},
            {"kind": "future", "key": "vue"},
        ])
        self.assertEqual("", css)
        self.assertEqual("", javascript)
        self.assertEqual(2, len(missing))
        self.assertIn("not-in-map", missing[0])
        self.assertIn("unknown asset kind", missing[1])


if __name__ == "__main__":
    unittest.main()
