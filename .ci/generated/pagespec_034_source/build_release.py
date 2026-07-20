#!/usr/bin/env python3
"""Build the 0.3.2/0.3.3 dual release without manufacturing a difypkg.

Plugin archives are created exclusively by the official ``dify plugin
package`` command.  Python is used only for staging, independent inspection,
workflow generation, reports, source archives and the outer delivery ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parent
MATRIX_PATH = ROOT / "release_matrix_0.3.2_0.3.3.json"
FROZEN_MATRIX_SHA256 = "6c89cb36ea34b461436170c8ad2a92e66e49675dd2083d9284ef3c6eeb720542"
DEFAULT_CANDIDATE = ROOT / "release_candidate_0.3.2_0.3.3"
DEFAULT_RELEASE = ROOT / "release_0.3.2_0.3.3"
CJK_RE = re.compile(r"[\u3400-\u9fff]")
RUNTIME_ROOT_FILES = (
    ".difyignore", "PRIVACY.md", "README.md", "main.py", "manifest.yaml",
    "pagespec.schema.json", "requirements.txt",
)
RUNTIME_DIRS = ("_assets", "provider", "readme", "tools", "catalog", "vendor")
SOURCE_ROOT_FILES = (
    *RUNTIME_ROOT_FILES,
    "build_pagespec_schema.py", "build_release.py",
    "release_matrix_0.3.2_0.3.3.json",
)
SOURCE_DIRS = (*RUNTIME_DIRS, "dev_sources", "docs", "examples", "tests")
SOURCE_VERIFICATION_FILES = (
    "check_workflows.py",
    "verify_packaged_plugin.py",
    "sdk_smoke.py",
    "catalog_browser_audit.mjs",
    "audit_dify_template_contracts.py",
    "catalog_generate.py",
    "generate_browser_fixtures.py",
)
SOURCE_VERIFICATION_ROOT_FILES = (
    "pagespec_resource_redteam_0.3.2.json",
    "catalog_generate.py",
    "generate_browser_fixtures.py",
)
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_matrix(requested: Path) -> dict[str, Any]:
    """Load the one immutable contract accepted by this release builder."""
    if requested.resolve() != MATRIX_PATH.resolve():
        raise RuntimeError(
            "custom release matrices are forbidden; use the frozen "
            f"{MATRIX_PATH.name} contract"
        )
    actual_hash = sha256(MATRIX_PATH)
    if actual_hash != FROZEN_MATRIX_SHA256:
        raise RuntimeError(
            f"frozen release matrix SHA-256 mismatch: {actual_hash} != "
            f"{FROZEN_MATRIX_SHA256}"
        )
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _walk_yaml_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_yaml_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_yaml_strings(item, f"{path}.{key}")


def verify_source_workflow_contract(matrix: dict[str, Any]) -> dict[str, Any]:
    """Gate every development workflow that is shipped in the source archive."""
    paths = sorted((ROOT / "dev_sources").glob("全库有意义测试_卷*.yml"))
    if len(paths) != 4:
        raise RuntimeError(f"expected four source workflows, found {len(paths)}")
    limit = int(matrix["limits"]["workflow_string_chars_exclusive"])
    forbidden = (
        "0.0.8",
        "冻结修复版",
        "三重门禁修复版",
        "b1742452068f2e28517e6cc4aa5be9201c33b19959e4b282209a43fe00c68335",
    )
    rows: list[dict[str, Any]] = []
    for source in paths:
        text = source.read_text(encoding="utf-8")
        stale = [marker for marker in forbidden if marker in text]
        if stale:
            raise RuntimeError(f"{source.name}: stale release markers: {stale}")
        document = yaml.safe_load(text)
        strings = list(_walk_yaml_strings(document))
        if not strings:
            raise RuntimeError(f"{source.name}: source workflow has no strings")
        where, value = max(strings, key=lambda item: len(item[1]))
        if len(value) >= limit:
            raise RuntimeError(
                f"{source.name}: source workflow scalar {where} is {len(value)} "
                f"characters; limit is < {limit}"
            )
        code_nodes = [
            node.get("data", {}).get("code")
            for node in document.get("workflow", {}).get("graph", {}).get("nodes", [])
            if node.get("data", {}).get("type") == "code"
        ]
        if len(code_nodes) != 1 or not isinstance(code_nodes[0], str):
            raise RuntimeError(f"{source.name}: expected one Python code node")
        compile(code_nodes[0], source.name, "exec")
        rows.append(
            {
                "file": source.name,
                "max_scalar_chars": len(value),
                "max_scalar_path": where,
                "sha256": sha256(source),
            }
        )
    return {"status": "PASS", "limit_exclusive": limit, "workflows": rows}


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        copy_file(path, target / relative)


def deterministic_zip(source: Path, target: Path, prefix: str = "") -> None:
    """Create a deterministic non-difypkg archive for delivery materials."""
    if target.suffix == ".difypkg":
        raise ValueError("difypkg creation is reserved for the official Dify CLI")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        names: set[str] = set()
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            name = f"{prefix.rstrip('/')}/{relative}" if prefix else relative
            if name in names:
                raise RuntimeError(f"duplicate ZIP member: {name}")
            names.add(name)
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.create_system = 3
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise RuntimeError(f"CRC failure: {temporary}")
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise RuntimeError(f"duplicate member names: {temporary}")
    os.replace(temporary, target)


def run(
    command: list[str],
    *,
    cwd: Path,
    log: Path,
    env: dict[str, str | None] | None = None,
) -> str:
    merged = os.environ.copy()
    if env:
        for key, value in env.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
    result = subprocess.run(
        command,
        cwd=cwd,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}; see {log}"
        )
    return result.stdout


def resolve_executable(value: str, label: str) -> Path:
    """Resolve one toolchain executable before any stateful build action."""
    found = shutil.which(value)
    path = Path(found if found else value).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"{label} executable not found: {value}")
    return path


def resolve_python_environment(value: str, label: str) -> Path:
    """Resolve a Python command without dereferencing its virtualenv shim.

    CPython discovers ``pyvenv.cfg`` from the path used to start the
    interpreter.  Calling ``Path.resolve()`` on ``venv/bin/python`` silently
    turns it into the base interpreter and therefore drops the audited SDK
    environment.  Keep an absolute, normalized path while deliberately
    preserving the final symlink.
    """
    found = shutil.which(value)
    path = Path(os.path.abspath(os.path.expanduser(found if found else value)))
    if not path.is_file():
        raise RuntimeError(f"{label} executable not found: {value}")
    return path


def executable_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def deterministic_node_tree_identity(root: Path) -> dict[str, Any]:
    """Hash an installed ``node_modules`` tree including executable symlinks.

    ``pagespec-node-tree-v1`` records are sorted by POSIX relative path.  A
    regular-file record hashes its relative path, byte size and bytes.  A
    symlink record additionally hashes the literal link value, resolved
    in-tree target path, target size and target bytes.  Broken, escaping,
    directory-targeting links and all non-regular filesystem objects fail
    closed instead of being skipped.
    """
    root = root.resolve()
    if not root.is_dir():
        raise RuntimeError(f"node_modules tree not found: {root}")
    entries: list[Path] = []
    for current_text, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_text)
        retained_directories: list[str] = []
        for name in sorted(directory_names):
            path = current / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                entries.append(path)
            elif stat.S_ISDIR(mode):
                retained_directories.append(name)
            else:
                raise RuntimeError(f"node_modules contains non-directory entry: {path}")
        directory_names[:] = retained_directories
        for name in sorted(file_names):
            entries.append(current / name)

    digest = hashlib.sha256(b"pagespec-node-tree-v1\0")
    regular_files = 0
    symlinks = 0
    hashed_bytes = 0
    for path in sorted(entries, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            link_value = os.readlink(path)
            try:
                target = path.resolve(strict=True)
                target_relative = target.relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError(
                    f"node_modules symlink is broken or escapes the tree: {relative}"
                ) from exc
            target_mode = target.lstat().st_mode
            if not stat.S_ISREG(target_mode):
                raise RuntimeError(
                    f"node_modules symlink target is not a regular file: {relative}"
                )
            source = target
            prefix = (
                f"L\0{relative}\0{link_value}\0{target_relative}\0"
                f"{target.stat().st_size}\0"
            ).encode("utf-8", "surrogateescape")
            symlinks += 1
        elif stat.S_ISREG(mode):
            source = path
            prefix = f"F\0{relative}\0{path.stat().st_size}\0".encode(
                "utf-8", "surrogateescape"
            )
            regular_files += 1
        else:
            raise RuntimeError(f"node_modules contains non-regular file: {relative}")
        digest.update(prefix)
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                hashed_bytes += len(chunk)
        digest.update(b"\0")
    return {
        "schema": "pagespec-node-tree-v1",
        "sha256": digest.hexdigest(),
        "regular_files": regular_files,
        "symlinks": symlinks,
        "hashed_bytes": hashed_bytes,
    }


def validate_browser_runtime(
    value: Path,
    contract: dict[str, Any],
    label: str,
) -> tuple[Path, dict[str, Any]]:
    """Bind one browser audit to the exact frozen Node dependency tree.

    The lockfile hash prevents dependency resolution drift, while the installed
    package checks prevent a correct lockfile from being paired with a stale or
    substituted ``node_modules`` directory.
    """
    root = value.expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"{label} browser runtime root not found: {value}")
    lock_path = root / "package-lock.json"
    if not lock_path.is_file():
        raise RuntimeError(f"{label} browser runtime lacks package-lock.json")
    lock_sha256 = sha256(lock_path)
    expected_lock_sha256 = str(contract.get("package_lock_sha256") or "")
    if lock_sha256 != expected_lock_sha256:
        raise RuntimeError(
            f"{label} browser runtime package-lock SHA-256 mismatch: "
            f"{lock_sha256} != {expected_lock_sha256}"
        )
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} browser runtime has invalid package-lock.json") from exc
    if lock.get("lockfileVersion") != 3 or not isinstance(lock.get("packages"), dict):
        raise RuntimeError(f"{label} browser runtime requires npm lockfileVersion 3")

    expected_packages = {
        "puppeteer-core": str(contract.get("puppeteer_core") or ""),
        "@sparticuz/chromium": str(contract.get("sparticuz_chromium") or ""),
    }
    package_report: dict[str, Any] = {}
    for package_name, expected_version in expected_packages.items():
        if not expected_version:
            raise RuntimeError(f"{label} browser runtime matrix lacks {package_name}")
        relative = Path("node_modules").joinpath(*package_name.split("/"), "package.json")
        installed_path = root / relative
        if not installed_path.is_file():
            raise RuntimeError(
                f"{label} browser runtime lacks installed {package_name} package"
            )
        try:
            installed = json.loads(installed_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"{label} browser runtime has invalid {package_name}/package.json"
            ) from exc
        installed_version = installed.get("version")
        lock_entry = lock["packages"].get(f"node_modules/{package_name}") or {}
        locked_version = lock_entry.get("version")
        if installed_version != expected_version or locked_version != expected_version:
            raise RuntimeError(
                f"{label} browser runtime {package_name} version mismatch: "
                f"installed={installed_version!r}, locked={locked_version!r}, "
                f"expected={expected_version!r}"
            )
        if not isinstance(lock_entry.get("integrity"), str) or not lock_entry["integrity"]:
            raise RuntimeError(
                f"{label} browser runtime lock lacks {package_name} integrity"
            )
        package_report[package_name] = {
            "version": expected_version,
            "package_json_sha256": sha256(installed_path),
            "lock_integrity": lock_entry["integrity"],
        }
    node_tree = deterministic_node_tree_identity(root / "node_modules")
    expected_tree_sha256 = str(contract.get("node_modules_tree_sha256") or "")
    if node_tree["sha256"] != expected_tree_sha256:
        raise RuntimeError(
            f"{label} browser runtime node_modules tree SHA-256 mismatch: "
            f"{node_tree['sha256']} != {expected_tree_sha256}"
        )
    return root, {
        "root": str(root),
        "package_lock": {
            "path": str(lock_path),
            "bytes": lock_path.stat().st_size,
            "sha256": lock_sha256,
            "lockfile_version": lock["lockfileVersion"],
        },
        "node_modules": node_tree,
        "packages": package_report,
    }


def validate_chromium_executable(
    executable: Path,
    contract: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    identity = executable_identity(executable)
    expected_sha256 = str(contract.get("chromium_executable_sha256") or "")
    if identity["sha256"] != expected_sha256:
        raise RuntimeError(
            f"{label} executable SHA-256 mismatch: "
            f"{identity['sha256']} != {expected_sha256}"
        )
    support_lib = executable.parent / "lib"
    support_identity = deterministic_node_tree_identity(support_lib)
    expected_support_sha256 = str(contract.get("support_lib_tree_sha256") or "")
    if support_identity["sha256"] != expected_support_sha256:
        raise RuntimeError(
            f"{label} support lib tree SHA-256 mismatch: "
            f"{support_identity['sha256']} != {expected_support_sha256}"
        )
    return {**identity, "support_lib": support_identity}


def validate_cjk_test_font(font: Path, contract: dict[str, Any]) -> dict[str, Any]:
    resolved = font.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(f"CJK test font not found: {resolved}")
    identity = executable_identity(resolved)
    if (
        identity["bytes"] != int(contract.get("bytes") or -1)
        or identity["sha256"] != str(contract.get("sha256") or "")
        or not str(contract.get("family") or "").strip()
    ):
        raise RuntimeError("CJK test font bytes or release-matrix identity mismatch")
    return {
        **identity,
        "family": str(contract["family"]),
        "source": str(contract.get("source") or ""),
    }


def write_fontconfig(candidate: Path, font: Path) -> Path:
    config_dir = candidate / "reports" / "browser_environment"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = config_dir / "fonts.conf"
    font_dir = str(font.parent.resolve())
    if any(character in font_dir for character in "<>&"):
        raise RuntimeError("CJK test font directory is not safe for Fontconfig XML")
    config.write_text(
        "<?xml version=\"1.0\"?>\n"
        "<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">\n"
        "<fontconfig>\n"
        f"  <dir>{font_dir}</dir>\n"
        "  <dir>/usr/share/fonts</dir>\n"
        "  <cachedir>/tmp/pagespec-font-cache-2c76254f6fc3</cachedir>\n"
        "  <config></config>\n"
        "</fontconfig>\n",
        encoding="utf-8",
    )
    return config


def _variant_stage_name(variant: dict[str, Any]) -> str:
    return (
        f"html_offline_exporter_PageSpec_{variant['version']}_"
        f"{variant['label']}"
    )


def _variant_text(source: str, variant: dict[str, Any]) -> str:
    text = source.replace("0.3.2", variant["version"])
    text = text.replace("Dify 1.7.1", f"Dify {variant['minimum_dify_version']}")
    text = text.replace("Dify1.7.1", variant["label"])
    return text


def stage_variant(stage_root: Path, variant: dict[str, Any], matrix: dict[str, Any]) -> Path:
    stage = stage_root / _variant_stage_name(variant)
    stage.mkdir(parents=True, exist_ok=False)
    for name in RUNTIME_ROOT_FILES:
        copy_file(ROOT / name, stage / name)
    for name in RUNTIME_DIRS:
        copy_tree(ROOT / name, stage / name)

    manifest_path = stage / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = variant["version"]
    manifest.setdefault("meta", {})["minimum_dify_version"] = variant[
        "minimum_dify_version"
    ]
    manifest["meta"].setdefault("runner", {})["language"] = "python"
    manifest["meta"]["runner"]["version"] = matrix["python"]
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    (stage / "requirements.txt").write_text(
        matrix["requirements_exact"] + "\n", encoding="utf-8"
    )
    for name in ("README.md", "readme/README_zh_Hans.md"):
        path = stage / name
        path.write_text(
            _variant_text(path.read_text(encoding="utf-8"), variant),
            encoding="utf-8",
        )
    validate_stage(stage, variant, matrix)
    return stage


def _python_source(document: dict[str, Any], label: str) -> str:
    source = (
        document.get("extra", {}).get("python", {}).get("source")
        if isinstance(document, dict)
        else None
    )
    if not isinstance(source, str) or not source:
        raise RuntimeError(f"{label} lacks extra.python.source")
    return source


def validate_stage(stage: Path, variant: dict[str, Any], matrix: dict[str, Any]) -> None:
    manifest = yaml.safe_load((stage / "manifest.yaml").read_text(encoding="utf-8"))
    if manifest.get("author") != matrix["plugin"]["author"] or manifest.get("name") != matrix["plugin"]["name"]:
        raise RuntimeError("frozen plugin identity changed")
    if manifest.get("version") != variant["version"]:
        raise RuntimeError("staged manifest version mismatch")
    if manifest.get("meta", {}).get("minimum_dify_version") != variant["minimum_dify_version"]:
        raise RuntimeError("staged minimum Dify version mismatch")
    if manifest.get("meta", {}).get("runner", {}).get("version") != matrix["python"]:
        raise RuntimeError("staged Python runner mismatch")
    requirement = (stage / "requirements.txt").read_text(encoding="utf-8").strip()
    if requirement != matrix["requirements_exact"]:
        raise RuntimeError("staged requirements.txt is not the one allowed line")
    if CJK_RE.search((stage / "README.md").read_text(encoding="utf-8")):
        raise RuntimeError("root README.md contains CJK characters")
    if not CJK_RE.search((stage / "readme/README_zh_Hans.md").read_text(encoding="utf-8")):
        raise RuntimeError("Chinese README has no CJK text")
    if not (stage / "PRIVACY.md").read_text(encoding="utf-8").strip():
        raise RuntimeError("privacy policy is empty")
    provider = yaml.safe_load(
        (stage / "provider/html_offline_exporter.yaml").read_text(encoding="utf-8")
    )
    tool = yaml.safe_load((stage / "tools/render_page.yaml").read_text(encoding="utf-8"))
    for label, source in (
        ("provider", _python_source(provider, "provider YAML")),
        ("tool", _python_source(tool, "tool YAML")),
    ):
        if not (stage / source).is_file():
            raise RuntimeError(f"{label} Python source is not staged: {source}")

    vendor = json.loads((stage / "vendor/vendor_map.json").read_text(encoding="utf-8"))
    libraries = vendor.get("libs") or {}
    if len(libraries) != 172:
        raise RuntimeError(f"vendor map has {len(libraries)} libraries, not 172")
    for key, spec in libraries.items():
        files = ([spec["file"]] if spec.get("file") else []) + list(spec.get("css") or [])
        for filename in files:
            path = stage / "vendor" / filename
            if not path.is_file():
                raise RuntimeError(f"{key}: missing vendor file {filename}")
            if path.stat().st_size != spec.get("bytes", {}).get(filename):
                raise RuntimeError(f"{key}: byte count mismatch for {filename}")
            if sha256(path) != spec.get("sha256", {}).get(filename):
                raise RuntimeError(f"{key}: SHA mismatch for {filename}")
    registry = json.loads((stage / "catalog/registry.json").read_text(encoding="utf-8"))
    covers = registry.get("covers") or []
    counts = {
        int(item["volume"]): int(item["count"])
        for item in registry.get("volumes") or []
    }
    expected_counts = {int(key): int(value) for key, value in matrix["catalog_volumes"].items()}
    if counts != expected_counts or len(covers) != 172 or len(set(covers)) != 172:
        raise RuntimeError("catalog is not the frozen 35/63/41/33 one-to-one partition")
    if set(covers) != set(libraries):
        raise RuntimeError("catalog and vendor map library sets differ")


def package_with_official_cli(
    *,
    stage: Path,
    output: Path,
    dify_cli: str,
    log: Path,
) -> Path:
    """Run the official packager from the directory above the plugin."""
    expected_cli_output = stage.parent / f"{stage.name}.difypkg"
    if expected_cli_output.exists():
        expected_cli_output.unlink()
    run(
        [dify_cli, "plugin", "package", f"./{stage.name}"],
        cwd=stage.parent,
        log=log,
    )
    if not expected_cli_output.is_file():
        created = sorted(stage.parent.glob("*.difypkg"))
        raise RuntimeError(
            f"official CLI did not create {expected_cli_output.name}; created={created}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(expected_cli_output, output)
    return output


def inspect_and_extract_package(
    package: Path,
    extract_to: Path,
    variant: dict[str, Any],
    matrix: dict[str, Any],
) -> dict[str, Any]:
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(names) != len(set(names)):
            raise RuntimeError(f"{package.name}: duplicate members")
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                raise RuntimeError(f"{package.name}: unsafe member {name!r}")
        if archive.testzip() is not None:
            raise RuntimeError(f"{package.name}: CRC failure")
        package_limit = int(matrix["limits"]["difypkg_bytes_exclusive"])
        if package.stat().st_size >= package_limit:
            raise RuntimeError(f"{package.name}: compressed size is not < {package_limit}")
        uncompressed = sum(item.file_size for item in entries)
        raw_limit = int(matrix["limits"]["uncompressed_bytes_exclusive"])
        if uncompressed >= raw_limit:
            raise RuntimeError(f"{package.name}: uncompressed size is not < {raw_limit}")
        archive.extractall(extract_to)
    validate_stage(extract_to, variant, matrix)
    return {
        "filename": package.name,
        "bytes": package.stat().st_size,
        "sha256": sha256(package),
        "members": len(names),
        "uncompressed_bytes": uncompressed,
        "official_cli_only": True,
    }


def verify_browser_reports(
    *,
    html_dir: Path,
    output_dir: Path,
    variant: dict[str, Any],
    browser_label: str,
    expected_major: int | None,
) -> dict[str, Any]:
    audit_path = output_dir / "audit_summary.json"
    mutation_path = output_dir / "mutation_summary.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
    if audit.get("schema") != "pagespec-catalog-browser-audit/v2":
        raise RuntimeError(f"{variant['version']} {browser_label}: wrong audit schema")
    if mutation.get("schema") != "pagespec-catalog-browser-mutations/v2":
        raise RuntimeError(f"{variant['version']} {browser_label}: wrong mutation schema")
    if (
        audit.get("payload_integrity_bound") is not True
        or mutation.get("payload_integrity_bound") is not True
        or audit.get("stable_window_required_ms") != 10_800
        or mutation.get("stable_window_required_ms") != 10_800
        or audit.get("catalog_count") != 172
        or mutation.get("catalog_count") != 172
    ):
        raise RuntimeError(
            f"{variant['version']} {browser_label}: frozen payload/stability contract missing"
        )
    generation_path = html_dir / "generation_manifest.json"
    if not generation_path.is_file():
        raise RuntimeError(
            f"{variant['version']} {browser_label}: generation manifest missing"
        )
    generation_identity = {
        "bytes": generation_path.stat().st_size,
        "sha256": sha256(generation_path),
        "schema": "pagespec-catalog-generation/v2",
    }
    if (
        audit.get("generationManifest") != generation_identity
        or mutation.get("generationManifest") != generation_identity
    ):
        raise RuntimeError(
            f"{variant['version']} {browser_label}: browser reports are not bound "
            "to the exact generation manifest"
        )
    if audit.get("total") != 8 or audit.get("passed") != 8 or audit.get("failures"):
        raise RuntimeError(
            f"{variant['version']} {browser_label}: desktop/mobile audit is not 8/8"
        )
    if mutation.get("total") != 8 or mutation.get("passed") != 8 or mutation.get("failures"):
        raise RuntimeError(
            f"{variant['version']} {browser_label}: mutation audit is not 8/8"
        )
    product = str((audit.get("browser") or {}).get("product") or "")
    mutation_product = str((mutation.get("browser") or {}).get("product") or "")
    major_match = re.search(r"/(\d+)(?:\.|$)", product)
    mutation_major_match = re.search(r"/(\d+)(?:\.|$)", mutation_product)
    if not major_match or not mutation_major_match:
        raise RuntimeError(f"{variant['version']} {browser_label}: missing browser product version")
    major = int(major_match.group(1))
    mutation_major = int(mutation_major_match.group(1))
    if major != mutation_major:
        raise RuntimeError(f"{variant['version']} {browser_label}: audit/mutation browser mismatch")
    if expected_major is not None and major != expected_major:
        raise RuntimeError(
            f"{variant['version']} {browser_label}: browser major {major} != {expected_major}"
        )
    if expected_major is None and major <= 109:
        raise RuntimeError(
            f"{variant['version']} {browser_label}: current-browser gate reused legacy major {major}"
        )
    frozen = {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in html_dir.glob("catalog_volume*.html")
    }
    if len(frozen) != 4:
        raise RuntimeError(f"{variant['version']}: browser input is not exactly four HTML files")
    base_expected = {
        (
            f"catalog_volume{volume:02d}.html",
            viewport,
            "",
            viewport == "mobile",
            390 if viewport == "mobile" else 1600,
            844 if viewport == "mobile" else 1000,
        )
        for volume in range(1, 5)
        for viewport in ("desktop", "mobile")
    }
    mutation_expected = {
        *( (f"catalog_volume{volume:02d}.html", "desktop", "#late-error-10500",
             False, 1600, 1000)
           for volume in range(1, 5) ),
        *( (f"catalog_volume{volume:02d}.html", "desktop", fragment,
             False, 1600, 1000)
           for volume in (1, 2)
           for fragment in ("#clear-first-canvas-probe", "#clear-first-svg-probe") ),
    }
    actual_base = {
        (
            row.get("file"),
            (row.get("viewport") or {}).get("name"),
            row.get("fragment"),
            (row.get("viewport") or {}).get("mobile"),
            (row.get("viewport") or {}).get("width"),
            (row.get("viewport") or {}).get("height"),
        )
        for row in (audit.get("results") or [])
    }
    actual_mutations = {
        (
            row.get("file"),
            (row.get("viewport") or {}).get("name"),
            row.get("fragment"),
            (row.get("viewport") or {}).get("mobile"),
            (row.get("viewport") or {}).get("width"),
            (row.get("viewport") or {}).get("height"),
        )
        for row in (mutation.get("results") or [])
    }
    if len(audit.get("results") or []) != 8 or actual_base != base_expected:
        raise RuntimeError(
            f"{variant['version']} {browser_label}: base result set is incomplete or duplicated"
        )
    expected_gate_names = {
        "ready", "exact_count", "exact_keys_once", "independent_checks",
        "meaningful_artifacts", "all_expected_library_handles_live",
        "self_pass_not_trusted", "page_stability_contract_10800ms",
        "externally_observed_after_done_10800ms", "no_external_request",
        "no_failed_request", "no_runtime_exception", "no_console_error_or_warning",
        "csp_single_and_closed", "executable_script_nonce", "no_error_card",
        "no_horizontal_overflow", "starts_at_origin", "shell_in_viewport",
        "requested_device_mode", "cjk_test_font_available",
        "page_self_gate_also_clean",
        "screenshot_has_real_visible_content",
    }
    if any(
        row.get("failures") != []
        or set((row.get("gates") or {}).keys()) != expected_gate_names
        or not all(value is True for value in row["gates"].values())
        or int(row.get("externalObservationMs") or 0) < 10_800
        or float(row.get("navigationToDoneMs") or 0) <= 0
        or row.get("externalRequests") != []
        or row.get("failedRequests") != []
        or row.get("exceptions") != []
        or (row.get("state") or {}).get("externalResources") != []
        or (row.get("screenshotEvidence") or {}).get("pass") is not True
        or int((row.get("screenshotEvidence") or {}).get("naturalWidth") or 0)
        < int((row.get("viewport") or {}).get("width") or 0)
        or int((row.get("screenshotEvidence") or {}).get("naturalHeight") or 0)
        < int((row.get("viewport") or {}).get("height") or 0)
        or int((row.get("screenshotEvidence") or {}).get("samples") or 0) <= 0
        or int((row.get("screenshotEvidence") or {}).get("quantizedColors") or 0) < 12
        or int((row.get("screenshotEvidence") or {}).get("topQuantizedColors") or 0) < 6
        or float((row.get("screenshotEvidence") or {}).get("luminanceVariance") or 0) < 40
        or float((row.get("screenshotEvidence") or {}).get("opaqueRatio") or 0) <= .99
        or float((row.get("screenshotEvidence") or {}).get("nonWhiteRatio") or 0) <= .05
        or int(row.get("screenshotBytes") or 0) <= 1_000
        or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("screenshotSha256") or ""))
        for row in (audit.get("results") or [])
    ):
        raise RuntimeError(
            f"{variant['version']} {browser_label}: at least one base row did not pass every gate"
        )
    if len(mutation.get("results") or []) != 8 or actual_mutations != mutation_expected:
        raise RuntimeError(
            f"{variant['version']} {browser_label}: mutation result set is incomplete or duplicated"
        )
    if any(
        row.get("externallyRejected") is not True
        or row.get("expectedMutationDetected") is not True
        or row.get("forgedSelfReport") is not True
        or int(row.get("externalObservationMs") or 0) < 10_800
        or not isinstance(row.get("expectedFailure"), str)
        or not row["expectedFailure"]
        or not isinstance(row.get("detectedReason"), str)
        or not row["detectedReason"]
        or row.get("externalRequests") != []
        for row in (mutation.get("results") or [])
    ):
        raise RuntimeError(
            f"{variant['version']} {browser_label}: at least one mutation was not externally rejected"
        )
    for row in mutation.get("results") or []:
        fragment = row["fragment"]
        volume_match = re.fullmatch(r"catalog_volume(\d{2})\.html", row["file"])
        if not volume_match:
            raise RuntimeError(
                f"{variant['version']} {browser_label}: malformed mutation filename"
            )
        volume = int(volume_match.group(1))
        applied = row.get("mutationApplied") or {}
        probe = row.get("targetProbe") or {}
        if fragment == "#late-error-10500":
            exact_text = (
                "late-error-probe 10500ms" if volume <= 2 else "late-probe-10500"
            )
            if (
                row.get("expectedFailure") != f"exact {exact_text} exception"
                or row.get("detectedReason") != f"late_error_observed:{exact_text}"
                or applied != {"kind": "late-error", "applied": True, "rowKey": None}
                or len(row.get("exceptions") or []) != 1
                or exact_text not in str(row["exceptions"][0])
            ):
                raise RuntimeError(
                    f"{variant['version']} {browser_label}: late-error mutation "
                    f"did not prove the exact volume-{volume} target"
                )
            continue
        row_key = applied.get("rowKey")
        artifact = next(
            (
                item for item in ((row.get("state") or {}).get("artifacts") or [])
                if item.get("key") == row_key
            ),
            None,
        )
        if (
            not isinstance(row_key, str)
            or not row_key
            or applied.get("applied") is not True
            or artifact is None
            or artifact.get("meaningfulCount") != 0
            or row.get("exceptions") != []
        ):
            raise RuntimeError(
                f"{variant['version']} {browser_label}: graphic mutation target is not proven"
            )
        if fragment == "#clear-first-canvas-probe":
            valid = (
                applied.get("kind") == "canvas"
                and probe.get("canvasPresent") is True
                and probe.get("canvasPainted") is False
                and int(probe.get("canvasColors") or 0) <= 1
                and row.get("expectedFailure")
                == "externally replaced first target canvas is blank and targeted artifact fails"
                and row.get("detectedReason")
                == f"blank_canvas_target_rejected:{row_key}"
            )
        elif fragment == "#clear-first-svg-probe":
            valid = (
                applied.get("kind") == "svg"
                and probe.get("svgPresent") is True
                and probe.get("svgGeometry") == 0
                and row.get("expectedFailure")
                == "externally emptied first target SVG has no geometry and targeted artifact fails"
                and row.get("detectedReason")
                == f"empty_svg_target_rejected:{row_key}"
            )
        else:
            valid = False
        if not valid:
            raise RuntimeError(
                f"{variant['version']} {browser_label}: mutation evidence is not exact"
            )
    fixture_hashes = {
        f"catalog_volume{volume:02d}.html": matrix_hash
        for volume, matrix_hash in enumerate(
            (
                "80b44901ce0a8d5bff1b433fc3ee4ef86d60390a03e7506300fd2f1f76d038b0",
                "025391598b3151f12649150ae949fb21efbc975bd170911a42ec275a2c2d3fff",
                "c889d3ddd9b6c39fa0062d5e1d37df70b70672add463b0aad52f7f4683a5665b",
                "6a2da3977907f88e388cf767cb08057c15e94cde373a95dbc659a80766d75804",
            ),
            1,
        )
    }
    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    generation_pages = generation.get("pages") or []
    registration = generation.get("real_registration") or {}
    registration_identity = registration.get("registration_identity") or {}
    plugin_identity = registration.get("plugin_identity") or {}
    if (
        generation.get("schema") != "pagespec-catalog-generation/v2"
        or generation.get("page_count") != 4
        or generation.get("catalog_count") != 172
        or generation.get("coverage_unique") is not True
        or generation.get("failures") != []
        or len(generation_pages) != 4
        or registration.get("plugin_constructor_checked") is not True
        or registration.get("both_registered_classes_normally_constructed_and_invoked")
        is not True
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(registration_identity.get("source_sha256") or "")
        )
        or registration_identity.get("source_sha256")
        != plugin_identity.get("source_sha256")
    ):
        raise RuntimeError(
            f"{variant['version']} {browser_label}: generation manifest is not release-clean"
        )
    for volume in range(1, 5):
        filename = f"catalog_volume{volume:02d}.html"
        page = next(
            (item for item in generation_pages if item.get("filename") == filename),
            None,
        )
        html_identity = frozen.get(filename)
        invocations = (page or {}).get("invocations") or []
        if (
            page is None
            or page.get("volume") != volume
            or page.get("pass") is not True
            or page.get("bytes") != html_identity["bytes"]
            or page.get("sha256") != html_identity["sha256"]
            or len(invocations) != 2
            or {item.get("origin") for item in invocations}
            != {"PluginRegistration", "Plugin"}
            or any(
                item.get("pass") is not True
                or item.get("library_count") != page.get("library_count")
                or item.get("message_types_valid") is not True
                or item.get("summary_found") is not True
                or item.get("blob_filename") != filename
                or item.get("static_html_audit") != []
                or (item.get("payload_integrity") or {}).get("pass") is not True
                or (item.get("payload_integrity") or {}).get("fixture_sha256")
                != fixture_hashes[filename]
                for item in invocations
            )
        ):
            raise RuntimeError(
                f"{variant['version']} {browser_label}: {filename} lacks exact "
                "two-path payload-integrity evidence"
            )
    for report_name, rows in (
        ("audit", audit.get("results") or []),
        ("mutation", mutation.get("results") or []),
    ):
        for row in rows:
            expected = frozen.get(row.get("file"))
            if (
                expected is None
                or row.get("inputBytes") != expected["bytes"]
                or row.get("inputSha256") != expected["sha256"]
                or row.get("fixtureSha256") != fixture_hashes.get(row.get("file"))
            ):
                raise RuntimeError(
                    f"{variant['version']} {browser_label}: stale {report_name} input"
                )
            if report_name == "audit":
                viewport_name = (row.get("viewport") or {}).get("name")
                volume_match = re.fullmatch(r"catalog_volume(\d{2})\.html", row.get("file") or "")
                screenshot = output_dir / (
                    f"volume{volume_match.group(1)}_{viewport_name}_top.png"
                    if volume_match else "missing.png"
                )
                if (
                    not screenshot.is_file()
                    or screenshot.stat().st_size != row.get("screenshotBytes")
                    or sha256(screenshot) != row.get("screenshotSha256")
                ):
                    raise RuntimeError(
                        f"{variant['version']} {browser_label}: screenshot evidence is stale"
                    )
    return {
        "browser_label": browser_label,
        "browser_major": major,
        "audit": {
            "report": audit_path.name,
            "sha256": sha256(audit_path),
            "passed": audit["passed"],
            "total": audit["total"],
            "browser": audit.get("browser"),
        },
        "mutations": {
            "report": mutation_path.name,
            "sha256": sha256(mutation_path),
            "passed": mutation["passed"],
            "total": mutation["total"],
        },
        "generation_manifest": generation_identity,
        "inputs_bound_by_sha256": True,
    }


def stage_source_snapshot(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=False)
    for name in SOURCE_ROOT_FILES:
        copy_file(ROOT / name, target / name)
    for name in SOURCE_DIRS:
        copy_tree(ROOT / name, target / name)
    for name in SOURCE_VERIFICATION_FILES:
        copy_file(
            ROOT / "verification" / "scripts" / name,
            target / "verification" / "scripts" / name,
        )
    for name in SOURCE_VERIFICATION_ROOT_FILES:
        copy_file(
            ROOT / "verification" / name,
            target / "verification" / name,
        )
    return target


def write_sums(paths: Iterable[Path], target: Path, base: Path) -> None:
    rows = [f"{sha256(path)}  {path.relative_to(base).as_posix()}\n" for path in sorted(paths)]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(rows), encoding="utf-8")


def assert_disposable_candidate(candidate: Path) -> None:
    """Refuse broad or ambiguous targets before clearing a previous candidate."""
    if "candidate" not in candidate.name.lower():
        raise RuntimeError("candidate directory name must contain 'candidate'")
    if candidate == ROOT or candidate in ROOT.parents:
        raise RuntimeError(f"refusing broad candidate directory: {candidate}")
    if len(candidate.parts) < 4:
        raise RuntimeError(f"candidate path is too broad: {candidate}")


def build(args: argparse.Namespace) -> dict[str, Any]:
    matrix = load_frozen_matrix(args.matrix)
    source_workflow_gate = verify_source_workflow_contract(matrix)
    candidate = args.candidate.resolve()
    assert_disposable_candidate(candidate)
    if candidate.exists():
        shutil.rmtree(candidate)
    stage_root = candidate / "_official_stage"
    stage_root.mkdir(parents=True)
    stages = [stage_variant(stage_root, variant, matrix) for variant in matrix["variants"]]
    prepare_report = {
        "schema": "pagespec-dual-release-prepare/v1",
        "status": "PASS",
        "variants": [
            {
                "key": variant["key"],
                "version": variant["version"],
                "minimum_dify_version": variant["minimum_dify_version"],
                "stage": str(stage),
            }
            for variant, stage in zip(matrix["variants"], stages)
        ],
        "requirements_exact": matrix["requirements_exact"],
        "catalog_volumes": matrix["catalog_volumes"],
        "source_workflow_gate": source_workflow_gate,
        "difypkg_created": False,
    }
    (candidate / "PREPARE_REPORT.json").write_text(
        json.dumps(prepare_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.prepare_only:
        return prepare_report
    if not args.sdk_python or not args.sdk_wheel:
        raise RuntimeError(
            "--sdk-python and --sdk-wheel are required for a releasable build"
        )
    if (
        not args.chromium_109
        or not args.chromium_current
        or not args.browser_runtime_109
        or not args.browser_runtime_current
        or not args.cjk_font
    ):
        raise RuntimeError(
            "--chromium-109, --chromium-current, --browser-runtime-109 and "
            "--browser-runtime-current and --cjk-font are all required for a "
            "releasable build"
        )

    logs = candidate / "reports" / "logs"
    dify_cli = resolve_executable(args.dify_cli, "Dify CLI")
    sdk_python = resolve_python_environment(args.sdk_python, "SDK Python")
    sdk_wheel = args.sdk_wheel.expanduser().resolve()
    if not sdk_wheel.is_file():
        raise RuntimeError(f"SDK wheel not found: {sdk_wheel}")
    if sha256(sdk_wheel) != matrix["sdk_gate_wheel_sha256"]:
        raise RuntimeError("SDK wheel SHA-256 is not the audited 0.9.1 PyPI wheel")
    chromium_109 = resolve_executable(args.chromium_109, "Chromium 109")
    chromium_current = resolve_executable(args.chromium_current, "current Chromium")
    chromium_109_identity = validate_chromium_executable(
        chromium_109,
        matrix["browser_gates"]["runtime_109"],
        "Chromium 109",
    )
    chromium_current_identity = validate_chromium_executable(
        chromium_current,
        matrix["browser_gates"]["runtime_current"],
        "current Chromium",
    )
    cjk_font = args.cjk_font.expanduser().resolve()
    cjk_font_identity = validate_cjk_test_font(
        cjk_font,
        matrix["browser_gates"]["cjk_test_font"],
    )
    fontconfig_file = write_fontconfig(candidate, cjk_font)
    browser_runtime_109, browser_runtime_109_report = validate_browser_runtime(
        args.browser_runtime_109,
        matrix["browser_gates"]["runtime_109"],
        "Chromium 109",
    )
    browser_runtime_current, browser_runtime_current_report = validate_browser_runtime(
        args.browser_runtime_current,
        matrix["browser_gates"]["runtime_current"],
        "current Chromium",
    )
    node = resolve_executable("node", "Node.js")
    dify_version = run(
        [str(dify_cli), "version"],
        cwd=ROOT,
        log=logs / "dify_cli_version.log",
    ).strip()
    expected_cli = matrix["official_dify_cli"]
    if dify_version != expected_cli["version"]:
        raise RuntimeError(
            f"Dify CLI version {dify_version!r} != audited official {expected_cli['version']!r}"
        )
    if sha256(dify_cli) != expected_cli["sha256"]:
        raise RuntimeError("Dify CLI binary SHA-256 is not the audited official release binary")
    node_version = run(
        [str(node), "--version"],
        cwd=ROOT,
        log=logs / "node_version.log",
    ).strip()
    toolchain = {
        "dify_cli": {**executable_identity(dify_cli), "version": dify_version},
        "sdk_python": executable_identity(sdk_python),
        "sdk_wheel": executable_identity(sdk_wheel),
        "chromium_109": chromium_109_identity,
        "chromium_current": chromium_current_identity,
        "browser_runtime_109": browser_runtime_109_report,
        "browser_runtime_current": browser_runtime_current_report,
        "cjk_test_font": cjk_font_identity,
        "fontconfig": executable_identity(fontconfig_file),
        "node": {**executable_identity(node), "version": node_version},
    }
    run(
        [sys.executable, "-m", "py_compile", *map(str, sorted(ROOT.glob("tools/*.py")))],
        cwd=ROOT,
        log=logs / "source_py_compile.log",
    )
    run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        log=logs / "source_unittest.log",
        env={"PYTHONPATH": str(ROOT / "tools")},
    )
    # The screenshot supplied by the user is only one UI representation.  The
    # release gate traces every stable Dify Template-node implementation from
    # the official Dify/Graphon Git tags, then executes the local tolerant
    # decoder against the direct runtime value, result-panel envelope and API
    # envelopes.  This prevents an unverified claim of "all Dify versions".
    template_contract_path = candidate / "reports" / "dify_template_contracts_0.6.0_to_1.16.0.json"
    template_contract_output = run(
        [
            sys.executable,
            str(ROOT / "verification/scripts/audit_dify_template_contracts.py"),
            "--dify-repo", str(args.dify_repo.resolve()),
            "--graphon-repo", str(args.graphon_repo.resolve()),
            "--plugin-root", str(ROOT),
            "--pretty",
        ],
        cwd=ROOT,
        log=template_contract_path,
    )
    template_contract = json.loads(template_contract_output)
    if (
        template_contract.get("status") != "PASS"
        or template_contract.get("scope", {}).get("stable_tags_verified") != 99
        or not template_contract.get("conclusion", {})
        .get("template_runtime_contract", {})
        .get("uniform_across_all_verified_stable_tags")
    ):
        raise RuntimeError("Dify Template-node historical contract gate did not pass")
    template_contract_gate = {
        "report": str(template_contract_path.relative_to(candidate)),
        "report_sha256": sha256(template_contract_path),
        "first_stable_version": template_contract["scope"]["first_stable_version"],
        "last_verified_stable_version": template_contract["scope"]["last_verified_stable_version"],
        "stable_tags_verified": template_contract["scope"]["stable_tags_verified"],
        "future_unreleased_versions_claimed": False,
    }
    resource_redteam_source = ROOT / "verification" / "pagespec_resource_redteam_0.3.2.json"
    resource_redteam = json.loads(resource_redteam_source.read_text(encoding="utf-8"))
    for relative, expected_hash in resource_redteam.get("source_sha256", {}).items():
        if sha256(ROOT / relative) != expected_hash:
            raise RuntimeError(f"resource red-team report is stale for {relative}")
    if resource_redteam.get("status") != "PASS_SOURCE":
        raise RuntimeError("PageSpec resource red-team source gate did not pass")
    resource_redteam_target = candidate / "reports" / resource_redteam_source.name
    copy_file(resource_redteam_source, resource_redteam_target)
    resource_redteam_gate = {
        "report": str(resource_redteam_target.relative_to(candidate)),
        "report_sha256": sha256(resource_redteam_target),
        "unit_tests": resource_redteam["verification"]["unittest"],
        "browser_evidence_superseded_by_final_package_gates": True,
    }

    package_reports: list[dict[str, Any]] = []
    for variant, stage in zip(matrix["variants"], stages):
        package = candidate / "packages" / variant["package_filename"]
        package_with_official_cli(
            stage=stage,
            output=package,
            dify_cli=str(dify_cli),
            log=logs / f"official_cli_package_{variant['version']}.log",
        )
        extract_to = candidate / "_gate_extract" / variant["key"]
        extract_to.mkdir(parents=True)
        package_report = inspect_and_extract_package(package, extract_to, variant, matrix)
        sdk_output = run(
            [
                str(sdk_python),
                str(ROOT / "verification/scripts/sdk_smoke.py"),
                "--root", str(extract_to),
                "--expected-sdk", matrix["sdk_gate_exact"],
                "--expected-requirement", ">=0.9.0",
                "--sdk-wheel", str(sdk_wheel),
                "--expected-wheel-sha256", matrix["sdk_gate_wheel_sha256"],
                "--startup-seconds", "1.0",
            ],
            cwd=ROOT,
            log=logs / f"sdk_registration_start_invoke_{variant['version']}.log",
        )
        try:
            package_report["sdk_gate"] = json.loads(sdk_output.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError("SDK smoke did not emit its required JSON result") from exc
        package_audit_path = (
            candidate / "reports" / f"package_audit_{variant['version']}.json"
        )
        html_output_dir = candidate / "generated_html" / variant["key"]
        run(
            [
                str(sdk_python),
                str(ROOT / "verification/scripts/verify_packaged_plugin.py"),
                str(package),
                "--expected-version", variant["version"],
                "--expected-min-dify", variant["minimum_dify_version"],
                "--expected-sdk", matrix["sdk_gate_exact"],
                "--expected-requirement", matrix["requirements_exact"],
                "--html-output-dir", str(html_output_dir),
                "--report", str(package_audit_path),
            ],
            cwd=ROOT,
            log=logs / f"packaged_four_volume_compile_{variant['version']}.log",
        )
        package_audit = json.loads(package_audit_path.read_text(encoding="utf-8"))
        if package_audit.get("status") != "PASS":
            raise RuntimeError(f"packaged audit did not pass for {variant['version']}")
        package_report["packaged_audit"] = {
            "report": str(package_audit_path.relative_to(candidate)),
            "report_sha256": sha256(package_audit_path),
            "catalog_volumes": package_audit["catalog_volumes"],
            "catalog_total": package_audit["catalog_total"],
            "generated_html": [
                {
                    "filename": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
                for path in sorted(html_output_dir.glob("*.html"))
            ],
        }
        browser_gates: list[dict[str, Any]] = []
        for browser_label, executable, runtime_root, expected_major in (
            (
                "109",
                chromium_109,
                browser_runtime_109,
                int(matrix["browser_gates"]["legacy_major"]),
            ),
            (
                "current",
                chromium_current,
                browser_runtime_current,
                int(matrix["browser_gates"]["current_major"]),
            ),
        ):
            browser_output = (
                candidate / "reports" / "browser" / variant["key"] / browser_label
            )
            browser_env = {
                "CATALOG_HTML_DIR": str(html_output_dir.resolve()),
                "CATALOG_BROWSER_OUT": str(browser_output.resolve()),
                "BROWSER_EXECUTABLE": str(executable),
                "BROWSER_EXPECTED_MAJOR": str(expected_major),
                "BROWSER_RUNTIME": str(runtime_root),
                "BROWSER_CJK_FONT_FAMILY": cjk_font_identity["family"],
                # The @sparticuz configs point at Lambda-only paths.  Use a
                # project-generated config bound to the matrix-locked Noto
                # font so screenshot evidence contains real Chinese glyphs.
                "FONTCONFIG_PATH": str(fontconfig_file.parent),
                "FONTCONFIG_FILE": str(fontconfig_file),
            }
            browser_runtime_root = executable.parent
            browser_lib = browser_runtime_root / "lib"
            if browser_lib.is_dir():
                browser_env["LD_LIBRARY_PATH"] = str(browser_lib)
            run(
                [str(node), str(ROOT / "verification/scripts/catalog_browser_audit.mjs"), "--mobile"],
                cwd=ROOT,
                log=logs / f"browser_{browser_label}_{variant['version']}_base.log",
                env=browser_env,
            )
            run(
                [str(node), str(ROOT / "verification/scripts/catalog_browser_audit.mjs"), "--mutations"],
                cwd=ROOT,
                log=logs / f"browser_{browser_label}_{variant['version']}_mutations.log",
                env=browser_env,
            )
            browser_gates.append(
                verify_browser_reports(
                    html_dir=html_output_dir,
                    output_dir=browser_output,
                    variant=variant,
                    browser_label=browser_label,
                    expected_major=expected_major,
                )
            )
        package_report["browser_gates"] = browser_gates
        html_test_zip = candidate / "html_test_packages" / variant["html_test_filename"]
        deterministic_zip(
            html_output_dir,
            html_test_zip,
            f"PageSpec_{variant['version']}_{variant['label']}_四卷全库网页",
        )
        with zipfile.ZipFile(html_test_zip) as archive:
            html_members = [name for name in archive.namelist() if name.endswith(".html")]
            if len(html_members) != 4 or archive.testzip() is not None:
                raise RuntimeError(
                    f"{variant['version']}: webpage ZIP does not contain four CRC-clean HTML files"
                )
        package_report["html_test_zip"] = {
            "path": str(html_test_zip.relative_to(candidate)),
            "bytes": html_test_zip.stat().st_size,
            "sha256": sha256(html_test_zip),
            "html_members": len(html_members),
            "bare_html_delivered": False,
        }
        workflow_dir = candidate / "workflows" / variant["workflow_directory"]
        run(
            [
                sys.executable,
                str(ROOT / "dev_sources/build_workflows.py"),
                "--package", str(package),
                "--version", variant["version"],
                "--minimum-dify-version", variant["minimum_dify_version"],
                "--output-dir", str(workflow_dir),
            ],
            cwd=ROOT,
            log=logs / f"workflow_generation_{variant['version']}.log",
        )
        package_reports.append({**variant, "package": package_report})

    workflow_output = run(
        [
            sys.executable,
            str(ROOT / "verification/scripts/check_workflows.py"),
            "--release-root", str(candidate),
            "--matrix", str(args.matrix.resolve()),
            "--report", str(candidate / "reports/workflow_static_check.json"),
        ],
        cwd=ROOT,
        log=logs / "eight_workflow_gate.log",
    )
    workflow_report = json.loads(workflow_output.strip().splitlines()[-1])
    if workflow_report.get("workflow_count") != 8:
        raise RuntimeError("workflow gate did not prove exactly eight workflows")

    source_stage = candidate / "_delivery_stage" / "source"
    stage_source_snapshot(source_stage)
    run(
        [sys.executable, "-m", "py_compile", *map(str, sorted(source_stage.rglob("*.py")))],
        cwd=source_stage,
        log=logs / "delivered_source_py_compile.log",
    )
    run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=source_stage,
        log=logs / "delivered_source_unittest.log",
        env={"PYTHONPATH": str(source_stage / "tools")},
    )
    source_zip = candidate / "PageSpec_0.3.2_0.3.3_完整源码_含vendor.zip"
    deterministic_zip(source_stage, source_zip, "PageSpec_0.3.2_0.3.3_source")

    complete_stage = candidate / "_delivery_stage" / "complete"
    for path in sorted((candidate / "packages").glob("*.difypkg")):
        copy_file(path, complete_stage / "安装包" / path.name)
    for directory in sorted((candidate / "workflows").iterdir()):
        copy_tree(directory, complete_stage / "工作流" / directory.name)
    for path in sorted((candidate / "html_test_packages").glob("*.zip")):
        copy_file(path, complete_stage / "四卷全库网页测试包" / path.name)
    copy_tree(candidate / "reports", complete_stage / "校验报告")
    copy_file(source_zip, complete_stage / "源码" / source_zip.name)
    copy_file(args.matrix, complete_stage / "发布矩阵" / args.matrix.name)
    (complete_stage / "DELIVERY_CONTENT_MANIFEST.json").write_text(
        json.dumps(
            {
                "schema": "pagespec-delivery-content/v2",
                "status": "PASS",
                "packaging": "official Dify CLI only for .difypkg",
                "requirements_exact": matrix["requirements_exact"],
                "sdk_gate_exact": matrix["sdk_gate_exact"],
                "toolchain": toolchain,
                "dify_template_contract_gate": template_contract_gate,
                "resource_redteam_gate": resource_redteam_gate,
                "packages": package_reports,
                "workflow_count": 8,
                "catalog_volumes": list(matrix["catalog_volumes"].values()),
                "catalog_total": sum(matrix["catalog_volumes"].values()),
                "bare_html_delivered": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    included = [path for path in complete_stage.rglob("*") if path.is_file()]
    bare_html = [path for path in included if path.suffix.lower() in {".html", ".htm"}]
    if bare_html:
        raise RuntimeError(f"complete delivery contains forbidden bare HTML: {bare_html}")
    write_sums(included, complete_stage / "SHA256SUMS.txt", complete_stage)
    complete_zip = candidate / "PageSpec_0.3.2_0.3.3_完整交付包.zip"
    deterministic_zip(complete_stage, complete_zip, "PageSpec_0.3.2_0.3.3_delivery")

    artifacts = [
        *sorted((candidate / "packages").glob("*.difypkg")),
        *sorted((candidate / "workflows").rglob("*.yml")),
        *sorted((candidate / "html_test_packages").glob("*.zip")),
        source_zip,
        complete_zip,
    ]
    write_sums(artifacts, candidate / "SHA256SUMS.txt", candidate)
    report = {
        "schema": "pagespec-dual-release/v2",
        "status": "PASS",
        "packaging": "official Dify CLI only for .difypkg",
        "requirements_exact": matrix["requirements_exact"],
        "sdk_gate_exact": matrix["sdk_gate_exact"],
        "toolchain": toolchain,
        "dify_template_contract_gate": template_contract_gate,
        "resource_redteam_gate": resource_redteam_gate,
        "packages": package_reports,
        "workflow_count": 8,
        "catalog_volumes": list(matrix["catalog_volumes"].values()),
        "catalog_total": sum(matrix["catalog_volumes"].values()),
        "artifacts": [
            {"path": str(path.relative_to(candidate)), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in artifacts
        ],
    }
    (candidate / "RELEASE_MANIFEST.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.rmtree(candidate / "_official_stage")
    shutil.rmtree(candidate / "_gate_extract")
    shutil.rmtree(candidate / "_delivery_stage")
    shutil.rmtree(candidate / "generated_html")
    return report


def publish(candidate: Path, release: Path) -> None:
    if release.exists():
        raise RuntimeError(
            f"refusing to replace an existing release directory automatically: {release}"
        )
    os.replace(candidate, release)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--dify-cli", default="dify")
    parser.add_argument(
        "--sdk-python",
        help="Python executable whose environment contains dify_plugin==0.9.1",
    )
    parser.add_argument(
        "--sdk-wheel",
        type=Path,
        help="audited official dify_plugin 0.9.1 wheel used to byte-check the SDK environment",
    )
    parser.add_argument("--chromium-109", help="Chromium 109 executable")
    parser.add_argument("--chromium-current", help="current Chromium executable")
    parser.add_argument(
        "--browser-runtime-109",
        type=Path,
        help="frozen npm runtime root for the Chromium 109 audit",
    )
    parser.add_argument(
        "--browser-runtime-current",
        type=Path,
        help="frozen npm runtime root for the current-Chromium audit",
    )
    parser.add_argument(
        "--cjk-font",
        type=Path,
        help="matrix-locked Noto Sans CJK SC font used only by browser audits",
    )
    parser.add_argument(
        "--dify-repo",
        type=Path,
        default=Path("/tmp/dify-source"),
        help="official langgenius/dify clone with release tags for the Template contract gate",
    )
    parser.add_argument(
        "--graphon-repo",
        type=Path,
        default=Path("/tmp/graphon-source-main"),
        help="official langgenius/graphon clone with locked release tags",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="stage and validate both source trees, but create no packages or workflows",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="atomically rename the completed candidate to --release",
    )
    args = parser.parse_args()
    report = build(args)
    if args.publish and not args.prepare_only:
        publish(args.candidate.resolve(), args.release.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
