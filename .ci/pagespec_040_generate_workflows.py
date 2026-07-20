#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

PLUGIN_ID = "zhangyu/html_offline_exporter"
MAX_DIFY_STRING = 80_000
OLD_IDENTIFIER = re.compile(r"zhangyu/html_offline_exporter:[0-9.]+@[0-9a-f]{64}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transform_identifiers(value: Any, identifier: str) -> Any:
    if isinstance(value, str):
        return OLD_IDENTIFIER.sub(identifier, value)
    if isinstance(value, list):
        return [transform_identifiers(item, identifier) for item in value]
    if isinstance(value, dict):
        return {key: transform_identifiers(item, identifier) for key, item in value.items()}
    return value


def walk_strings(value: Any, path: str = "$"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(item, f"{path}.{key}")


def update_dependencies(document: dict[str, Any], identifier: str) -> int:
    changed = 0
    for dependency in document.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        value = dependency.get("value")
        if not isinstance(value, dict):
            continue
        current = str(value.get("plugin_unique_identifier") or "")
        plugin_id = str(value.get("plugin_id") or "")
        if "html_offline_exporter" in current or plugin_id == PLUGIN_ID:
            value["plugin_unique_identifier"] = identifier
            changed += 1
    return changed


def update_tool_nodes(document: dict[str, Any]) -> int:
    count = 0
    nodes = document["workflow"]["graph"]["nodes"]
    for node in nodes:
        data = node.get("data") if isinstance(node, dict) else None
        if not isinstance(data, dict):
            continue
        if data.get("type") == "tool" and data.get("tool_name") == "render_page":
            configurations = data.setdefault("tool_configurations", {})
            configurations["include_all_libraries"] = {"type": "constant", "value": True}
            count += 1
    return count


def build(source: Path, output: Path, identifier: str, name: str, description: str) -> dict[str, Any]:
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"invalid YML document: {source}")
    document = transform_identifiers(document, identifier)
    document["app"]["name"] = name
    document["app"]["description"] = description
    dependencies = update_dependencies(document, identifier)
    tool_nodes = update_tool_nodes(document)
    if dependencies < 1:
        raise RuntimeError(f"PageSpec dependency not found in {source.name}")
    if tool_nodes < 1:
        raise RuntimeError(f"render_page node not found in {source.name}")
    oversized = [(path, len(text)) for path, text in walk_strings(document) if len(text) >= MAX_DIFY_STRING]
    if oversized:
        raise RuntimeError(f"Dify 80000-character limit reached: {oversized[:5]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    return {
        "source": source.name,
        "output": output.name,
        "dependencies": dependencies,
        "render_page_nodes": tool_nodes,
        "maximum_string": max((len(text) for _path, text in walk_strings(document)), default=0),
        "bytes": output.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--baseline-delivery", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    package_hash = sha256(args.package)
    identifier = f"{PLUGIN_ID}:0.4.0@{package_hash}"
    library = next(args.baseline_delivery.rglob("PageSpec_城市公共图书馆年度阅读与活动报告_样例_Dify1.7.1.yml"))
    phone = next(args.baseline_delivery.rglob("手机号一键查询并生成报告_PageSpec0.3.4_Dify1.7.1.yml"))

    results = [
        build(
            library,
            args.output_dir / "PageSpec_城市公共图书馆年度阅读与活动报告_全库版_0.4.0_Dify1.7.1.yml",
            identifier,
            "城市公共图书馆年度阅读与活动报告·172库全能力版",
            "结构化图书馆年度报告与四卷172库全能力展示位于同一离线HTML；一次只运行一卷。",
        ),
        build(
            phone,
            args.output_dir / "手机号一键查询并生成报告_PageSpec全库版_0.4.0_Dify1.7.1.yml",
            identifier,
            "手机号一键查询并生成报告·PageSpec 0.4.0全库版",
            "保留原业务节点、字段和两条报告分支，并在每份离线HTML中封装四卷172库能力展示。",
        ),
    ]
    import json
    (args.output_dir / "PageSpec_0.4.0_YML审计.json").write_text(
        json.dumps({"plugin_identifier": identifier, "workflows": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(identifier)
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
