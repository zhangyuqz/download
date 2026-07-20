#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage sanitized GitHub assets under canonical names, then run the full audit."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path


def load_v1(path: Path):
    spec = importlib.util.spec_from_file_location("pagespec_audit_v1", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    v1 = load_v1(Path(__file__).with_name("pagespec_040_post_release_audit.py"))
    source_assets = args.assets.resolve()
    files = [path for path in source_assets.iterdir() if path.is_file()]
    by_name = {path.name: path for path in files}
    aliases: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="pagespec-release-stage-") as temporary:
        stage = Path(temporary)
        for path in files:
            shutil.copy2(path, stage / path.name)

        def alias(canonical: str, source: Path) -> None:
            if canonical in by_name:
                return
            shutil.copy2(source, stage / canonical)
            aliases[canonical] = source.name

        ymls = sorted((path for path in files if path.suffix.lower() in {".yml", ".yaml"}), key=lambda p: p.stat().st_size)
        if len(ymls) == 2:
            alias(v1.LIB_YML, ymls[0])
            alias(v1.PHONE_YML, ymls[1])

        zips = sorted((path for path in files if path.suffix.lower() == ".zip"), key=lambda p: p.stat().st_size)
        if len(zips) == 2:
            alias(v1.SOURCE_ZIP, zips[0])
            alias(v1.DELIVERY_ZIP, zips[1])

        jsons = [path for path in files if path.suffix.lower() == ".json"]
        for path in jsons:
            if "YML" in path.name.upper():
                alias(v1.YML_AUDIT, path)
            else:
                alias(v1.SUMMARY, path)

        txts = [path for path in files if path.suffix.lower() == ".txt" and path.name != v1.SUMS]
        if len(txts) == 1:
            alias("PageSpec_0.4.0_完整交付包_SHA256.txt", txts[0])

        status = v1.audit(stage, args.baseline.resolve(), args.output.resolve())

    report = json.loads(args.output.read_text(encoding="utf-8"))
    filename_ok = not aliases
    report.setdefault("checks", {}).setdefault("release", []).append({
        "name": "GitHub Release preserves canonical filenames",
        "passed": filename_ok,
        "detail": aliases,
    })
    if not filename_ok:
        report.setdefault("errors", []).append(
            "release: GitHub Release canonical filenames were sanitized: " + json.dumps(aliases, ensure_ascii=False, sort_keys=True)
        )
    report["passed"] = not report.get("errors")
    report["summary"] = {
        "checks": sum(len(items) for items in report.get("checks", {}).values()),
        "failed": len(report.get("errors", [])),
        "warnings": len(report.get("warnings", [])),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.read_text(encoding="utf-8"))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
