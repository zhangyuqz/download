#!/usr/bin/env python3
"""Build trusted catalog fixtures from four version-neutral source workflows.

The input workflows are development evidence only.  Their HTML is authored by
the plugin project, never by an end user.  This build step removes every CDN
tag and emits a closed runtime fixture containing only:

* plugin-owned markup/style/test code;
* an ordered list of version-locked vendor catalogue keys; and
* the exact set of library ids covered by the fixed assertions.

Runtime code does not parse or transform user HTML.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dev_sources"
OUTPUT = ROOT / "catalog"
VENDOR_MAP = ROOT / "vendor" / "vendor_map.json"

SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.I | re.S)
LINK = re.compile(r"<link\b([^>]*)>", re.I | re.S)
STYLE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
ATTR = re.compile(r"([:\w-]+)\s*=\s*([\"'])(.*?)\2", re.I | re.S)


def attrs(raw: str) -> dict[str, str]:
    return {m.group(1).lower(): m.group(3) for m in ATTR.finditer(raw)}


def cdn_request(url: str) -> tuple[str, str | None, str] | None:
    parts = urlsplit("https:" + url if url.startswith("//") else url)
    if parts.query or parts.fragment:
        return None
    host = (parts.hostname or "").lower()
    seg = [unquote(x) for x in parts.path.split("/") if x]
    if host == "cdn.jsdelivr.net":
        if len(seg) < 2 or seg[0].lower() != "npm":
            return None
        seg = seg[1:]
    elif host == "unpkg.com":
        pass
    elif host == "cdnjs.cloudflare.com":
        if len(seg) < 4 or [x.lower() for x in seg[:2]] != ["ajax", "libs"]:
            return None
        return seg[2].lower(), seg[3], "/".join(seg[4:])
    else:
        return None
    if not seg:
        return None
    if seg[0].startswith("@"):
        if len(seg) < 2:
            return None
        scope, package_part = seg[0], seg[1]
        marker = package_part.rfind("@")
        name = package_part[:marker] if marker > 0 else package_part
        version = package_part[marker + 1:] if marker > 0 else None
        return f"{scope}/{name}".lower(), version, "/".join(seg[2:])
    package_part = seg[0]
    marker = package_part.rfind("@")
    package = package_part[:marker].lower() if marker > 0 else package_part.lower()
    version = package_part[marker + 1:] if marker > 0 else None
    return package, version, "/".join(seg[1:])


def resolve(url: str, kind: str, data: dict) -> str:
    request = cdn_request(url)
    if not request:
        raise ValueError(f"unsupported trusted fixture URL: {url}")
    package, version, path = request
    libs, aliases, versions = data["libs"], data.get("alias", {}), data.get("versions", {})
    default = package if package in libs else aliases.get(package)
    routes = versions.get(package) or versions.get(str(libs.get(default, {}).get("package", "")).lower()) or []
    if not routes and default in libs:
        routes = [{"key": default, "version": libs[default].get("version")}]
    found = []
    for route in routes:
        key = route.get("key")
        spec = libs.get(key) or {}
        if str(route.get("version")) != str(version):
            continue
        allowed = spec.get("js_paths" if kind == "js" else "css_paths", [])
        if path.lstrip("/") in [str(x).lstrip("/") for x in allowed]:
            found.append(key)
    if len(found) != 1:
        raise ValueError(f"trusted fixture URL did not resolve uniquely: {url} -> {found}")
    return found[0]


def code_from_workflow(path: Path) -> str:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    nodes = doc["workflow"]["graph"]["nodes"]
    code = next(n["data"]["code"] for n in nodes if n["data"].get("type") == "code")
    match = re.search(r"html\s*=\s*r'''(.*)'''", code, re.S)
    if not match:
        raise ValueError(f"HTML literal not found in {path.name}")
    return match.group(1)


def build_volume(path: Path, volume: int, data: dict) -> dict:
    html = code_from_workflow(path)
    head_match = re.search(r"<head>(.*?)</head>", html, re.I | re.S)
    body_match = re.search(r"<body>(.*?)</body>", html, re.I | re.S)
    if not head_match or not body_match:
        raise ValueError(f"malformed frozen fixture {path.name}")
    head, body = head_match.group(1), body_match.group(1)
    styles = STYLE.findall(head)
    head_without_styles = STYLE.sub("", head)
    assets: list[dict[str, str]] = []
    for match in LINK.finditer(head_without_styles):
        a = attrs(match.group(1))
        if "stylesheet" not in a.get("rel", "").lower().split():
            raise ValueError(f"unexpected head link in {path.name}: {match.group(0)}")
        assets.append({"kind": "css", "key": resolve(a["href"], "css", data)})
    head_without_styles = LINK.sub("", head_without_styles)
    prelude = []
    for match in SCRIPT.finditer(head_without_styles):
        a = attrs(match.group(1))
        if a.get("src"):
            raise ValueError(f"unexpected head script src in {path.name}")
        prelude.append(match.group(2).strip())
    residue = SCRIPT.sub("", head_without_styles)
    residue = re.sub(r"<(?:meta|title)\b[^>]*>.*?</title>|<meta\b[^>]*>", "", residue,
                     flags=re.I | re.S).strip()
    if residue:
        raise ValueError(f"unparsed trusted head content in {path.name}: {residue[:120]!r}")

    for match in LINK.finditer(body):
        a = attrs(match.group(1))
        if "stylesheet" not in a.get("rel", "").lower().split():
            raise ValueError(f"unexpected link in {path.name}: {match.group(0)}")
        assets.append({"kind": "css", "key": resolve(a["href"], "css", data)})
    body = LINK.sub("", body)

    runner: list[str] = []
    rebuilt: list[str] = []
    cursor = 0
    for match in SCRIPT.finditer(body):
        rebuilt.append(body[cursor:match.start()])
        a = attrs(match.group(1))
        src = a.get("src")
        code = match.group(2).strip()
        if src:
            key = resolve(src, "js", data)
            assets.append({"kind": "js", "key": key})
        elif len(code) < 400 and (
            "__LIB_HANDLES__" in code
            or re.fullmatch(
                r'L(?:\.[A-Za-z_$][\w$]*|\[["\'][^"\']+["\']\])='
                r'(?:[A-Za-z_$][\w$]*|window\[["\'][^"\']+["\']\]|null);',
                code,
            )
        ):
            # Runtime derives the handle from vendor_map.global immediately
            # after loading this exact ordered asset.
            pass
        else:
            runner.append(code)
        cursor = match.end()
    rebuilt.append(body[cursor:])
    fragment = "".join(rebuilt).strip()

    if re.search(r"<(?:script|link)\b", fragment, re.I):
        raise ValueError(f"executable/loadable tag remained in {path.name}")
    # Runner assertions intentionally contain inert URL strings for parsers
    # such as Autolinker/linkify.  Only markup/style/prelude can create a load
    # before the CSP/runtime guard starts, so gate those byte ranges here.
    if re.search(r"https?://", fragment + "\n" + "\n".join(styles + prelude), re.I):
        raise ValueError(f"network URL remained in trusted fixture {path.name}")
    if len(runner) != 1:
        raise ValueError(f"expected one suite runner in {path.name}, found {len(runner)}")

    case_keys = [m.group(2) for m in re.finditer(
        r"(?m)^\s*\{key:([\"'])([^\"']+)\1", runner[0]
    )]
    if not case_keys:
        raise ValueError(f"no catalog cases found in {path.name}")
    if len(case_keys) != len(set(case_keys)):
        raise ValueError(f"duplicate case key within {path.name}")

    asset_keys = [item["key"] for item in assets]
    # Every asserted library must either be explicitly loaded or be a declared
    # dependency (dayjs-antd is the one synthetic dependency-only case).
    closure = set(asset_keys)
    changed = True
    while changed:
        changed = False
        for key in tuple(closure):
            for dep in data["libs"][key].get("deps", []):
                if dep not in closure:
                    closure.add(dep)
                    changed = True
    missing = sorted(set(case_keys) - closure)
    if missing:
        raise ValueError(f"case libraries missing from asset closure in {path.name}: {missing}")

    return {
        "schema": "catalog-fixture/v1",
        "volume": volume,
        "source_workflow": path.name,
        "count": len(case_keys),
        "covers": case_keys,
        "assets": assets,
        "styles": styles,
        "prelude_js": prelude,
        "body_html": fragment,
        "runner_js": runner[0],
    }


def main() -> None:
    data = json.loads(VENDOR_MAP.read_text(encoding="utf-8"))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    registry = {"schema": "catalog-registry/v1", "catalog_count": len(data["libs"]), "volumes": []}
    covered: list[str] = []
    for volume in range(1, 5):
        path = SOURCE / f"全库有意义测试_卷0{volume}.yml"
        fixture = build_volume(path, volume, data)
        target = OUTPUT / f"volume{volume:02d}.json"
        target.write_text(json.dumps(fixture, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        registry["volumes"].append({
            "volume": volume,
            "file": target.name,
            "count": fixture["count"],
            "covers": fixture["covers"],
        })
        covered.extend(fixture["covers"])
    duplicates = sorted({x for x in covered if covered.count(x) > 1})
    missing = sorted(set(data["libs"]) - set(covered))
    unknown = sorted(set(covered) - set(data["libs"]))
    if duplicates or missing or unknown or len(covered) != len(data["libs"]):
        raise SystemExit(f"catalog coverage gate failed: duplicates={duplicates}, missing={missing}, unknown={unknown}")
    registry["covers"] = covered
    registry["coverage_sha256_input"] = "\n".join(sorted(covered)) + "\n"
    (OUTPUT / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"catalog": len(data["libs"]), "covered": len(covered),
                      "volumes": [v["count"] for v in registry["volumes"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
