#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent post-release audit for PageSpec 0.4.0 deliverables."""
from __future__ import annotations

import argparse
import base64
import copy
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

PLUGIN_SHA = "4690c97a1a1e739ee7ff2e04a1b4d4af76d23b324eac090d806f3e257c572e4a"
PLUGIN_ID = f"zhangyu/html_offline_exporter:0.4.0@{PLUGIN_SHA}"
PACKAGE_NAME = "html_offline_exporter_PageSpec_0.4.0_Dify1.7.1_SDK0.9plus.difypkg"
LIB_YML = "PageSpec_城市公共图书馆年度阅读与活动报告_全库版_0.4.0_Dify1.7.1.yml"
PHONE_YML = "手机号一键查询并生成报告_PageSpec全库版_0.4.0_Dify1.7.1.yml"
SOURCE_ZIP = "PageSpec_0.4.0_完整源码_含vendor.zip"
DELIVERY_ZIP = "PageSpec_0.4.0_完整交付包_推荐.zip"
SUMS = "PageSpec_0.4.0_SHA256SUMS.txt"
SUMMARY = "PageSpec_0.4.0_交付验证摘要.json"
YML_AUDIT = "PageSpec_0.4.0_YML审计.json"
EXPECTED_RELEASE_FILES = {
    PACKAGE_NAME, LIB_YML, PHONE_YML, SOURCE_ZIP, DELIVERY_ZIP, SUMS, SUMMARY, YML_AUDIT,
    "PageSpec_0.4.0_完整交付包_SHA256.txt",
}
EXPECTED_COUNTS = [35, 63, 41, 33]
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
PLUGIN_ID_RE = re.compile(r"zhangyu/html_offline_exporter:[0-9]+\.[0-9]+\.[0-9]+@[0-9a-f]{64}")


class UniqueLoader(yaml.SafeLoader):
    pass


def _construct_unique(loader: yaml.Loader, node: yaml.Node, deep: bool = False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_zip(path: Path) -> tuple[list[zipfile.ZipInfo], int]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ValueError(f"{path.name}: duplicate ZIP members")
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                raise ValueError(f"{path.name}: unsafe ZIP path {name!r}")
        bad = archive.testzip()
        if bad:
            raise ValueError(f"{path.name}: CRC failure at {bad}")
        unpacked = sum(item.file_size for item in infos if not item.is_dir())
    return infos, unpacked


def extract_zip(path: Path, destination: Path) -> None:
    safe_zip(path)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(destination)


def load_yaml(path: Path) -> Any:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)


def walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk_strings(item)


