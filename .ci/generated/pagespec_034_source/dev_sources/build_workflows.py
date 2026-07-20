#!/usr/bin/env python3
"""Generate four SHA-bound PageSpec catalog workflows for one release package.

This generator has no placeholder mode.  A workflow is emitted only after the
actual ``.difypkg`` has been opened, its manifest and dependency declaration
have been checked, and its SHA-256 has been calculated.  Consequently a YML
cannot accidentally refer to a preliminary package or a different version.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "dev_sources"
CATALOG_DIR = ROOT / "catalog"
EXPECTED_COUNTS = {1: 35, 2: 63, 3: 41, 4: 33}
REQUIREMENTS_EXACT = "dify_plugin>=0.9.0"
MAX_WORKFLOW_STRING = 80_000
PLUGIN_AUTHOR = "zhangyu"
PLUGIN_NAME = "html_offline_exporter"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _tool_parameter_forms(document: dict[str, Any] | None = None) -> dict[str, str]:
    """Derive Dify node storage from the Tool declaration, never by memory."""
    if document is None:
        document = yaml.safe_load((ROOT / "tools/render_page.yaml").read_text(encoding="utf-8"))
    parameters = document.get("parameters") if isinstance(document, dict) else None
    if not isinstance(parameters, list):
        raise ValueError("render_page.yaml parameters must be a list")
    forms: dict[str, str] = {}
    for parameter in parameters:
        if not isinstance(parameter, dict):
            raise ValueError("render_page.yaml parameter entry must be a mapping")
        name, form = parameter.get("name"), parameter.get("form")
        if not isinstance(name, str) or form not in {"llm", "form"} or name in forms:
            raise ValueError(f"invalid/duplicate Tool parameter declaration: {parameter!r}")
        forms[name] = form
    expected_llm = {"spec", "filename"}
    expected_form = {f"slot{index}" for index in range(1, 21)}
    if {name for name, form in forms.items() if form == "llm"} != expected_llm:
        raise ValueError("Tool llm-form parameters are not exactly spec/filename")
    if {name for name, form in forms.items() if form == "form"} != expected_form:
        raise ValueError("Tool form parameters are not exactly slot1..slot20")
    return forms


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_names(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("package contains duplicate member names")
    for name in names:
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or "\\" in name:
            raise ValueError(f"unsafe package member: {name!r}")
    if archive.testzip() is not None:
        raise ValueError("package CRC check failed")
    return names


def inspect_package(
    package: Path, expected_version: str, expected_minimum_dify: str
) -> dict[str, Any]:
    if package.suffix != ".difypkg" or not package.is_file():
        raise ValueError(f"not a readable .difypkg: {package}")
    with zipfile.ZipFile(package) as archive:
        names = _safe_member_names(archive)
        for required in ("manifest.yaml", "requirements.txt"):
            if required not in names:
                raise ValueError(f"package is missing {required}")
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        tool_definition = yaml.safe_load(archive.read("tools/render_page.yaml"))
        requirements = archive.read("requirements.txt").decode("utf-8").strip()
    if not isinstance(manifest, dict):
        raise ValueError("package manifest is not a mapping")
    if str(manifest.get("version")) != expected_version:
        raise ValueError(
            f"package version {manifest.get('version')!r} != {expected_version!r}"
        )
    meta = manifest.get("meta")
    if not isinstance(meta, dict) or str(meta.get("minimum_dify_version")) != expected_minimum_dify:
        raise ValueError("package minimum_dify_version does not match release variant")
    if manifest.get("author") != PLUGIN_AUTHOR or manifest.get("name") != PLUGIN_NAME:
        raise ValueError("package identity is not the frozen zhangyu/html_offline_exporter")
    if requirements != REQUIREMENTS_EXACT:
        raise ValueError(
            f"requirements.txt must be exactly {REQUIREMENTS_EXACT!r}; got {requirements!r}"
        )
    forms = _tool_parameter_forms(tool_definition)
    return {
        "path": str(package),
        "bytes": package.stat().st_size,
        "sha256": _sha256(package),
        "members": len(names),
        "version": expected_version,
        "minimum_dify_version": expected_minimum_dify,
        "parameter_forms": forms,
    }


def _source_path(volume: int) -> Path:
    return SOURCE_DIR / f"全库有意义测试_卷{volume:02d}.yml"


def _output_path(output_dir: Path, volume: int, version: str) -> Path:
    return output_dir / f"PageSpec全库有意义测试_卷{volume:02d}_插件{version}.yml"


def _node_by_type(document: dict[str, Any], node_type: str) -> dict[str, Any]:
    matches = [
        node
        for node in document["workflow"]["graph"]["nodes"]
        if node.get("data", {}).get("type") == node_type
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one {node_type!r} node, found {len(matches)}")
    return matches[0]


def _walk_strings(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{path}.{key}")


def _catalog_contract(volume: int) -> tuple[int, list[str]]:
    fixture = json.loads(
        (CATALOG_DIR / f"volume{volume:02d}.json").read_text(encoding="utf-8")
    )
    count = fixture.get("count")
    covers = fixture.get("covers")
    if count != EXPECTED_COUNTS[volume] or not isinstance(covers, list):
        raise ValueError(f"catalog volume {volume:02d} has unexpected metadata")
    if len(covers) != count or len(set(covers)) != count:
        raise ValueError(f"catalog volume {volume:02d} coverage is not one-to-one")
    return count, covers


def _pagespec_code(volume: int, version: str) -> str:
    title = f"全库有意义测试·卷{volume:02d}·PageSpec {version}"
    return (
        "import json\n\n"
        "def main() -> dict:\n"
        "    spec = {\n"
        "        \"version\": 1,\n"
        "        \"profile\": \"catalog-verification\",\n"
        f"        \"doc\": {{\"title\": {title!r}, \"lang\": \"zh-CN\"}},\n"
        f"        \"blocks\": [{{\"type\": \"catalog_demo\", \"volume\": {volume}}}],\n"
        "    }\n"
        "    return {\"html\": json.dumps(spec, ensure_ascii=False, "
        "separators=(\",\", \":\"))}\n"
    )


def _slot_configurations() -> dict[str, dict[str, Any]]:
    return {
        f"slot{index}": {"type": "constant", "value": None}
        for index in range(1, 21)
    }


def build_one(
    volume: int,
    *,
    version: str,
    plugin_identifier: str,
    output_dir: Path,
) -> Path:
    count, _covers = _catalog_contract(volume)
    document = yaml.safe_load(_source_path(volume).read_text(encoding="utf-8"))
    original_graph = copy.deepcopy(document["workflow"]["graph"])
    code_node = _node_by_type(document, "code")
    tool_node = _node_by_type(document, "tool")
    answer_node = _node_by_type(document, "answer")
    original_input_selector = tool_node["data"]["tool_configurations"]["html"]["value"]
    original_answer = copy.deepcopy(answer_node["data"]["answer"])

    document["app"].update(
        {
            "description": (
                f"PageSpec {version} 全库有意义验证：卷{volume:02d}覆盖 {count} 个库；"
                "四卷固定为 35/63/41/33，共 172 个库。输入只含 catalog_demo 块，"
                "库目录与验证逻辑由同版本插件内置。"
            ),
            "name": f"PageSpec {version} 全库有意义测试·卷{volume:02d}·{count}库",
        }
    )
    document["dependencies"][0]["value"]["plugin_unique_identifier"] = plugin_identifier
    document["workflow"]["features"]["opening_statement"] = (
        f"发送任意消息生成 {version} 卷{volume:02d}离线验证页。本卷 {count} 个库；"
        "下载后断网用 file:// 打开，并等待页面标题进入最终 PASS 或 FAIL。"
    )

    code_node["data"].update(
        {
            "code": _pagespec_code(volume, version),
            "desc": f"生成 catalog_demo 卷{volume:02d} PageSpec，固定覆盖 {count} 个库",
            "title": f"PageSpec {version} 输入·卷{volume:02d}",
            "outputs": {"html": {"children": None, "type": "string"}},
        }
    )
    tool_data = tool_node["data"]
    tool_data.update(
        {
            "desc": f"把卷{volume:02d} PageSpec 编译为自包含断网 HTML",
            "title": f"PageSpec {version} 生成离线HTML·卷{volume:02d}",
            "tool_description": "用封闭 PageSpec JSON 生成离线单文件 HTML",
            "tool_label": "用 JSON 生成离线 HTML",
            "tool_name": "render_page",
            "tool_node_version": "2",
            "tool_parameters": {
                "spec": {"type": "mixed", "value": original_input_selector},
                "filename": {
                    "type": "mixed",
                    "value": f"PageSpec_{version}_全库有意义测试_卷{volume:02d}.html",
                },
            },
            "params": {},
        }
    )
    tool_data["tool_configurations"] = _slot_configurations()
    forms = _tool_parameter_forms()
    if set(tool_data["tool_parameters"]) != {name for name, form in forms.items() if form == "llm"}:
        raise AssertionError("workflow tool_parameters do not match Tool form=llm declarations")
    if set(tool_data["tool_configurations"]) != {name for name, form in forms.items() if form == "form"}:
        raise AssertionError("workflow tool_configurations do not match Tool form=form declarations")
    if answer_node["data"]["answer"] != original_answer:
        raise AssertionError("answer selector changed unexpectedly")

    rewritten_graph = document["workflow"]["graph"]
    if rewritten_graph["edges"] != original_graph["edges"]:
        raise AssertionError(f"volume {volume:02d}: edges changed")
    for before, after in zip(original_graph["nodes"], rewritten_graph["nodes"]):
        for key in (
            "id", "position", "positionAbsolute", "type", "width", "height",
            "sourcePosition", "targetPosition",
        ):
            if before.get(key) != after.get(key):
                raise AssertionError(
                    f"volume {volume:02d}: node {before.get('id')} {key} changed"
                )
    if rewritten_graph.get("viewport") != original_graph.get("viewport"):
        raise AssertionError(f"volume {volume:02d}: viewport changed")

    oversized = [
        (path, len(value))
        for path, value in _walk_strings(document)
        if len(value) >= MAX_WORKFLOW_STRING
    ]
    if oversized:
        raise AssertionError(f"volume {volume:02d}: YML strings reach 80000: {oversized}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = _output_path(output_dir, volume, version)
    output_path.write_text(
        yaml.safe_dump(
            document,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--minimum-dify-version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not VERSION_RE.fullmatch(args.version):
        raise SystemExit(f"invalid semantic version: {args.version!r}")
    package = args.package.resolve()
    package_report = inspect_package(package, args.version, args.minimum_dify_version)
    plugin_identifier = (
        f"{PLUGIN_AUTHOR}/{PLUGIN_NAME}:{args.version}@{package_report['sha256']}"
    )
    outputs = [
        build_one(
            volume,
            version=args.version,
            plugin_identifier=plugin_identifier,
            output_dir=args.output_dir.resolve(),
        )
        for volume in EXPECTED_COUNTS
    ]
    print(
        json.dumps(
            {
                "schema": "pagespec-workflow-generation/v2",
                "generated": [str(path) for path in outputs],
                "counts": list(EXPECTED_COUNTS.values()),
                "total": sum(EXPECTED_COUNTS.values()),
                "plugin_identifier": plugin_identifier,
                "package": package_report,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
