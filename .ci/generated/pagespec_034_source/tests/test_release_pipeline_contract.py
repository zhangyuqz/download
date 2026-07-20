from __future__ import annotations

import json
import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMatrixContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = json.loads(
            (ROOT / "release_matrix_0.3.2_0.3.3.json").read_text(encoding="utf-8")
        )

    def test_dual_variants_dependency_and_catalog_partition_are_frozen(self) -> None:
        self.assertEqual("dify_plugin>=0.9.0", self.matrix["requirements_exact"])
        self.assertEqual(
            "1c45ed4f06ccd9375382c728ebd6b7394da854273f59666c25c0d7b914a0dbf0",
            self.matrix["sdk_gate_wheel_sha256"],
        )
        self.assertEqual("dify_plugin>=0.9.0\n", (ROOT / "requirements.txt").read_text())
        self.assertEqual(
            [("0.3.2", "1.7.1"), ("0.3.3", "1.14.2")],
            [
                (item["version"], item["minimum_dify_version"])
                for item in self.matrix["variants"]
            ],
        )
        self.assertEqual([35, 63, 41, 33], list(self.matrix["catalog_volumes"].values()))
        self.assertEqual(
            {
                "registry.json": "2246ec65f357fac1487bfb2f77fd1e11ae018401bf6bd96d9209ac2967be50c2",
                "volume01.json": "80b44901ce0a8d5bff1b433fc3ee4ef86d60390a03e7506300fd2f1f76d038b0",
                "volume02.json": "025391598b3151f12649150ae949fb21efbc975bd170911a42ec275a2c2d3fff",
                "volume03.json": "c889d3ddd9b6c39fa0062d5e1d37df70b70672add463b0aad52f7f4683a5665b",
                "volume04.json": "6a2da3977907f88e388cf767cb08057c15e94cde373a95dbc659a80766d75804",
            },
            self.matrix["catalog_fixture_sha256"],
        )
        self.assertEqual(
            {
                "version": "v0.6.1",
                "sha256": "a8495a4392e377737e7166e1f7afb0b40bc8cc5fa4d44eaa65c08e60c679d7d5",
            },
            self.matrix["official_dify_cli"],
        )
        self.assertEqual(109, self.matrix["browser_gates"]["legacy_major"])
        self.assertEqual(149, self.matrix["browser_gates"]["current_major"])
        self.assertEqual(
            {
                "family": "Noto Sans CJK SC",
                "source": "https://github.com/notofonts/noto-cjk/tree/Sans2.004",
                "bytes": 16437364,
                "sha256": "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b",
            },
            self.matrix["browser_gates"]["cjk_test_font"],
        )
        self.assertEqual(
            {
                "chromium_executable_sha256": "a47ba3615bb98d97e25379af98cf01cef555b24ff2684c4852f63e546d95c11a",
                "support_lib_tree_sha256": "adb897e1f28cd0f1a3abcaa6685ac7a2a2cc137dae4a625dab30d08f3f178d51",
                "package_lock_sha256": "308e88ea88fe4414b53db6c63b9393c6155728ecc533fb6d48ce66ef8e2cc36e",
                "node_modules_tree_sha256": "f0bc5e785e06e018209ae9ab1bcccee2697fc1d80e775a9bf608f462b52ce991",
                "puppeteer_core": "19.6.3",
                "sparticuz_chromium": "109.0.6",
            },
            self.matrix["browser_gates"]["runtime_109"],
        )
        self.assertEqual(
            {
                "chromium_executable_sha256": "434d2607c55941dcaa7fdd19b0800ee90488922fd9744a6642ef527ba8fabf63",
                "support_lib_tree_sha256": "1e5cbf99195a3f9e5d9b75a01cf1e4532f17b4be6037ff0a855e77d344cfe573",
                "package_lock_sha256": "67791269dad2b78dc39b36c191a9de9e51dabb889702d6a39fd6dc4b96dbcf0a",
                "node_modules_tree_sha256": "8fa5154c915e063fc417ee418016e7f0e90ff0181585fd9abb5d4d15992efd7c",
                "puppeteer_core": "25.0.4",
                "sparticuz_chromium": "149.0.0",
            },
            self.matrix["browser_gates"]["runtime_current"],
        )
        self.assertEqual(
            [
                "PageSpec_0.3.2_Dify1.7.1_四卷全库网页测试包.zip",
                "PageSpec_0.3.3_Dify1.14.2_四卷全库网页测试包.zip",
            ],
            [item["html_test_filename"] for item in self.matrix["variants"]],
        )

    def test_readme_localisation_and_official_packaging_contract(self) -> None:
        root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "readme/README_zh_Hans.md").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[\u3400-\u9fff]", root_readme))
        self.assertIsNotNone(re.search(r"[\u3400-\u9fff]", chinese))
        builder = (ROOT / "build_release.py").read_text(encoding="utf-8")
        self.assertIn('[dify_cli, "plugin", "package"', builder)
        self.assertIn('if target.suffix == ".difypkg"', builder)
        self.assertIn("difypkg creation is reserved for the official Dify CLI", builder)
        self.assertIn('variant["html_test_filename"]', builder)
        self.assertIn("complete delivery contains forbidden bare HTML", builder)
        self.assertIn("Dify CLI binary SHA-256 is not the audited official release binary", builder)
        browser = (ROOT / "verification/scripts/catalog_browser_audit.mjs").read_text()
        self.assertIn("mobile: true", browser)
        self.assertIn("Emulation.setTouchEmulationEnabled", browser)
        self.assertIn("navigateAndWaitForPageSpec", browser)
        self.assertIn("globalThis.__ALL_TESTS_DONE__===true", browser)
        self.assertNotIn("Page.domContentEventFired", browser)
        self.assertNotIn("Page.loadEventFired", browser)
        package_gate = (ROOT / "verification/scripts/verify_packaged_plugin.py").read_text()
        self.assertIn("PluginRegistration(config)", package_gate)
        self.assertIn("registered_class(runtime=runtime, session=session)", package_gate)
        self.assertNotIn("import render_page\n", package_gate)
        self.assertIn("pagespec_resource_redteam_0.3.2.json", builder)
        self.assertIn("audit_dify_template_contracts.py", builder)
        self.assertIn('"BROWSER_RUNTIME": str(runtime_root)', builder)
        self.assertIn('"FONTCONFIG_PATH": str(fontconfig_file.parent)', builder)
        self.assertIn('"FONTCONFIG_FILE": str(fontconfig_file)', builder)
        self.assertIn('"BROWSER_CJK_FONT_FAMILY": cjk_font_identity["family"]', builder)
        self.assertIn("cjk_test_font_available", browser)
        self.assertIn("--browser-runtime-109", builder)
        self.assertIn("--browser-runtime-current", builder)
        self.assertIn("--cjk-font", builder)

    def test_sdk_python_virtualenv_shim_is_not_dereferenced(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import build_release

        with tempfile.TemporaryDirectory(prefix="pagespec-sdk-python-shim-") as temporary:
            root = Path(temporary)
            base = root / "base-python"
            base.write_bytes(b"placeholder")
            shim = root / "venv-python"
            shim.symlink_to(base.name)
            resolved = build_release.resolve_python_environment(
                str(shim), "SDK Python"
            )
            self.assertEqual(shim, resolved)
            self.assertTrue(resolved.is_symlink())
            self.assertNotEqual(base.resolve(), resolved)

    def test_browser_runtime_lock_and_installed_versions_are_both_enforced(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import build_release

        with tempfile.TemporaryDirectory(prefix="pagespec-browser-runtime-") as temporary:
            runtime = Path(temporary)
            packages = {
                "puppeteer-core": ("19.6.3", "sha512-puppeteer"),
                "@sparticuz/chromium": ("109.0.6", "sha512-chromium"),
            }
            lock = {
                "name": "frozen-test-runtime",
                "lockfileVersion": 3,
                "packages": {
                    f"node_modules/{name}": {
                        "version": version,
                        "integrity": integrity,
                    }
                    for name, (version, integrity) in packages.items()
                },
            }
            lock_path = runtime / "package-lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            for name, (version, _integrity) in packages.items():
                package_path = runtime / "node_modules" / Path(*name.split("/")) / "package.json"
                package_path.parent.mkdir(parents=True, exist_ok=True)
                package_path.write_text(json.dumps({"version": version}), encoding="utf-8")
            payload = runtime / "node_modules" / "puppeteer-core" / "runner.js"
            payload.write_text("export const frozen = true;\n", encoding="utf-8")
            tree = build_release.deterministic_node_tree_identity(runtime / "node_modules")
            contract = {
                "package_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                "node_modules_tree_sha256": tree["sha256"],
                "puppeteer_core": "19.6.3",
                "sparticuz_chromium": "109.0.6",
            }
            resolved, report = build_release.validate_browser_runtime(
                runtime, contract, "test"
            )
            self.assertEqual(runtime.resolve(), resolved)
            self.assertEqual(
                contract["package_lock_sha256"],
                report["package_lock"]["sha256"],
            )
            self.assertEqual(contract["node_modules_tree_sha256"], report["node_modules"]["sha256"])
            installed = runtime / "node_modules" / "puppeteer-core" / "package.json"
            installed.write_text(json.dumps({"version": "19.6.2"}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "version mismatch"):
                build_release.validate_browser_runtime(runtime, contract, "test")
            installed.write_text(json.dumps({"version": "19.6.3"}), encoding="utf-8")
            payload.write_text("export const frozen = false;\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "node_modules tree SHA-256 mismatch"):
                build_release.validate_browser_runtime(runtime, contract, "test")
            payload.write_text("export const frozen = true;\n", encoding="utf-8")
            lock_path.write_text(lock_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "package-lock SHA-256 mismatch"):
                build_release.validate_browser_runtime(runtime, contract, "test")

    def test_chromium_executable_hash_is_frozen(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import build_release

        with tempfile.TemporaryDirectory(prefix="pagespec-browser-executable-") as temporary:
            executable = Path(temporary) / "chromium"
            executable.write_bytes(b"frozen chromium fixture")
            support_lib = executable.parent / "lib"
            support_lib.mkdir()
            (support_lib / "libfixture.so").write_bytes(b"frozen support library")
            expected = hashlib.sha256(executable.read_bytes()).hexdigest()
            support_expected = build_release.deterministic_node_tree_identity(
                support_lib
            )["sha256"]
            report = build_release.validate_chromium_executable(
                executable,
                {
                    "chromium_executable_sha256": expected,
                    "support_lib_tree_sha256": support_expected,
                },
                "test",
            )
            self.assertEqual(expected, report["sha256"])
            self.assertEqual(support_expected, report["support_lib"]["sha256"])
            executable.write_bytes(b"substituted chromium fixture")
            with self.assertRaisesRegex(RuntimeError, "executable SHA-256 mismatch"):
                build_release.validate_chromium_executable(
                    executable,
                    {
                        "chromium_executable_sha256": expected,
                        "support_lib_tree_sha256": support_expected,
                    },
                    "test",
                )
            executable.write_bytes(b"frozen chromium fixture")
            (support_lib / "libfixture.so").write_bytes(b"substituted support library")
            with self.assertRaisesRegex(RuntimeError, "support lib tree SHA-256 mismatch"):
                build_release.validate_chromium_executable(
                    executable,
                    {
                        "chromium_executable_sha256": expected,
                        "support_lib_tree_sha256": support_expected,
                    },
                    "test",
                )

    def test_node_tree_hashes_symlink_target_and_rejects_escape(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import build_release

        with tempfile.TemporaryDirectory(prefix="pagespec-node-tree-") as temporary:
            base = Path(temporary)
            tree = base / "node_modules"
            package = tree / "package"
            package.mkdir(parents=True)
            first = package / "first.js"
            second = package / "second.js"
            first.write_text("first\n", encoding="utf-8")
            second.write_text("second\n", encoding="utf-8")
            link = tree / "runner"
            link.symlink_to(first.relative_to(tree))
            first_identity = build_release.deterministic_node_tree_identity(tree)
            self.assertEqual(1, first_identity["symlinks"])
            link.unlink()
            link.symlink_to(second.relative_to(tree))
            second_identity = build_release.deterministic_node_tree_identity(tree)
            self.assertNotEqual(first_identity["sha256"], second_identity["sha256"])
            outside = base / "outside.js"
            outside.write_text("outside\n", encoding="utf-8")
            link.unlink()
            link.symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, "escapes the tree"):
                build_release.deterministic_node_tree_identity(tree)

    def test_release_builder_refuses_custom_or_modified_matrix(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import build_release

        actual = hashlib.sha256(
            (ROOT / "release_matrix_0.3.2_0.3.3.json").read_bytes()
        ).hexdigest()
        self.assertEqual(actual, build_release.FROZEN_MATRIX_SHA256)
        self.assertEqual(
            self.matrix, build_release.load_frozen_matrix(build_release.MATRIX_PATH)
        )
        with tempfile.TemporaryDirectory(prefix="pagespec-fake-matrix-") as temporary:
            fake = Path(temporary) / "release_matrix_0.3.2_0.3.3.json"
            fake.write_text(
                json.dumps(
                    {
                        **self.matrix,
                        "browser_gates": {"legacy_major": 109, "current_major": 109},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError, "custom release matrices are forbidden"
            ):
                build_release.load_frozen_matrix(fake)

    def test_browser_report_gate_rejects_empty_or_mislabeled_results(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import build_release

        with tempfile.TemporaryDirectory(prefix="pagespec-browser-report-redteam-") as temporary:
            base = Path(temporary)
            html_dir = base / "html"
            output_dir = base / "out"
            html_dir.mkdir()
            output_dir.mkdir()
            for volume in range(1, 5):
                (html_dir / f"catalog_volume{volume:02d}.html").write_text(
                    "<!doctype html><title>x</title>", encoding="utf-8"
                )
            generation = html_dir / "generation_manifest.json"
            generation.write_text(
                json.dumps({"schema": "pagespec-catalog-generation/v2"}),
                encoding="utf-8",
            )
            generation_identity = {
                "bytes": generation.stat().st_size,
                "sha256": hashlib.sha256(generation.read_bytes()).hexdigest(),
                "schema": "pagespec-catalog-generation/v2",
            }
            fake = {
                "schema": "pagespec-catalog-browser-audit/v2",
                "payload_integrity_bound": True,
                "stable_window_required_ms": 10_800,
                "catalog_count": 172,
                "generationManifest": generation_identity,
                "total": 8,
                "passed": 8,
                "failures": [],
                "browser": {"product": "HeadlessChrome/149.0.0.0"},
                "results": [],
            }
            (output_dir / "audit_summary.json").write_text(json.dumps(fake))
            mutation_fake = {
                **fake,
                "schema": "pagespec-catalog-browser-mutations/v2",
            }
            (output_dir / "mutation_summary.json").write_text(
                json.dumps(mutation_fake)
            )
            with self.assertRaisesRegex(RuntimeError, "browser major 149 != 109"):
                build_release.verify_browser_reports(
                    html_dir=html_dir,
                    output_dir=output_dir,
                    variant={"version": "redteam"},
                    browser_label="109",
                    expected_major=109,
                )
            fake["browser"]["product"] = "HeadlessChrome/109.0.0.0"
            (output_dir / "audit_summary.json").write_text(json.dumps(fake))
            mutation_fake = {
                **fake,
                "schema": "pagespec-catalog-browser-mutations/v2",
            }
            (output_dir / "mutation_summary.json").write_text(
                json.dumps(mutation_fake)
            )
            with self.assertRaisesRegex(RuntimeError, "result set is incomplete"):
                build_release.verify_browser_reports(
                    html_dir=html_dir,
                    output_dir=output_dir,
                    variant={"version": "redteam"},
                    browser_label="109",
                    expected_major=109,
                )


class WorkflowReleaseContractTests(unittest.TestCase):
    def test_source_workflows_are_release_neutral_and_below_dify_scalar_limit(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import build_release

        matrix = json.loads(
            (ROOT / "release_matrix_0.3.2_0.3.3.json").read_text(encoding="utf-8")
        )
        report = build_release.verify_source_workflow_contract(matrix)
        self.assertEqual("PASS", report["status"])
        self.assertEqual(4, len(report["workflows"]))
        self.assertLess(
            max(row["max_scalar_chars"] for row in report["workflows"]),
            matrix["limits"]["workflow_string_chars_exclusive"],
        )

        with tempfile.TemporaryDirectory(prefix="pagespec-source-yml-redteam-") as temporary:
            base = Path(temporary)
            source_dir = base / "dev_sources"
            source_dir.mkdir()
            for volume in range(1, 5):
                code = "x" * 80_000 if volume == 3 else "def main():\n return {}\n"
                document = {
                    "workflow": {
                        "graph": {
                            "nodes": [{"data": {"type": "code", "code": code}}]
                        }
                    }
                }
                (source_dir / f"全库有意义测试_卷{volume:02d}.yml").write_text(
                    yaml.safe_dump(document, allow_unicode=True), encoding="utf-8"
                )
            with mock.patch.object(build_release, "ROOT", base):
                with self.assertRaisesRegex(RuntimeError, "source workflow scalar"):
                    build_release.verify_source_workflow_contract(matrix)

    def test_each_version_generates_four_small_exactly_bound_workflows(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from dev_sources import build_workflows

        with tempfile.TemporaryDirectory(prefix="pagespec-workflows-") as temporary:
            base = Path(temporary)
            for version, marker in (("0.3.2", "a"), ("0.3.3", "b")):
                output = base / version
                identifier = (
                    f"zhangyu/html_offline_exporter:{version}@{marker * 64}"
                )
                paths = [
                    build_workflows.build_one(
                        volume,
                        version=version,
                        plugin_identifier=identifier,
                        output_dir=output,
                    )
                    for volume in range(1, 5)
                ]
                self.assertEqual(4, len(paths))
                for volume, path in enumerate(paths, 1):
                    document = yaml.safe_load(path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        identifier,
                        document["dependencies"][0]["value"]["plugin_unique_identifier"],
                    )
                    self.assertIn(version, path.name)
                    self.assertIn(f"卷{volume:02d}", path.name)
                    tool_node = next(
                        node for node in document["workflow"]["graph"]["nodes"]
                        if node.get("data", {}).get("type") == "tool"
                    )["data"]
                    self.assertEqual(
                        {"spec", "filename"}, set(tool_node["tool_parameters"])
                    )
                    self.assertEqual(
                        {f"slot{index}" for index in range(1, 21)},
                        set(tool_node["tool_configurations"]),
                    )
                    strings = list(build_workflows._walk_strings(document))
                    self.assertLess(max(len(value) for _where, value in strings), 80_000)
        source = (ROOT / "dev_sources/build_workflows.py").read_text(encoding="utf-8")
        self.assertNotIn("PLUGIN_SHA256_PLACEHOLDER", source)

    def test_workflow_gate_rejects_invalid_dify_tool_envelopes(self) -> None:
        import copy
        import sys

        sys.path.insert(0, str(ROOT))
        from dev_sources import build_workflows
        from verification.scripts import check_workflows

        identifier = f"zhangyu/html_offline_exporter:0.3.2@{'a' * 64}"
        forms = build_workflows._tool_parameter_forms()
        with tempfile.TemporaryDirectory(prefix="pagespec-workflow-envelope-redteam-") as temporary:
            base = Path(temporary)
            canonical = build_workflows.build_one(
                1, version="0.3.2", plugin_identifier=identifier, output_dir=base
            )
            document = yaml.safe_load(canonical.read_text(encoding="utf-8"))
            tool = next(
                node for node in document["workflow"]["graph"]["nodes"]
                if node.get("data", {}).get("type") == "tool"
            )["data"]
            mutations = {
                "spec_type": lambda d: d["tool_parameters"]["spec"].update(type="invalid"),
                "filename_value": lambda d: d["tool_parameters"]["filename"].update(value={"not": "a string"}),
                "slot_type": lambda d: d["tool_configurations"]["slot1"].update(type="mixed"),
                "provider": lambda d: d.update(provider_id="attacker/provider/tool"),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    candidate_doc = copy.deepcopy(document)
                    candidate_tool = next(
                        node for node in candidate_doc["workflow"]["graph"]["nodes"]
                        if node.get("data", {}).get("type") == "tool"
                    )["data"]
                    mutate(candidate_tool)
                    candidate = base / f"mutated_{label}.yml"
                    candidate.write_text(
                        yaml.safe_dump(candidate_doc, allow_unicode=True, sort_keys=False),
                        encoding="utf-8",
                    )
                    with self.assertRaises(AssertionError):
                        check_workflows.inspect_workflow(
                            output_path=candidate,
                            volume=1,
                            expected_count=35,
                            version="0.3.2",
                            expected_identifier=identifier,
                            max_text=80_000,
                            parameter_forms=forms,
                        )


if __name__ == "__main__":
    unittest.main()