def normalize_identity(value: Any) -> Any:
    if isinstance(value, str):
        return PLUGIN_ID_RE.sub("zhangyu/html_offline_exporter:VERSION@SHA", value)
    if isinstance(value, list):
        return [normalize_identity(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_identity(item) for key, item in value.items()}
    return value


def normalize_workflow_for_compare(document: dict[str, Any]) -> dict[str, Any]:
    value = normalize_identity(copy.deepcopy(document))
    value["app"]["name"] = "APP_NAME"
    value["app"]["description"] = "APP_DESCRIPTION"
    for node in value["workflow"]["graph"]["nodes"]:
        data = node.get("data") or {}
        if data.get("type") == "tool" and data.get("tool_name") == "render_page":
            configs = data.get("tool_configurations") or {}
            configs.pop("include_all_libraries", None)
    return value


def locate_single(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise ValueError(f"expected one {name!r}, found {len(matches)}")
    return matches[0]


def add_result(report: dict[str, Any], section: str, name: str, passed: bool, detail: Any = None):
    item = {"name": name, "passed": bool(passed)}
    if detail is not None:
        item["detail"] = detail
    report.setdefault("checks", {}).setdefault(section, []).append(item)
    if not passed:
        report.setdefault("errors", []).append(f"{section}: {name}: {detail}")


def audit(assets: Path, baseline_zip: Path, output: Path) -> int:
    report: dict[str, Any] = {"version": "0.4.0", "checks": {}, "errors": [], "warnings": []}
    actual_names = {path.name for path in assets.iterdir() if path.is_file()}
    add_result(report, "release", "expected release assets", EXPECTED_RELEASE_FILES <= actual_names,
               {"missing": sorted(EXPECTED_RELEASE_FILES - actual_names), "extra": sorted(actual_names - EXPECTED_RELEASE_FILES)})

    required = {name: assets / name for name in EXPECTED_RELEASE_FILES}
    for name, path in required.items():
        add_result(report, "release", f"asset exists: {name}", path.is_file(), path.stat().st_size if path.is_file() else None)

    if report["errors"]:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    sums_text = required[SUMS].read_text(encoding="utf-8")
    declared: dict[str, str] = {}
    for line in sums_text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})\s+\*?(.+)", line.strip())
        if match:
            declared[Path(match.group(2)).name] = match.group(1)
    for name in (PACKAGE_NAME, LIB_YML, PHONE_YML, SOURCE_ZIP):
        actual = sha256(required[name])
        add_result(report, "hashes", name, declared.get(name) == actual,
                   {"declared": declared.get(name), "actual": actual})
    add_result(report, "hashes", "plugin SHA frozen", sha256(required[PACKAGE_NAME]) == PLUGIN_SHA,
               sha256(required[PACKAGE_NAME]))

    delivery_infos, delivery_unpacked = safe_zip(required[DELIVERY_ZIP])
    delivery_names = {Path(info.filename).name for info in delivery_infos if not info.is_dir()}
    expected_inside_delivery = {PACKAGE_NAME, LIB_YML, PHONE_YML, SOURCE_ZIP, SUMS, SUMMARY, YML_AUDIT}
    add_result(report, "archives", "delivery ZIP members", expected_inside_delivery <= delivery_names,
               {"missing": sorted(expected_inside_delivery - delivery_names), "count": len(delivery_infos), "unpacked": delivery_unpacked})

    package_infos, package_unpacked = safe_zip(required[PACKAGE_NAME])
    package_names = [info.filename for info in package_infos if not info.is_dir()]
    package_size = required[PACKAGE_NAME].stat().st_size
    add_result(report, "package", "compressed size < 15 MiB", package_size < 15_728_640,
               {"bytes": package_size, "margin": 15_728_640 - package_size})
    add_result(report, "package", "unpacked size < 50,000,000", package_unpacked < 50_000_000,
               {"bytes": package_unpacked, "margin": 50_000_000 - package_unpacked})
    forbidden = [name for name in package_names if any(part in name.lower() for part in (
        "/.git/", "/.venv/", "/venv/", "__pycache__", ".pyc", ".pyo", "/.idea/", "/.vscode/", ".env", ".log"
    ))]
    add_result(report, "package", "no forbidden package debris", not forbidden, forbidden[:30])

    with tempfile.TemporaryDirectory(prefix="pagespec-audit-") as temporary:
        temp = Path(temporary)
        package_root = temp / "package"
        source_root_container = temp / "source"
        baseline_root = temp / "baseline"
        extract_zip(required[PACKAGE_NAME], package_root)
        extract_zip(required[SOURCE_ZIP], source_root_container)
        extract_zip(baseline_zip, baseline_root)
        source_manifest = locate_single(source_root_container, "manifest.yaml")
        source_root = source_manifest.parent

        manifest = load_yaml(package_root / "manifest.yaml")
        provider = load_yaml(package_root / "provider/html_offline_exporter.yaml")
        tool = load_yaml(package_root / "tools/render_page.yaml")
        add_result(report, "package", "manifest identity",
                   manifest.get("version") == "0.4.0" and manifest.get("author") == "zhangyu" and manifest.get("name") == "html_offline_exporter",
                   {"version": manifest.get("version"), "author": manifest.get("author"), "name": manifest.get("name")})
        add_result(report, "package", "minimum Dify 1.7.1", str((manifest.get("meta") or {}).get("minimum_dify_version")) == "1.7.1",
                   (manifest.get("meta") or {}).get("minimum_dify_version"))
        add_result(report, "package", "requirements exact",
                   (package_root / "requirements.txt").read_text(encoding="utf-8").strip() == "dify_plugin>=0.9.0",
                   (package_root / "requirements.txt").read_text(encoding="utf-8").strip())
        provider_source = ((provider.get("extra") or {}).get("python") or {}).get("source")
        tool_source = ((tool.get("extra") or {}).get("python") or {}).get("source")
        add_result(report, "package", "provider Python source exists", bool(provider_source) and (package_root / provider_source).is_file(), provider_source)
        add_result(report, "package", "tool Python source exists", bool(tool_source) and (package_root / tool_source).is_file(), tool_source)
        readme = (package_root / "README.md").read_text(encoding="utf-8")
        add_result(report, "package", "root README CJK=0", not CJK_RE.search(readme), len(CJK_RE.findall(readme)))
        privacy = (package_root / "PRIVACY.md").read_text(encoding="utf-8").strip()
        add_result(report, "package", "PRIVACY non-empty", bool(privacy), len(privacy))
        add_result(report, "package", "Chinese README separated", (package_root / "readme/README_zh_Hans.md").is_file(), None)

        params = tool.get("parameters") or []
        forms = {item.get("name"): item.get("form") for item in params if isinstance(item, dict)}
        add_result(report, "package", "Tool parameter forms",
                   {name for name, form in forms.items() if form == "llm"} == {"spec", "filename"}
                   and {name for name, form in forms.items() if form == "form"} == ({f"slot{i}" for i in range(1, 21)} | {"include_all_libraries"}),
                   forms)

        registry = json.loads((package_root / "catalog/registry.json").read_text(encoding="utf-8"))
        counts = [item.get("count") for item in registry.get("volumes", [])]
        covers = registry.get("covers") or []
        add_result(report, "catalog", "registry 172 unique and 35/63/41/33",
                   len(covers) == 172 and len(set(covers)) == 172 and counts == EXPECTED_COUNTS,
                   {"count": len(covers), "unique": len(set(covers)), "volumes": counts})
        for volume, expected_count in enumerate(EXPECTED_COUNTS, 1):
            fixture = json.loads((package_root / f"catalog/volume{volume:02d}.json").read_text(encoding="utf-8"))
            declared_volume = next(item for item in registry["volumes"] if item["volume"] == volume)
            ok = fixture.get("count") == expected_count and fixture.get("covers") == declared_volume.get("covers")
            add_result(report, "catalog", f"volume {volume:02d} frozen coverage", ok,
                       {"fixture_count": fixture.get("count"), "declared_count": declared_volume.get("count")})

        source_debris = [path.relative_to(source_root).as_posix() for path in source_root.rglob("*") if path.is_file()
                         and ("__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"})]
        add_result(report, "source", "source ZIP has no Python cache", not source_debris, source_debris[:30])
        mismatches = []
        missing_in_source = []
        for name in package_names:
            package_path = package_root / name
            source_path = source_root / name
            if not source_path.is_file():
                missing_in_source.append(name)
            elif package_path.read_bytes() != source_path.read_bytes():
                mismatches.append(name)
        add_result(report, "source", "package bytes match source archive", not mismatches and not missing_in_source,
                   {"mismatches": mismatches[:30], "missing_in_source": missing_in_source[:30]})

        baseline_library = locate_single(baseline_root, "PageSpec_城市公共图书馆年度阅读与活动报告_样例_Dify1.7.1.yml")
        baseline_phone = locate_single(baseline_root, "手机号一键查询并生成报告_PageSpec0.3.4_Dify1.7.1.yml")
        for current_name, baseline_path, expected_nodes in (
            (LIB_YML, baseline_library, 1), (PHONE_YML, baseline_phone, 2),
        ):
            current_doc = load_yaml(required[current_name])
            baseline_doc = load_yaml(baseline_path)
            strings = list(walk_strings(current_doc))
            render_nodes = [node for node in current_doc["workflow"]["graph"]["nodes"]
                            if (node.get("data") or {}).get("type") == "tool" and (node.get("data") or {}).get("tool_name") == "render_page"]
            ids = [value for value in strings if PLUGIN_ID_RE.search(value)]
            configs_ok = all((((node["data"].get("tool_configurations") or {}).get("include_all_libraries") or {}).get("value") is True)
                             for node in render_nodes)
            add_result(report, "workflows", f"{current_name}: plugin identity", bool(ids) and all(PLUGIN_ID in value for value in ids), ids[:10])
            add_result(report, "workflows", f"{current_name}: render nodes and full-library switch",
                       len(render_nodes) == expected_nodes and configs_ok,
                       {"render_nodes": len(render_nodes), "switches": [((node["data"].get("tool_configurations") or {}).get("include_all_libraries")) for node in render_nodes]})
            add_result(report, "workflows", f"{current_name}: max string < 80000", max(map(len, strings), default=0) < 80_000,
                       max(map(len, strings), default=0))
            current_normalized = normalize_workflow_for_compare(current_doc)
            baseline_normalized = normalize_workflow_for_compare(baseline_doc)
            add_result(report, "workflows", f"{current_name}: no unintended workflow mutation",
                       current_normalized == baseline_normalized,
                       None if current_normalized == baseline_normalized else "normalized documents differ")

        sys.path.insert(0, str(package_root / "tools"))
        spec_module = importlib.util.spec_from_file_location("pagespec_transport_release", package_root / "tools/pagespec_transport.py")
        transport = importlib.util.module_from_spec(spec_module)
        assert spec_module and spec_module.loader
        sys.modules[spec_module.name] = transport
        spec_module.loader.exec_module(transport)
        canonical = {"version": 1, "blocks": [{"type": "text", "text": "容错回归"}]}
        canonical_text = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
        transport_cases = {
            "strict_json": canonical_text,
            "dify_output_wrapper": json.dumps({"output": canonical_text}, ensure_ascii=False),
            "markdown_fence": f"```json\n{canonical_text}\n```",
            "percent_encoded": urllib.parse.quote(canonical_text, safe=""),
            "base64": base64.b64encode(canonical_text.encode()).decode(),
            "urlsafe_base64": base64.urlsafe_b64encode(canonical_text.encode()).decode().rstrip("="),
            "gzip_base64": base64.b64encode(gzip.compress(canonical_text.encode(), mtime=0)).decode(),
            "zlib_base64": base64.b64encode(zlib.compress(canonical_text.encode())).decode(),
            "data_json_base64": "data:application/json;base64," + base64.b64encode(canonical_text.encode()).decode(),
        }
        for case_name, raw in transport_cases.items():
            outcome = transport.parse_spec(raw)
            passed = outcome.error is None and outcome.value == canonical
            add_result(report, "transport", case_name, passed,
                       {"error": outcome.error, "events": outcome.events[-4:] if outcome.events else []})

    report["passed"] = not report["errors"]
    report["summary"] = {
        "checks": sum(len(items) for items in report["checks"].values()),
        "failed": len(report["errors"]),
        "warnings": len(report["warnings"]),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    status = audit(args.assets.resolve(), args.baseline.resolve(), args.output.resolve())
    print(args.output.read_text(encoding="utf-8"))
    raise SystemExit(status)


if __name__ == "__main__":
    main()
