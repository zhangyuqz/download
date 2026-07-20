# -*- coding: utf-8 -*-
"""PageSpec-only resource boundary.

This module owns the non-Tool leaf operations needed by ``RenderPageTool``:
reading Dify image slots, validating image containers, creating placeholders,
and loading hash-locked vendor assets.  It deliberately imports neither
``dify_plugin.Tool`` nor the legacy arbitrary-HTML exporter, so PageSpec can be
changed and tested without crossing an independent tool boundary.
"""
from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import socket
import struct
import time
import xml.etree.ElementTree as ET
import zlib
from html import escape as html_escape
from html import unescape as html_unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, unquote_to_bytes, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = PLUGIN_ROOT / "vendor"
VENDOR_MAP_PATH = VENDOR_DIR / "vendor_map.json"

MIB = 1024 * 1024
OUTPUT_WARN_BYTES = 26 * MIB
OUTPUT_REJECT_BYTES = 28 * MIB
MAX_SLOT_FILE_BYTES = 20 * MIB
MAX_TOTAL_SLOT_BYTES = 20 * MIB
MAX_IMAGE_SIDE = 16_384
MAX_IMAGE_PIXELS = 100_000_000
MAX_IMAGE_ANIMATION_FRAMES = 256
MAX_SVG_EMBEDDED_DEPTH = 3
MAX_SVG_EMBEDDED_RESOURCES = 64
MAX_SVG_EMBEDDED_BYTES = 20 * MIB
MAX_TOTAL_IMAGE_PIXELS = 100_000_000
FILE_FETCH_TIMEOUT_SECONDS = 4
MAX_FILE_CANDIDATES_PER_SLOT = 24
MAX_FILE_FETCH_ATTEMPTS = 20 * (1 + MAX_FILE_CANDIDATES_PER_SLOT)
MAX_FILE_FETCH_TOTAL_SECONDS = 24

# Dify has changed the shape used for template/file values several times.  A
# slot is therefore treated as a bounded transport envelope, not as one exact
# SDK class.  These names mirror the public File fields used by old and new
# Dify releases and the wrapper fields emitted by template/code/tool nodes.
FILE_URL_FIELDS = (
    "url", "preview_url", "download_url", "file_url", "source_url", "remote_url",
)
FILE_BYTE_FIELDS = ("_blob", "blob", "base64", "content", "bytes", "body")
FILE_WRAPPER_FIELDS = (
    "files", "file", "output", "result", "data", "json", "images", "items",
    "message", "meta", "variable_value", "json_object", "text", "value", "payload",
)
FILE_URL_ENV = (
    "INTERNAL_FILES_URL", "DIFY_INNER_API_URL", "PLUGIN_DIFY_INNER_API_URL",
    "FILES_URL", "DIFY_FILES_URL", "DIFY_API_URL", "INNER_API_URL", "CONSOLE_API_URL",
)
INTERNAL_FILE_BASES = (
    "http://api:5001", "http://dify-api:5001", "http://dify_api:5001",
    "http://127.0.0.1", "http://127.0.0.1:5001", "http://localhost",
    "http://localhost:5001", "http://api", "http://dify-api", "http://nginx",
    "http://dify-nginx", "http://host.docker.internal",
    "http://host.docker.internal:5001", "https://host.docker.internal",
    "http://gateway.docker.internal", "http://host.containers.internal",
)
MAX_FILE_WRAPPER_DEPTH = 16
MAX_FILE_WRAPPER_NODES = 4096
MAX_FILE_INPUT_CANDIDATES = 256

DIFY_FILE_IDENTITY = "__dify__file__"
_SIGNED_TOOL_FILE_PATH = re.compile(
    r"/files/tools/([0-9a-fA-F-]{36})(?:\.([A-Za-z0-9]{1,12}))?\Z"
)
_DIRECT_BROWSER_IMAGE_MIMES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    "image/avif", "image/bmp", "image/x-icon", "image/vnd.microsoft.icon",
})
_SVG_CSS_PRESENTATION_ATTRS = frozenset({
    "fill", "stroke", "filter", "clip-path", "mask", "cursor",
    "marker", "marker-start", "marker-mid", "marker-end",
})
_SVG_IMAGE_RESOURCE_ATTRS = frozenset({"poster", "background", "data"})
_SVG_NAVIGATION_ATTRS = frozenset({"action", "formaction", "manifest", "ping"})
_SVG_CANDIDATE_LIST_ATTRS = frozenset({"srcset", "imagesrcset"})


# CSS identifiers may spell resource functions through simple or hexadecimal
# escapes.  These patterns classify what a browser will interpret, rather than
# only the most common literal spelling.
_CSS_ESCAPE_GAP = r"(?:\r\n|[\t\n\f\r ])?"


def _css_keyword_pattern(keyword: str) -> str:
    """Build a regex fragment for one CSS keyword including CSS escapes."""
    pieces: list[str] = []
    for char in keyword:
        if char.isalpha():
            code = f"{ord(char):x}"
            pieces.append(
                rf"(?:{re.escape(char)}|\\{re.escape(char)}|\\0{{0,{6 - len(code)}}}{code}{_CSS_ESCAPE_GAP})"
            )
        else:
            pieces.append(re.escape(char))
    return "".join(pieces)


_CSS_URL_KEYWORD = _css_keyword_pattern("url")
_CSS_IMPORT_KEYWORD = _css_keyword_pattern("import")
_CSS_IMAGE_SET_KEYWORD = _css_keyword_pattern("image-set")
_CSS_WEBKIT_IMAGE_SET_KEYWORD = _css_keyword_pattern("-webkit-image-set")
_CSS_URL_FUNCTION = re.compile(
    rf"(?<![-_A-Za-z0-9]){_CSS_URL_KEYWORD}\s*\(", re.I
)
_CSS_IMPORT_TOKEN = re.compile(
    rf"@{_CSS_IMPORT_KEYWORD}(?![-_A-Za-z0-9])", re.I
)
_CSS_IMAGE_SET_FUNCTION = re.compile(
    rf"(?<![-_A-Za-z0-9])(?:{_CSS_WEBKIT_IMAGE_SET_KEYWORD}|{_CSS_IMAGE_SET_KEYWORD})\s*\(",
    re.I,
)


def _is_embedded_url(value: str | None) -> bool:
    """Return whether a URL remains self-contained after the HTML is saved."""
    if value is None:
        return True
    normalized = html_unescape(value).strip().lower()
    # A pre-existing blob URL belongs to a different browsing context and is
    # therefore intentionally not classified as a portable embedded resource.
    return not normalized or normalized.startswith(("data:", "#")) or normalized == "about:blank"


def _decode_css_escapes(value: str) -> str:
    """Decode CSS escapes only for resource classification, without reserializing CSS."""

    def replace_hex(match: re.Match) -> str:
        try:
            codepoint = int(match.group(1), 16)
            if codepoint == 0 or codepoint > 0x10FFFF:
                return "\ufffd"
            return chr(codepoint)
        except ValueError:
            return "\ufffd"

    value = re.sub(
        r"\\([0-9a-fA-F]{1,6})(?:\r\n|[\t\n\f\r ])?",
        replace_hex,
        value,
    )
    return re.sub(r"\\([^\r\n\f])", r"\1", value)


def _protect_non_resource_css_literals(css: str) -> tuple[str, list[tuple[str, str]]]:
    """Hide comments and strings that cannot denote a CSS resource."""
    prefix = "__PAGESPEC_CSS_LITERAL_" + hashlib.sha256(
        css.encode("utf-8")
    ).hexdigest()[:16] + "_"
    while prefix in css:
        prefix += "X"
    saved: list[tuple[str, str]] = []
    out: list[str] = []
    functions: list[str] = []
    pending_identifier: str | None = None
    import_active = False
    i = 0
    length = len(css)

    def protect(value: str) -> str:
        token = f"{prefix}{len(saved)}__"
        saved.append((token, value))
        return token

    def resource_context() -> bool:
        return import_active or bool(
            functions and functions[-1] in {"url", "image-set", "-webkit-image-set"}
        )

    while i < length:
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            end = length if end < 0 else end + 2
            value = css[i:end]
            out.append(value if resource_context() else protect(value))
            i = end
            continue

        char = css[i]
        if char in {'"', "'"}:
            quote_char = char
            end = i + 1
            while end < length:
                if css[end] == "\\":
                    end += 2
                    continue
                if css[end] == quote_char:
                    end += 1
                    break
                end += 1
            value = css[i:min(end, length)]
            out.append(value if resource_context() else protect(value))
            pending_identifier = None
            i = min(end, length)
            continue

        import_match = _CSS_IMPORT_TOKEN.match(css, i)
        if import_match:
            value = import_match.group(0)
            out.append(value)
            import_active = True
            pending_identifier = None
            i += len(value)
            continue

        if char.isalpha() or char in {"_", "-", "\\"}:
            end = i + 1
            while end < length:
                if css[end].isalnum() or css[end] in {"_", "-"}:
                    end += 1
                    continue
                if css[end] == "\\" and end + 1 < length:
                    escaped = end + 1
                    digits = 0
                    while (
                        escaped < length
                        and digits < 6
                        and css[escaped] in "0123456789abcdefABCDEF"
                    ):
                        escaped += 1
                        digits += 1
                    if digits:
                        if css.startswith("\r\n", escaped):
                            escaped += 2
                        elif escaped < length and css[escaped] in "\t\n\f\r ":
                            escaped += 1
                    else:
                        escaped += 1
                    end = escaped
                    continue
                break
            value = css[i:end]
            out.append(value)
            pending_identifier = _decode_css_escapes(value).lower()
            i = end
            continue

        out.append(char)
        if char == "(":
            functions.append(pending_identifier or "")
            pending_identifier = None
        elif char == ")":
            if functions:
                functions.pop()
            pending_identifier = None
        elif char == ";":
            if not functions:
                import_active = False
            pending_identifier = None
        elif char == "{":
            import_active = False
            pending_identifier = None
        elif not char.isspace():
            pending_identifier = None
        i += 1
    return "".join(out), saved


def _restore_css_literals(css: str, saved: list[tuple[str, str]]) -> str:
    """Restore byte-identical comments and non-resource strings."""
    for token, value in saved:
        css = css.replace(token, value)
    return css


def _protect_css_namespace_rules(css: str) -> tuple[str, list[tuple[str, str]]]:
    """Hide @namespace identifiers because they name XML namespaces, not loads."""
    prefix = "__PAGESPEC_CSS_NAMESPACE_" + hashlib.sha256(
        css.encode("utf-8")
    ).hexdigest()[:16] + "_"
    while prefix in css:
        prefix += "X"
    saved: list[tuple[str, str]] = []
    out: list[str] = []
    position = 0
    finder = re.compile(r"@namespace(?![-_A-Za-z0-9])", re.I)
    while True:
        match = finder.search(css, position)
        if match is None:
            out.append(css[position:])
            break
        i = match.end()
        quote_char: str | None = None
        depth = 0
        while i < len(css):
            char = css[i]
            if quote_char:
                if char == "\\":
                    i += 2
                    continue
                if char == quote_char:
                    quote_char = None
            elif char in {'"', "'"}:
                quote_char = char
            elif char == "(":
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif char == ";" and depth == 0:
                i += 1
                break
            elif char == "{" and depth == 0:
                break
            i += 1
        out.append(css[position:match.start()])
        token = f"{prefix}{len(saved)}__"
        statement = css[match.start():i]
        saved.append((token, statement))
        out.append(token)
        position = i
    return "".join(out), saved


def _css_matching_paren(css: str, opening: int) -> int | None:
    """Find the matching parenthesis while respecting CSS strings and escapes."""
    depth = 1
    quote_char: str | None = None
    i = opening + 1
    while i < len(css):
        char = css[i]
        if quote_char:
            if char == "\\":
                i += 2
                continue
            if char == quote_char:
                quote_char = None
            i += 1
            continue
        if char in {'"', "'"}:
            quote_char = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _css_string_end(css: str, start: int) -> int:
    """Return the exclusive end offset of a possibly escaped CSS string."""
    quote_char = css[start]
    i = start + 1
    while i < len(css):
        if css[i] == "\\":
            i += 2
            continue
        if css[i] == quote_char:
            return i + 1
        i += 1
    return len(css)


def _css_url_value(inner: str) -> str:
    """Extract and decode the semantic URL value from one url() body."""
    value = re.sub(r"/\*.*?\*/", "", inner, flags=re.S).strip()
    if len(value) >= 2 and value[0] in {'"', "'"} and value[-1] == value[0]:
        value = value[1:-1]
    return _decode_css_escapes(value).strip()


def _rewrite_css_url_functions(css: str) -> str:
    """Replace every non-portable CSS url() with a network-inert value."""
    out: list[str] = []
    position = 0
    while True:
        match = _CSS_URL_FUNCTION.search(css, position)
        if match is None:
            out.append(css[position:])
            break
        opening = css.find("(", match.start(), match.end())
        closing = _css_matching_paren(css, opening)
        if closing is None:
            out.append(css[position:])
            break
        out.append(css[position:match.start()])
        original = css[match.start():closing + 1]
        value = _css_url_value(css[opening + 1:closing])
        out.append(original if value and _is_embedded_url(value) else "url(data:,)")
        position = closing + 1
    return "".join(out)


def _rewrite_css_image_set_strings(css: str) -> str:
    """Neutralize bare non-embedded string candidates inside image-set()."""
    out: list[str] = []
    position = 0
    while True:
        match = _CSS_IMAGE_SET_FUNCTION.search(css, position)
        if match is None:
            out.append(css[position:])
            break
        opening = css.find("(", match.start(), match.end())
        closing = _css_matching_paren(css, opening)
        if closing is None:
            out.append(css[position:])
            break
        inner = css[opening + 1:closing]
        rebuilt: list[str] = []
        i = 0
        depth = 0
        candidate_start = True
        while i < len(inner):
            char = inner[i]
            if char in {'"', "'"}:
                end = _css_string_end(inner, i)
                literal = inner[i:end]
                if depth == 0 and candidate_start:
                    value = _decode_css_escapes(literal[1:-1]).strip()
                    if not value or not _is_embedded_url(value):
                        literal = literal[0] + "data:," + (
                            literal[-1] if len(literal) > 1 else literal[0]
                        )
                rebuilt.append(literal)
                candidate_start = False
                i = end
                continue
            rebuilt.append(char)
            if char == "(":
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif char == "," and depth == 0:
                candidate_start = True
            elif not char.isspace() and depth == 0:
                candidate_start = False
            i += 1
        out.append(css[position:opening + 1])
        out.append("".join(rebuilt))
        out.append(")")
        position = closing + 1
    return "".join(out)


def _remove_nonembedded_css_imports(css: str) -> str:
    """Drop @import statements whose target is not a self-contained fragment."""
    out: list[str] = []
    position = 0
    while True:
        match = _CSS_IMPORT_TOKEN.search(css, position)
        if match is None:
            out.append(css[position:])
            break
        i = match.end()
        quote_char: str | None = None
        depth = 0
        while i < len(css):
            char = css[i]
            if quote_char:
                if char == "\\":
                    i += 2
                    continue
                if char == quote_char:
                    quote_char = None
            elif char in {'"', "'"}:
                quote_char = char
            elif char == "(":
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif char == ";" and depth == 0:
                i += 1
                break
            elif char == "{" and depth == 0:
                break
            i += 1
        statement = css[match.start():i]
        target = ""
        url_match = _CSS_URL_FUNCTION.search(statement)
        if url_match:
            opening = statement.find("(", url_match.start(), url_match.end())
            closing = _css_matching_paren(statement, opening)
            if closing is not None:
                target = _css_url_value(statement[opening + 1:closing])
        else:
            offset = match.end() - match.start()
            string_match = re.search(r"[\"']", statement[offset:])
            if string_match:
                start = offset + string_match.start()
                end = _css_string_end(statement, start)
                target = _decode_css_escapes(
                    statement[start + 1:max(start + 1, end - 1)]
                ).strip()
        out.append(css[position:match.start()])
        # data: stylesheets can contain hidden secondary loads, so only inert
        # fragment/about:blank imports may remain in a self-contained page.
        if target and _is_embedded_url(target) and not target.lower().startswith("data:"):
            out.append(statement)
        position = i
    return "".join(out)


_TRANSPARENT_GIF_DATA_URI = (
    "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)
_EMPTY_DATA_URL_PATTERN = r"url\(\s*[\"']?data:,\s*[\"']?\s*\)"


def _finalize_css_resources(css: str) -> str:
    """Remove unusable font fallbacks and make empty image URLs decodable."""
    empty_source = _EMPTY_DATA_URL_PATTERN + r"(?:\s*format\(\s*[^)]*\s*\))?"

    def clean_font_face(match: re.Match) -> str:
        block = match.group(0)
        urls = re.findall(r"url\(\s*([^)]+?)\s*\)", block, flags=re.I)
        has_local = re.search(r"\blocal\s*\(", block, flags=re.I) is not None
        valid_urls = [
            value for value in urls
            if value.strip().strip("\"'").strip().lower() != "data:,"
        ]
        if urls and not valid_urls and not has_local:
            return ""
        block = re.sub(empty_source + r"\s*,\s*", "", block, flags=re.I)
        block = re.sub(r",\s*" + empty_source, "", block, flags=re.I)
        return re.sub(empty_source, "", block, flags=re.I)

    css = re.sub(r"@font-face\s*\{[^{}]*\}", clean_font_face, css, flags=re.I)
    return re.sub(
        _EMPTY_DATA_URL_PATTERN,
        f"url({_TRANSPARENT_GIF_DATA_URI})",
        css,
        flags=re.I,
    )


def sanitize_css(css: str) -> str:
    """Return vendor CSS with every non-embedded resource made network-inert."""
    css, protected_literals = _protect_non_resource_css_literals(css)
    css, protected_namespaces = _protect_css_namespace_rules(css)
    css = _remove_nonembedded_css_imports(css)
    css = _rewrite_css_url_functions(css)
    css = _rewrite_css_image_set_strings(css)
    css = _finalize_css_resources(css)
    css = _restore_css_literals(css, protected_namespaces)
    return _restore_css_literals(css, protected_literals)


def load_vendor_map() -> dict:
    """Read the PageSpec-bundled library registry from its fixed package path."""
    with VENDOR_MAP_PATH.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _read_vendor(name: str) -> str:
    """Decode one packaged UTF-8 asset without newline normalization."""
    return (VENDOR_DIR / name).read_bytes().decode("utf-8")


def read_verified_vendor(spec: dict, name: str) -> str:
    """Read one vendor asset and enforce its registry SHA-256 before use."""
    text = _read_vendor(name)
    expected = (spec.get("sha256") or {}).get(name)
    if expected:
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual != expected:
            raise ValueError(f"{name} SHA-256 不匹配")
    return text


def _origin(parts) -> tuple[str, str, int] | None:
    """Normalize one HTTP(S) origin for strict configured-origin comparison."""
    try:
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return None
    return parts.scheme.lower(), parts.hostname.lower().rstrip("."), port


def _safe_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    try:
        return getattr(value, key, default)
    except Exception:
        return default


def _strip_file_shell(value: Any) -> str:
    """Remove transport-only wrappers without changing the signed query."""
    raw = str(value or "").strip().replace("\ufeff", "").replace("\u200b", "")
    for _ in range(3):
        lowered = raw.lower()
        removed = False
        for opening, closing in (
            ("&quot;", "&quot;"), ("&#34;", "&#34;"), ("&#x22;", "&#x22;"),
            ("&apos;", "&apos;"), ("&#39;", "&#39;"), ("&#x27;", "&#x27;"),
        ):
            if lowered.startswith(opening) and lowered.endswith(closing):
                raw = raw[len(opening):-len(closing)].strip()
                removed = True
                break
        if removed:
            continue
        if len(raw) >= 2 and (raw[0], raw[-1]) in {
            ('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("`", "`"),
            ("<", ">"), ("(", ")"), ("[", "]"),
        }:
            raw = raw[1:-1].strip()
        else:
            break
    return raw


def _local_file_bytes(value: Any) -> bytes | None:
    """Decode bounded in-memory/file-like/data-URI/base64 candidates."""
    if isinstance(value, bytes):
        return value if len(value) <= MAX_SLOT_FILE_BYTES else None
    if isinstance(value, (bytearray, memoryview)):
        data = bytes(value)
        return data if len(data) <= MAX_SLOT_FILE_BYTES else None
    if not isinstance(value, str) and callable(getattr(value, "read", None)):
        position = None
        try:
            if callable(getattr(value, "tell", None)):
                position = value.tell()
            if callable(getattr(value, "seek", None)):
                value.seek(0)
            data = value.read(MAX_SLOT_FILE_BYTES + 1)
            if position is not None and callable(getattr(value, "seek", None)):
                value.seek(position)
            if isinstance(data, (bytes, bytearray, memoryview)) and len(data) <= MAX_SLOT_FILE_BYTES:
                return bytes(data)
        except Exception:
            try:
                if position is not None and callable(getattr(value, "seek", None)):
                    value.seek(position)
            except Exception:
                pass
        return None
    if not isinstance(value, str):
        return None
    text = _strip_file_shell(value)
    if text.lower().startswith("data:") and "," in text:
        header, payload = text.split(",", 1)
        if len(payload) > (MAX_SLOT_FILE_BYTES * 4 // 3 + 32):
            return None
        try:
            data = (base64.b64decode(payload, validate=True)
                    if ";base64" in header.lower() else unquote_to_bytes(payload))
            return data if len(data) <= MAX_SLOT_FILE_BYTES else None
        except (ValueError, binascii.Error):
            return None
    if 16 <= len(text) <= (MAX_SLOT_FILE_BYTES * 4 // 3 + 32) and re.fullmatch(
        r"[A-Za-z0-9+/_-]+={0,2}", text
    ):
        try:
            padded = text + "=" * (-len(text) % 4)
            data = (base64.urlsafe_b64decode(padded)
                    if "-" in text or "_" in text else base64.b64decode(padded, validate=True))
            return data if len(data) <= MAX_SLOT_FILE_BYTES else None
        except (ValueError, binascii.Error):
            return None
    return None


def _stored_file_bytes(value: Any) -> bytes | None:
    mappings: list[dict] = [value] if isinstance(value, dict) else []
    if not isinstance(value, dict):
        try:
            mapping = vars(value)
            if isinstance(mapping, dict):
                mappings.append(mapping)
        except Exception:
            pass
        try:
            private = object.__getattribute__(value, "__pydantic_private__")
            if isinstance(private, dict):
                mappings.append(private)
        except Exception:
            pass
    for mapping in mappings:
        for field in FILE_BYTE_FIELDS:
            if field in mapping:
                data = _local_file_bytes(mapping.get(field))
                if data is not None:
                    return data
    return None


def _decode_file_container(value: str) -> Any | None:
    text = value.strip()
    fence = re.fullmatch(
        r"(?:```|~~~)(?:json|javascript|python|text)?\s*(.*?)\s*(?:```|~~~)",
        text, flags=re.I | re.S,
    )
    if fence:
        text = fence.group(1).strip()
    for _ in range(5):
        if not text or text[0] not in "[({\"'":
            break
        try:
            parsed = json.loads(text)
        except Exception:
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                parsed = None
        if isinstance(parsed, str) and parsed.strip() != text:
            text = parsed.strip()
            continue
        if isinstance(parsed, (dict, list, tuple)):
            return parsed
        break
    return None


def _repair_file_url_variants(value: Any) -> list[str]:
    """Generate bounded spelling repairs while preserving query bytes."""
    raw = _strip_file_shell(value)
    if not raw or len(raw) > 65536:
        return []
    output: list[str] = []
    queue: list[tuple[str, int]] = []

    def add(item: str, depth: int = 0) -> None:
        item = _strip_file_shell(item)
        if item and item not in output:
            output.append(item)
            queue.append((item, depth))

    add(raw)
    mapping = {"002f": "/", "005c": "\\", "003a": ":"}
    index = 0
    while index < len(queue) and len(output) < 32:
        current, depth = queue[index]
        index += 1
        if depth >= 8:
            continue
        structure, marker, query = current.partition("?")
        normalized = structure.replace("／", "/").replace("＼", "\\").replace("：", ":")
        normalized = re.sub(
            r"\\u(002[fF]|005[cC]|003[aA])",
            lambda match: mapping[match.group(1).lower()], normalized,
        )
        add(normalized + (marker + query if marker else ""), depth + 1)
        repaired = re.sub(r"\s+", "", normalized.strip().replace("\\/", "/").replace("\\", "/"))
        match = re.match(r"^(https?)(?::)?/+(.+)$", repaired, flags=re.I)
        if match:
            repaired = match.group(1).lower() + "://" + match.group(2).lstrip("/")
        else:
            match = re.match(r"^(https?):([^/].+)$", repaired, flags=re.I)
            if match:
                repaired = match.group(1).lower() + "://" + match.group(2)
        add(repaired + (marker + query if marker else ""), depth + 1)
        add(re.sub(r"&(?:amp|#38|#x0*26);", "&", current, flags=re.I), depth + 1)
        decoded_structure = unquote(structure)
        if decoded_structure != structure:
            add(decoded_structure + (marker + query if marker else ""), depth + 1)
        if not marker:
            decoded = unquote(current)
            if decoded != current:
                add(decoded, depth + 1)
    return output[:32]


def _looks_file_host(authority: str) -> bool:
    authority = str(authority or "").strip()
    if not authority or "@" in authority or any(char.isspace() for char in authority):
        return False
    try:
        parsed = urlsplit("//" + authority)
        if not parsed.hostname:
            return False
        _ = parsed.port
    except (ValueError, UnicodeError):
        return False
    host = parsed.hostname
    if host.lower() == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        try:
            encoded = host.rstrip(".").encode("idna").decode("ascii")
        except Exception:
            return False
        return bool(encoded and all(re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part
        ) for part in encoded.split(".")))


def _scheme_order(authority: str) -> tuple[str, str]:
    try:
        host = (urlsplit("//" + authority).hostname or authority).lower()
    except Exception:
        host = authority.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    return (("http", "https") if host == "localhost" or "." not in host
            or (address and (address.is_private or address.is_loopback)) else ("https", "http"))


def _embedded_file_urls(value: str) -> list[str]:
    if len(value) > 65536:
        return []
    output: list[str] = []

    def add(item: str) -> None:
        item = _strip_file_shell(item).rstrip("),.;，。；）]")
        if item and item not in output:
            output.append(item)

    text = value.strip()
    markdown = re.fullmatch(r"!?\[[^\]]*\]\(\s*(\S+?)(?:\s+[\"'][^\"']*[\"'])?\s*\)", text)
    if markdown:
        add(markdown.group(1).strip("<>"))
    image = re.fullmatch(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", text, flags=re.I)
    if image:
        add(image.group(1))
    for match in re.finditer(
        r"[^\s<>\"'，。；（）()]*[\\/]files[\\/][^\s<>\"'，。；（）()]+", value, flags=re.I
    ):
        add(match.group(0))
    for match in re.finditer(
        r"(?:(?:https?)\s*(?::[\\/]*|[\\/]{1,4})|//)[^\s<>\"']+", value, flags=re.I
    ):
        add(match.group(0))
    if not output:
        for variant in _repair_file_url_variants(text):
            if "/files/" in variant.lower() or re.match(r"^https?://", variant, flags=re.I):
                add(variant)
                break
    return output[:256]


def _file_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        embedded = _embedded_file_urls(value)
        return embedded or ([_strip_file_shell(value)] if _strip_file_shell(value) else [])
    output: list[str] = []
    for field in FILE_URL_FIELDS:
        raw = _safe_get(value, field)
        if isinstance(raw, str) and raw.strip():
            for item in (_embedded_file_urls(raw) or [_strip_file_shell(raw)]):
                if item not in output:
                    output.append(item)
    return output


def _file_paths(raw: str) -> list[str]:
    output: list[str] = []

    def add(path: str) -> None:
        if path and not path.startswith("/"):
            path = "/" + path
        structure, marker, query = path.partition("?")
        structure = re.sub(r"/{2,}", "/", structure)
        path = structure + (marker + query if marker else "")
        if path and path not in output:
            output.append(path)

    lower = raw.lower()
    if lower.startswith("files/"):
        add(raw)
    elif lower.startswith(("./files/", ".\\files\\")):
        add("/files/" + raw[8:])
    elif re.match(r"^/+/files/", raw, flags=re.I):
        marker = lower.find("/files/")
        add(raw[marker:])
    elif re.match(r"^https?://", raw, flags=re.I) or raw.startswith("//"):
        try:
            parsed = urlsplit(("https:" + raw) if raw.startswith("//") else raw)
        except Exception:
            parsed = None
        if parsed is not None and "/files/" in parsed.path.lower():
            add(parsed.path + (("?" + parsed.query) if parsed.query else ""))
    elif "/files/" in lower:
        add(raw[lower.find("/files/"):])
    return output


def _configured_file_base_urls() -> list[str]:
    output: list[str] = []
    for name in FILE_URL_ENV:
        value = (os.environ.get(name) or "").strip()
        for variant in _repair_file_url_variants(value):
            direct = _direct_file_urls(variant)
            for item in direct:
                parts = urlsplit(item)
                origin = f"{parts.scheme.lower()}://{parts.netloc}"
                path = parts.path.rstrip("/")
                marker = path.lower().find("/files")
                prefix = path[:marker] if marker >= 0 else path
                for base in ((origin + prefix) if prefix else origin, origin):
                    if base not in output:
                        output.append(base)
    return output


def _configured_file_bases() -> list[tuple[str, str]]:
    """Backward-compatible tuple view used by older tests and diagnostics."""
    result: list[tuple[str, str]] = []
    for value in _configured_file_base_urls():
        parts = urlsplit(value)
        item = (parts.scheme, parts.netloc)
        if item not in result:
            result.append(item)
    return result


def _direct_file_urls(value: Any) -> list[str]:
    output: list[str] = []

    def add(item: str) -> None:
        if item and item not in output:
            output.append(item)

    for raw in _repair_file_url_variants(value):
        if re.match(r"^https?://", raw, flags=re.I):
            try:
                parts = urlsplit(raw)
                _ = parts.port
            except (ValueError, UnicodeError):
                continue
            if parts.hostname and not parts.username and not parts.password:
                add(urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, "")))
        elif raw.startswith("//"):
            slash = raw.find("/", 2)
            authority = raw[2:slash] if slash >= 0 else raw[2:]
            if authority.lower() != "files" and _looks_file_host(authority):
                for scheme in _scheme_order(authority):
                    add(scheme + ":" + raw)
        else:
            slash = raw.find("/")
            authority = raw[:slash] if slash > 0 else ""
            if (authority.lower() != "files" and _looks_file_host(authority)
                    and not re.match(r"^https?(?::)?[\\/]", raw, flags=re.I)):
                for scheme in _scheme_order(authority):
                    add(f"{scheme}://{authority}{raw[slash:]}")
    return output


def _forbidden_metadata_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").replace("。", ".").replace("．", ".").rstrip(".").lower()
        _ = parts.port
    except (ValueError, UnicodeError):
        return True
    if parts.scheme.lower() not in {"http", "https"} or not host or parts.username or parts.password:
        return True
    if host in {
        "metadata.google.internal", "metadata.google", "instance-data", "100.100.100.200",
        "169.254.169.254", "169.254.170.2", "fd00:ec2::254", "fd20:ce::254",
    }:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        if re.fullmatch(r"(?i)(?:0x[0-9a-f]+|0[0-7]+|\d+|[0-9a-fx.]+)", host):
            try:
                address = ipaddress.ip_address(socket.inet_aton(host))
            except OSError:
                pass
    return bool(address and (address.is_link_local or address.is_multicast or address.is_unspecified))


def _file_url_candidates(url: str) -> list[str]:
    """Generate direct and Dify-internal candidates without changing query order."""
    raw_input = _strip_file_shell(url)
    if re.match(r"^https?://", raw_input, flags=re.I):
        try:
            input_parts = urlsplit(raw_input)
        except (ValueError, UnicodeError):
            return []
        if input_parts.username or input_parts.password:
            return []
    output: list[str] = []

    def add(item: str) -> None:
        if item and item not in output and not _forbidden_metadata_url(item):
            output.append(item)

    variants = _repair_file_url_variants(url)
    for variant in variants:
        for direct in _direct_file_urls(variant):
            add(direct)
    paths: list[str] = []
    for variant in variants:
        for path in _file_paths(variant):
            if path not in paths:
                paths.append(path)
    bases = _configured_file_base_urls() + list(INTERNAL_FILE_BASES)
    for base in bases:
        for path in paths[:6]:
            add(base.rstrip("/") + path)
            if len(output) >= 144:
                return output
    return output[:144]


def _validate_dify_file_url(url: str) -> str | None:
    """Compatibility validator: accept all bounded safe Dify/public candidates."""
    candidates = _file_url_candidates(url)
    if not candidates:
        return "文件地址无法归一为安全的 http/https 或 Dify /files 路径"
    return None


def _iter_file_candidates(value: Any, depth: int = 0, seen: set[int] | None = None,
                          budget: list[int] | None = None):
    """Flatten historical Dify file/template envelopes deterministically."""
    if value is None or depth > MAX_FILE_WRAPPER_DEPTH:
        return
    if seen is None:
        seen = set()
    if budget is None:
        budget = [MAX_FILE_WRAPPER_NODES]
    budget[0] -= 1
    if budget[0] < 0:
        return
    if not isinstance(value, (str, bytes, bytearray, memoryview, int, float, bool)):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
    if isinstance(value, str):
        decoded = _decode_file_container(value)
        if decoded is not None:
            yield from _iter_file_candidates(decoded, depth + 1, seen, budget)
            return
        if _local_file_bytes(value) is not None or _file_urls(value):
            yield value
        return
    if isinstance(value, (bytes, bytearray, memoryview)) or callable(getattr(value, "read", None)):
        yield value
        return
    if isinstance(value, (list, tuple, set)):
        items = (sorted(value, key=lambda item: repr(item))
                 if isinstance(value, set) else value)
        for item in items:
            yield from _iter_file_candidates(item, depth + 1, seen, budget)
        return
    if isinstance(value, dict):
        if _stored_file_bytes(value) is not None or _file_urls(value):
            yield value
        visited: set[str] = set()
        for key in FILE_WRAPPER_FIELDS:
            if key in value:
                visited.add(key)
                yield from _iter_file_candidates(value[key], depth + 1, seen, budget)
        if not (_stored_file_bytes(value) is not None or _file_urls(value)):
            for key, item in value.items():
                if key not in visited and isinstance(item, (dict, list, tuple, set, str, bytes, bytearray, memoryview)):
                    yield from _iter_file_candidates(item, depth + 1, seen, budget)
        return
    if _stored_file_bytes(value) is not None or _file_urls(value):
        yield value
        return
    try:
        mapping = vars(value)
    except Exception:
        mapping = None
    if isinstance(mapping, dict):
        yield from _iter_file_candidates(mapping, depth + 1, seen, budget)


def _bounded_download(url: str, timeout: float | None = None) -> bytes:
    """Read one validated signed URL without redirects and within the slot limit."""

    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = Request(url, headers={"User-Agent": "PageSpec-Offline-Renderer/0.3"})
    opener = build_opener(_NoRedirect())
    with opener.open(
        request,
        timeout=max(
            0.05,
            min(FILE_FETCH_TIMEOUT_SECONDS, timeout or FILE_FETCH_TIMEOUT_SECONDS),
        ),
    ) as response:
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > MAX_SLOT_FILE_BYTES:
                    raise ValueError("文件响应超过单插槽上限")
            except ValueError as exc:
                if str(exc) == "文件响应超过单插槽上限":
                    raise
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(min(64 * 1024, MAX_SLOT_FILE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SLOT_FILE_BYTES:
                raise ValueError("文件响应超过单插槽上限")
            chunks.append(chunk)
        return b"".join(chunks)


def file_bytes(
    file_value,
    cache: dict[str, tuple[bytes | None, str | None]] | None = None,
    fetch_budget: dict[str, Any] | None = None,
) -> tuple[bytes | None, str | None]:
    """Read one historical/current Dify file value with bounded best-effort repair."""
    declared_size = _safe_get(file_value, "size")
    if (
        isinstance(declared_size, int)
        and not isinstance(declared_size, bool)
        and declared_size > MAX_SLOT_FILE_BYTES
    ):
        return None, f"文件声明大小超过单插槽上限 {MAX_SLOT_FILE_BYTES // MIB} MiB"

    blob = _local_file_bytes(file_value) or _stored_file_bytes(file_value)
    sdk_error = None
    if blob is None and _safe_get(file_value, "dify_model_identity") == DIFY_FILE_IDENTITY:
        if fetch_budget is None or (
            fetch_budget.get("remaining", 0) > 0
            and time.monotonic() < fetch_budget.get("deadline", float("inf"))
        ):
            if fetch_budget is not None:
                fetch_budget["remaining"] -= 1
            try:
                sdk_blob = file_value.blob
                if isinstance(sdk_blob, (bytes, bytearray, memoryview)):
                    blob = bytes(sdk_blob)
            except Exception as exc:
                sdk_error = type(exc).__name__

    urls = _file_urls(file_value)
    # Some Dify versions expose a stale/placeholder ``_blob`` together with a
    # working signed URL.  Do not let the first malformed candidate suppress
    # later valid candidates.
    if blob is not None and urls:
        detected = sniff_image_mime(bytes(blob))
        if detected is None:
            blob = None
    attempts = 0
    cache_key = "|".join(urls)
    if blob is None and cache is not None and cache_key and cache_key in cache:
        return cache[cache_key]
    if blob is None:
        normalized_urls: list[str] = []
        for raw in urls:
            for candidate in _file_url_candidates(raw):
                if candidate not in normalized_urls:
                    normalized_urls.append(candidate)
        if not normalized_urls:
            return None, "未找到可读取的图片字节或安全文件地址"
        for candidate in normalized_urls[:MAX_FILE_CANDIDATES_PER_SLOT]:
                remaining_time = (
                    fetch_budget.get("deadline", float("inf")) - time.monotonic()
                    if fetch_budget is not None else FILE_FETCH_TIMEOUT_SECONDS
                )
                if fetch_budget is not None and (
                    fetch_budget.get("remaining", 0) <= 0 or remaining_time <= 0
                ):
                    break
                if fetch_budget is not None:
                    fetch_budget["remaining"] -= 1
                attempts += 1
                try:
                    blob = _bounded_download(candidate, remaining_time)
                    break
                except (HTTPError, URLError, OSError, TimeoutError, ValueError):
                    continue
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        detail = f"SDK {sdk_error}；" if sdk_error else ""
        if fetch_budget is not None and (
            fetch_budget.get("remaining", 0) <= 0
            or time.monotonic() >= fetch_budget.get("deadline", float("inf"))
        ):
            detail += "整次导出的文件读取次数或总时限预算已用完；"
        result = (None, f"{detail}文件兼容读取失败（已尝试 {attempts} 个受限地址）")
        if cache is not None and cache_key:
            cache[cache_key] = result
        return result

    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return None, "Dify SDK 返回的文件内容不是字节数据"
    blob = bytes(blob)
    if len(blob) > MAX_SLOT_FILE_BYTES:
        return None, f"文件实际大小超过单插槽上限 {MAX_SLOT_FILE_BYTES // MIB} MiB"
    result = (blob, None)
    if cache is not None and cache_key:
        cache[cache_key] = result
    return result


def slot_placeholder(slot: str, reason: str = "未上传图片") -> str:
    """Create a labeled inline SVG when a PageSpec image slot cannot be used."""
    safe_slot = html_escape(str(slot)[:160], quote=False)
    safe_reason = html_escape(str(reason)[:160], quote=False)
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='320' height='120'>"
        "<rect x='1' y='1' width='318' height='118' rx='10' fill='#808a99' fill-opacity='0.12' "
        "stroke='#8d95a2' stroke-opacity='0.6' stroke-width='2' stroke-dasharray='8 6'/>"
        f"<text x='160' y='56' text-anchor='middle' fill='#8d95a2' font-family='sans-serif' font-size='15'>{safe_slot} · {safe_reason}</text>"
        "<text x='160' y='80' text-anchor='middle' fill='#8d95a2' fill-opacity='0.75' font-family='sans-serif' font-size='12'>导出已继续；详情见插件节点文本输出</text>"
        "</svg>"
    )
    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")


def sniff_image_mime(blob: bytes) -> str | None:
    """Identify supported image types from bytes, never from a filename."""
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[:2] == b"BM":
        return "image/bmp"
    if blob[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    if blob[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if len(blob) >= 16 and blob[4:8] == b"ftyp":
        box_size = int.from_bytes(blob[:4], "big")
        brand_end = min(len(blob), box_size if box_size >= 16 else 16, 4096)
        brands = {blob[8:12]} | {
            blob[i:i + 4] for i in range(16, brand_end, 4)
        }
        if brands & {b"avif", b"avis"}:
            return "image/avif"
        if brands & {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "image/heic"
    lead = blob[:1024].lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
    if lead.startswith(b"<svg") or (lead.startswith(b"<?xml") and b"<svg" in lead):
        return "image/svg+xml"
    if blob.startswith((b"\xff\xfe", b"\xfe\xff")) or lead.startswith(
        (b"<!--", b"<!DOCTYPE", b"<!doctype")
    ):
        try:
            root = ET.fromstring(blob)
            if root.tag.rsplit("}", 1)[-1].lower() == "svg":
                return "image/svg+xml"
        except (ET.ParseError, ValueError):
            pass
    return None


def _decode_data_url(
    value: str,
    allowed_mimes: set[str] | None = None,
) -> tuple[str, bytes] | None:
    """Strictly decode one bounded data URL used by a nested SVG image."""
    if not value.lower().startswith("data:") or "," not in value:
        return None
    header, payload = value[5:].split(",", 1)
    fields = header.split(";")
    mime = (fields[0] or "text/plain").strip().lower()
    flags = [item.strip().lower() for item in fields[1:] if item.strip()]
    if allowed_mimes is not None and mime not in allowed_mimes:
        return None
    try:
        if flags and flags[-1] == "base64":
            if any(item == "base64" for item in flags[:-1]):
                return None
            decoded = base64.b64decode(payload, validate=True)
        else:
            if "base64" in flags:
                return None
            decoded = unquote_to_bytes(payload)
    except (ValueError, binascii.Error):
        return None
    return mime, decoded


def _css_data_urls(css: str) -> list[str]:
    """Extract semantic data: values from CSS url() functions."""
    results: list[str] = []
    for match in _CSS_URL_FUNCTION.finditer(css):
        opening = css.find("(", match.start(), match.end())
        closing = _css_matching_paren(css, opening)
        if closing is None:
            continue
        value = _css_url_value(css[opening + 1:closing])
        if value.lower().startswith("data:"):
            results.append(value)
    results.extend(
        value for value in _css_image_set_string_values(css)
        if value.lower().startswith("data:")
    )
    return results


def _css_image_set_string_values(css: str) -> list[str]:
    """Return bare string candidates from image-set() at candidate depth zero."""
    values: list[str] = []
    position = 0
    while True:
        match = _CSS_IMAGE_SET_FUNCTION.search(css, position)
        if match is None:
            break
        opening = css.find("(", match.start(), match.end())
        closing = _css_matching_paren(css, opening)
        if closing is None:
            break
        inner = css[opening + 1:closing]
        i = 0
        depth = 0
        candidate_start = True
        while i < len(inner):
            char = inner[i]
            if char in {'"', "'"}:
                end = _css_string_end(inner, i)
                if depth == 0 and candidate_start:
                    values.append(_decode_css_escapes(inner[i + 1:max(i + 1, end - 1)]).strip())
                candidate_start = False
                i = end
                continue
            if char == "(":
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif char == "," and depth == 0:
                candidate_start = True
            elif not char.isspace() and depth == 0:
                candidate_start = False
            i += 1
        position = closing + 1
    return values


def _css_has_nonembedded_resource(css: str) -> bool:
    """Detect a CSS resource that would need a file or network outside the page."""
    protected, literals = _protect_non_resource_css_literals(css)
    protected, namespaces = _protect_css_namespace_rules(protected)
    if _CSS_IMPORT_TOKEN.search(protected):
        return True
    position = 0
    while True:
        match = _CSS_URL_FUNCTION.search(protected, position)
        if match is None:
            break
        opening = protected.find("(", match.start(), match.end())
        closing = _css_matching_paren(protected, opening)
        if closing is None:
            return True
        if not _is_embedded_url(_css_url_value(protected[opening + 1:closing])):
            return True
        position = closing + 1
    if any(not _is_embedded_url(value) for value in _css_image_set_string_values(protected)):
        return True
    # Restoring is not needed for classification, but retaining both lists in
    # scope documents that namespace/literal tokens were intentionally ignored.
    _ = literals, namespaces
    return False


def image_frame_count(blob: bytes, mime: str) -> int:
    """Return a conservative frame count after container validation."""
    if mime == "image/webp":
        declared_end = min(len(blob), int.from_bytes(blob[4:8], "little") + 8)
        position = 12
        frames = 0
        while position + 8 <= declared_end:
            size = int.from_bytes(blob[position + 4:position + 8], "little")
            if blob[position:position + 4] == b"ANMF":
                frames += 1
            position += 8 + size + (size & 1)
        return max(1, frames)
    if mime == "image/gif":
        position = 13
        if blob[10] & 0x80:
            position += 3 * (2 ** ((blob[10] & 0x07) + 1))
        frames = 0
        while position < len(blob):
            marker = blob[position]
            position += 1
            if marker == 0x3B:
                break
            if marker == 0x21:
                position += 1
                while position < len(blob):
                    size = blob[position]
                    position += 1
                    if size == 0:
                        break
                    position += size
                continue
            if marker != 0x2C or position + 9 > len(blob):
                break
            frames += 1
            packed = blob[position + 8]
            position += 9
            if packed & 0x80:
                position += 3 * (2 ** ((packed & 0x07) + 1))
            position += 1
            while position < len(blob):
                size = blob[position]
                position += 1
                if size == 0:
                    break
                position += size
        return max(1, frames)
    return 1


def _svg_embedded_images(
    root: ET.Element,
    depth: int,
    budget: dict[str, int],
) -> str | None:
    """Validate nested SVG image data and reject all external/relative loads."""
    urls: list[str] = []
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
        normalized_attributes = {
            name.rsplit("}", 1)[-1].lower(): (value or "").strip()
            for name, value in element.attrib.items()
        }
        for attr_name, attr_value in element.attrib.items():
            attr_local = attr_name.rsplit("}", 1)[-1].lower()
            value = (attr_value or "").strip()
            if attr_local in {"href", "src"} and value:
                if value.startswith("#"):
                    continue
                if local not in {"image", "feimage"} or not value.lower().startswith("data:image/"):
                    return f"SVG <{local or '?'}> 的 {attr_local} 不是安全片段或内嵌图片"
                urls.append(value)
            elif attr_local in _SVG_IMAGE_RESOURCE_ATTRS and value:
                if value.startswith("#") or value.lower() == "about:blank":
                    continue
                if not value.lower().startswith("data:image/"):
                    return f"SVG <{local or '?'}> 的 {attr_local} 不是内嵌图片"
                urls.append(value)
            elif attr_local in _SVG_NAVIGATION_ATTRS and value and not value.startswith("#"):
                return f"SVG <{local or '?'}> 的 {attr_local} 会产生外部导航或请求"
            elif attr_local in _SVG_CANDIDATE_LIST_ATTRS and value:
                return f"SVG <{local or '?'}> 的 {attr_local} 候选资源列表不允许出现"
            elif attr_local == "srcdoc" and value:
                return "SVG foreignObject/iframe 的 srcdoc 内嵌文档不允许出现"
            is_css_value = (
                attr_local == "style"
                or attr_local in _SVG_CSS_PRESENTATION_ATTRS
                or bool(re.search(r"(?:url|(?:-webkit-)?image-set)\s*\(", value, re.I))
                or bool(re.search(r"@import\b", value, re.I))
            )
            if is_css_value:
                if _css_has_nonembedded_resource(value):
                    return f"SVG <{local or '?'}> 的 {attr_local} 内含外部/相对加载资源"
                urls.extend(_css_data_urls(value))
        if (
            local == "meta"
            and normalized_attributes.get("http-equiv", "").lower() == "refresh"
            and normalized_attributes.get("content")
        ):
            return "SVG foreignObject 内含 meta refresh 导航"
        if local == "style" and element.text:
            if _css_has_nonembedded_resource(element.text):
                return "SVG style 内含外部/相对加载资源"
            urls.extend(_css_data_urls(element.text))

    for value in urls:
        budget["resources"] += 1
        if budget["resources"] > MAX_SVG_EMBEDDED_RESOURCES:
            return f"SVG 内嵌图片超过 {MAX_SVG_EMBEDDED_RESOURCES} 个资源上限"
        decoded = _decode_data_url(value, set(_DIRECT_BROWSER_IMAGE_MIMES))
        if decoded is None:
            return "SVG 内嵌图片 data URL 无法安全解码"
        declared_mime, child = decoded
        budget["bytes"] += len(child)
        if budget["bytes"] > MAX_SVG_EMBEDDED_BYTES:
            return f"SVG 内嵌图片解码后累计超过 {MAX_SVG_EMBEDDED_BYTES // MIB} MiB"
        actual_mime = sniff_image_mime(child)
        if actual_mime != declared_mime:
            return "SVG 内嵌图片声明格式与字节签名不一致"
        if actual_mime == "image/svg+xml" and depth >= MAX_SVG_EMBEDDED_DEPTH:
            return f"SVG 内嵌层级超过 {MAX_SVG_EMBEDDED_DEPTH} 层"
        dimensions, error = validate_image_blob(
            child,
            actual_mime,
            _svg_depth=depth + 1,
            _svg_budget=budget,
        )
        if error:
            return "SVG 内嵌图片不安全：" + error
        if dimensions:
            budget["pixels"] += (
                dimensions[0] * dimensions[1] * image_frame_count(child, actual_mime)
            )
            if budget["pixels"] > MAX_TOTAL_IMAGE_PIXELS:
                return "SVG 内嵌图片累计解码像素超过上限"
    return None


def validate_image_blob(
    blob: bytes,
    mime: str,
    _svg_depth: int = 0,
    _svg_budget: dict[str, int] | None = None,
) -> tuple[tuple[int, int] | None, str | None]:
    """Validate image framing, dimensions, animation bounds, and SVG resources."""
    dimensions: tuple[int, int] | None = None
    try:
        if mime == "image/png":
            if len(blob) < 33 or blob[:8] != b"\x89PNG\r\n\x1a\n":
                return None, "PNG 头或 IHDR 不完整"
            position = 8
            saw_ihdr = False
            saw_plte = False
            saw_iend = False
            idat_parts: list[bytes] = []
            bit_depth = color_type = interlace = 0
            while position + 12 <= len(blob):
                size = int.from_bytes(blob[position:position + 4], "big")
                kind = blob[position + 4:position + 8]
                end = position + 12 + size
                if end > len(blob):
                    return None, "PNG 数据块被截断"
                payload = blob[position + 8:position + 8 + size]
                expected_crc = int.from_bytes(blob[position + 8 + size:end], "big")
                if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
                    return None, f"PNG {kind.decode('ascii', 'replace')} 数据块 CRC 错误"
                if not saw_ihdr:
                    if kind != b"IHDR" or size != 13:
                        return None, "PNG 首个数据块不是完整 IHDR"
                    dimensions = (
                        int.from_bytes(payload[:4], "big"),
                        int.from_bytes(payload[4:8], "big"),
                    )
                    bit_depth, color_type = payload[8], payload[9]
                    if payload[10] != 0 or payload[11] != 0 or payload[12] not in {0, 1}:
                        return None, "PNG IHDR 使用了不支持的压缩、过滤或隔行参数"
                    interlace = payload[12]
                    valid_depths = {
                        0: {1, 2, 4, 8, 16},
                        2: {8, 16},
                        3: {1, 2, 4, 8},
                        4: {8, 16},
                        6: {8, 16},
                    }
                    if color_type not in valid_depths or bit_depth not in valid_depths[color_type]:
                        return None, "PNG IHDR 的颜色类型与位深组合不合法"
                    saw_ihdr = True
                elif kind == b"PLTE":
                    saw_plte = True
                elif kind == b"IDAT":
                    idat_parts.append(payload)
                if kind == b"IEND":
                    if size != 0:
                        return None, "PNG IEND 数据块不合法"
                    saw_iend = True
                    break
                position = end
            if not saw_ihdr or not saw_iend:
                return None, "PNG 缺少 IHDR 或 IEND"
            if not idat_parts or not any(idat_parts):
                return None, "PNG 缺少非空 IDAT 图像数据"
            if color_type == 3 and not saw_plte:
                return None, "索引色 PNG 缺少 PLTE 调色板"

            width, height = dimensions
            if width <= 0 or height <= 0:
                return None, "PNG 声明的宽高不是正数"
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
            bits_per_pixel = channels * bit_depth
            row_lengths: list[int] = []
            if interlace == 0:
                row_lengths = [1 + ((width * bits_per_pixel + 7) // 8)] * height
            else:
                # Adam7 pass geometry. Each non-empty pass row begins with one
                # filter byte just like an ordinary PNG scanline.
                for x0, y0, dx, dy in (
                    (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
                    (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
                    (0, 1, 1, 2),
                ):
                    if width <= x0 or height <= y0:
                        continue
                    pass_width = (width - x0 + dx - 1) // dx
                    pass_height = (height - y0 + dy - 1) // dy
                    row_lengths.extend(
                        [1 + ((pass_width * bits_per_pixel + 7) // 8)] * pass_height
                    )
            expected_bytes = sum(row_lengths)
            decoder = zlib.decompressobj()
            decoded_total = 0
            row_index = 0
            row_remaining = 0

            def inspect_scanlines(data: bytes) -> str | None:
                nonlocal decoded_total, row_index, row_remaining
                offset = 0
                decoded_total += len(data)
                if decoded_total > expected_bytes:
                    return "PNG IDAT 解压后数据超过声明尺寸"
                while offset < len(data):
                    if row_remaining == 0:
                        if row_index >= len(row_lengths):
                            return "PNG IDAT 含声明图像以外的数据"
                        if data[offset] > 4:
                            return "PNG 扫描行使用了非法过滤器"
                        row_remaining = row_lengths[row_index] - 1
                        row_index += 1
                        offset += 1
                        continue
                    take = min(row_remaining, len(data) - offset)
                    row_remaining -= take
                    offset += take
                return None

            try:
                for compressed in idat_parts:
                    pending = compressed
                    while pending:
                        limit = min(64 * 1024, expected_bytes - decoded_total + 1)
                        if limit <= 0:
                            return None, "PNG IDAT 解压后数据超过声明尺寸"
                        output = decoder.decompress(pending, limit)
                        pending = decoder.unconsumed_tail
                        scan_error = inspect_scanlines(output)
                        if scan_error:
                            return None, scan_error
                tail = decoder.flush()
                scan_error = inspect_scanlines(tail)
                if scan_error:
                    return None, scan_error
            except zlib.error:
                return None, "PNG IDAT zlib 数据无法完整解压"
            if not decoder.eof or decoder.unused_data:
                return None, "PNG IDAT zlib 数据流不完整或含尾随数据"
            if decoded_total != expected_bytes or row_index != len(row_lengths) or row_remaining:
                return None, "PNG IDAT 解压长度与 IHDR 声明尺寸不一致"

        elif mime == "image/jpeg":
            if len(blob) < 12 or blob[:2] != b"\xff\xd8" or blob.rfind(b"\xff\xd9") < 4:
                return None, "JPEG SOI/EOI 不完整"
            position = 2
            sof_markers = {
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            }
            while position + 1 < len(blob):
                if blob[position] != 0xFF:
                    position += 1
                    continue
                while position < len(blob) and blob[position] == 0xFF:
                    position += 1
                if position >= len(blob):
                    break
                marker = blob[position]
                position += 1
                if marker in {0x00, 0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                    if marker == 0xD9:
                        break
                    continue
                if position + 2 > len(blob):
                    return None, "JPEG 段长度被截断"
                segment_size = int.from_bytes(blob[position:position + 2], "big")
                if segment_size < 2 or position + segment_size > len(blob):
                    return None, "JPEG 段边界不合法"
                if marker in sof_markers:
                    if segment_size < 7:
                        return None, "JPEG SOF 段不完整"
                    dimensions = (
                        int.from_bytes(blob[position + 5:position + 7], "big"),
                        int.from_bytes(blob[position + 3:position + 5], "big"),
                    )
                position += segment_size
                if marker == 0xDA:
                    break
            if dimensions is None:
                return None, "JPEG 缺少可识别的 SOF 尺寸段"

        elif mime == "image/gif":
            if len(blob) < 14 or blob[:6] not in {b"GIF87a", b"GIF89a"}:
                return None, "GIF 头或逻辑屏幕描述符不完整"
            dimensions = struct.unpack("<HH", blob[6:10])
            position = 13
            packed = blob[10]
            if packed & 0x80:
                position += 3 * (2 ** ((packed & 0x07) + 1))
            frames = 0

            def skip_sub_blocks(offset: int) -> int | None:
                while offset < len(blob):
                    block_size = blob[offset]
                    offset += 1
                    if block_size == 0:
                        return offset
                    if offset + block_size > len(blob):
                        return None
                    offset += block_size
                return None

            while position < len(blob):
                marker = blob[position]
                position += 1
                if marker == 0x3B:
                    if frames == 0:
                        return None, "GIF 没有图像帧"
                    break
                if marker == 0x21:
                    if position >= len(blob):
                        return None, "GIF 扩展块被截断"
                    position += 1
                    next_position = skip_sub_blocks(position)
                    if next_position is None:
                        return None, "GIF 扩展子块被截断"
                    position = next_position
                    continue
                if marker != 0x2C or position + 9 > len(blob):
                    return None, "GIF 图像块结构不合法"
                left, top, frame_width, frame_height = struct.unpack(
                    "<HHHH", blob[position:position + 8]
                )
                if frame_width <= 0 or frame_height <= 0:
                    return None, "GIF 图像帧宽高不是正数"
                if (
                    frame_width > MAX_IMAGE_SIDE
                    or frame_height > MAX_IMAGE_SIDE
                    or frame_width * frame_height > MAX_IMAGE_PIXELS
                    or left + frame_width > MAX_IMAGE_SIDE
                    or top + frame_height > MAX_IMAGE_SIDE
                ):
                    return None, f"GIF 图像帧 {frame_width}×{frame_height} @ {left},{top} 超过解码上限"
                image_packed = blob[position + 8]
                position += 9
                if image_packed & 0x80:
                    position += 3 * (2 ** ((image_packed & 0x07) + 1))
                if position >= len(blob):
                    return None, "GIF 图像数据被截断"
                position += 1
                next_position = skip_sub_blocks(position)
                if next_position is None:
                    return None, "GIF 图像子块被截断"
                position = next_position
                frames += 1
                if frames > MAX_IMAGE_ANIMATION_FRAMES:
                    return None, f"GIF 动画帧数超过 {MAX_IMAGE_ANIMATION_FRAMES} 帧上限"
            else:
                return None, "GIF 缺少文件结束标记"

        elif mime == "image/webp":
            if len(blob) < 20 or blob[:4] != b"RIFF" or blob[8:12] != b"WEBP":
                return None, "WebP RIFF/WEBP 头不完整"
            declared_end = int.from_bytes(blob[4:8], "little") + 8
            if declared_end > len(blob) or declared_end < 20:
                return None, "WebP RIFF 声明长度超出文件"
            position = 12
            saw_image = False
            canvas: tuple[int, int] | None = None
            image_dimensions: list[tuple[int, int]] = []
            animation_frames = 0
            while position + 8 <= declared_end:
                kind = blob[position:position + 4]
                size = int.from_bytes(blob[position + 4:position + 8], "little")
                start = position + 8
                end = start + size
                if end > declared_end:
                    return None, "WebP 数据块被截断"
                payload = blob[start:end]
                if kind == b"VP8X" and size >= 10:
                    candidate_canvas = (
                        1 + int.from_bytes(payload[4:7], "little"),
                        1 + int.from_bytes(payload[7:10], "little"),
                    )
                    if canvas is not None and canvas != candidate_canvas:
                        return None, "WebP 含冲突的 VP8X 画布尺寸"
                    canvas = candidate_canvas
                elif kind == b"VP8 " and size >= 10 and payload[3:6] == b"\x9d\x01\x2a":
                    image_dimensions.append((
                        int.from_bytes(payload[6:8], "little") & 0x3FFF,
                        int.from_bytes(payload[8:10], "little") & 0x3FFF,
                    ))
                    saw_image = True
                elif kind == b"VP8L" and size >= 5 and payload[0] == 0x2F:
                    bits = int.from_bytes(payload[1:5], "little")
                    image_dimensions.append(((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1))
                    saw_image = True
                elif kind == b"ANMF":
                    if size < 16:
                        return None, "WebP ANMF 动画帧头被截断"
                    x = 2 * int.from_bytes(payload[0:3], "little")
                    y = 2 * int.from_bytes(payload[3:6], "little")
                    frame_width = 1 + int.from_bytes(payload[6:9], "little")
                    frame_height = 1 + int.from_bytes(payload[9:12], "little")
                    image_dimensions.append((frame_width, frame_height))
                    animation_frames += 1
                    if animation_frames > MAX_IMAGE_ANIMATION_FRAMES:
                        return None, f"WebP 动画帧数超过 {MAX_IMAGE_ANIMATION_FRAMES} 帧上限"
                    if canvas and (x + frame_width > canvas[0] or y + frame_height > canvas[1]):
                        return None, "WebP 动画帧超出 VP8X 画布"
                    saw_image = True
                position = end + (size & 1)
            if not saw_image or not (canvas or image_dimensions):
                return None, "WebP 缺少完整图像块或画布尺寸"
            if canvas and image_dimensions and animation_frames == 0:
                if any(item != canvas for item in image_dimensions):
                    return None, "WebP VP8X 画布与实际图像尺寸冲突"
            dimensions = canvas or image_dimensions[0]
            for width, height in image_dimensions:
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    return None, "WebP 图像帧尺寸超过解码上限"

        elif mime == "image/bmp":
            if len(blob) < 26 or blob[:2] != b"BM":
                return None, "BMP 文件头不完整"
            declared_size = int.from_bytes(blob[2:6], "little")
            pixel_offset = int.from_bytes(blob[10:14], "little")
            dib_size = int.from_bytes(blob[14:18], "little")
            if declared_size and declared_size > len(blob):
                return None, "BMP 声明大小超出文件"
            if pixel_offset >= len(blob):
                return None, "BMP 像素偏移超出文件"
            if dib_size == 12 and len(blob) >= 26:
                dimensions = struct.unpack("<HH", blob[18:22])
            elif dib_size >= 40 and len(blob) >= 26:
                width, height = struct.unpack("<ii", blob[18:26])
                dimensions = (abs(width), abs(height))
            else:
                return None, "BMP DIB 头不受支持或被截断"

        elif mime in {"image/x-icon", "image/vnd.microsoft.icon"}:
            if len(blob) < 22 or blob[:4] != b"\x00\x00\x01\x00":
                return None, "ICO 文件头不完整"
            count = int.from_bytes(blob[4:6], "little")
            if count < 1 or 6 + 16 * count > len(blob):
                return None, "ICO 目录被截断"
            max_width = max_height = 0
            for index in range(count):
                entry = blob[6 + 16 * index:22 + 16 * index]
                width = entry[0] or 256
                height = entry[1] or 256
                size = int.from_bytes(entry[8:12], "little")
                offset = int.from_bytes(entry[12:16], "little")
                if size == 0 or offset + size > len(blob):
                    return None, "ICO 图像目录项超出文件"
                payload = blob[offset:offset + size]
                actual: tuple[int, int] | None = None
                if payload.startswith(b"\x89PNG\r\n\x1a\n"):
                    actual, error = validate_image_blob(payload, "image/png")
                    if error or actual is None:
                        return None, "ICO 内嵌 PNG 不合法：" + (error or "缺少尺寸")
                elif len(payload) >= 12:
                    dib_size = int.from_bytes(payload[:4], "little")
                    if dib_size == 12:
                        actual_width = int.from_bytes(payload[4:6], "little")
                        stored_height = int.from_bytes(payload[6:8], "little")
                    elif dib_size >= 40 and len(payload) >= 16:
                        actual_width = abs(int.from_bytes(payload[4:8], "little", signed=True))
                        stored_height = abs(int.from_bytes(payload[8:12], "little", signed=True))
                    else:
                        return None, "ICO 内嵌 DIB 头不受支持或被截断"
                    actual_height = stored_height // 2
                    if actual_width <= 0 or actual_height <= 0:
                        return None, "ICO 内嵌 DIB 宽高不合法"
                    actual = (actual_width, actual_height)
                else:
                    return None, "ICO 图像数据无法验证"
                actual_width, actual_height = actual
                max_width = max(max_width, width, actual_width)
                max_height = max(max_height, height, actual_height)
            dimensions = (max_width, max_height)

        elif mime == "image/avif":
            position = 0
            top_types: set[bytes] = set()
            brands: set[bytes] = set()
            while position + 8 <= len(blob):
                size = int.from_bytes(blob[position:position + 4], "big")
                kind = blob[position + 4:position + 8]
                header = 8
                if size == 1:
                    if position + 16 > len(blob):
                        return None, "AVIF 扩展盒头被截断"
                    size = int.from_bytes(blob[position + 8:position + 16], "big")
                    header = 16
                elif size == 0:
                    size = len(blob) - position
                if size < header or position + size > len(blob):
                    return None, "AVIF ISO-BMFF 盒边界不合法"
                top_types.add(kind)
                if kind == b"ftyp" and size >= header + 8:
                    payload = blob[position + header:position + size]
                    brands = {payload[:4]} | {
                        payload[i:i + 4] for i in range(8, len(payload), 4)
                    }
                position += size
            if b"ftyp" not in top_types or not brands & {b"avif", b"avis"}:
                return None, "AVIF 缺少正确 ftyp 品牌"
            if not top_types & {b"meta", b"moov"}:
                return None, "AVIF 缺少 meta/moov 图像结构"
            search = 0
            ispe_dimensions: set[tuple[int, int]] = set()
            while True:
                index = blob.find(b"ispe", search)
                if index < 4:
                    break
                box_size = int.from_bytes(blob[index - 4:index], "big")
                if box_size >= 20 and index - 4 + box_size <= len(blob):
                    ispe_dimensions.add((
                        int.from_bytes(blob[index + 8:index + 12], "big"),
                        int.from_bytes(blob[index + 12:index + 16], "big"),
                    ))
                search = index + 4
            if not ispe_dimensions:
                return None, "AVIF 缺少可验证的 ispe 图像尺寸"
            for width, height in ispe_dimensions:
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_IMAGE_SIDE
                    or height > MAX_IMAGE_SIDE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    return None, "AVIF ispe 图像尺寸超过解码上限"
            dimensions = max(ispe_dimensions, key=lambda item: item[0] * item[1])

        elif mime == "image/svg+xml":
            try:
                root = ET.fromstring(blob)
            except ET.ParseError:
                return None, "SVG XML 无法解析"
            if root.tag.rsplit("}", 1)[-1].lower() != "svg":
                return None, "SVG 根元素不是 svg"
            budget = _svg_budget or {"resources": 0, "bytes": 0, "pixels": 0}
            nested_error = _svg_embedded_images(root, _svg_depth, budget)
            if nested_error:
                return None, nested_error

            def svg_length(value: str | None) -> tuple[str, float | None]:
                if value is None or not value.strip():
                    return "absent", None
                text = value.strip()
                calc = re.fullmatch(r"calc\(\s*(.*?)\s*\)", text, re.I | re.S)
                if calc:
                    text = calc.group(1).strip()
                match = re.fullmatch(
                    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z%]*)",
                    text,
                )
                if not match:
                    return "invalid", None
                number = float(match.group(1))
                unit = match.group(2).lower()
                scales = {
                    "": 1.0,
                    "px": 1.0,
                    "in": 96.0,
                    "cm": 96.0 / 2.54,
                    "mm": 96.0 / 25.4,
                    "q": 96.0 / 101.6,
                    "pt": 96.0 / 72.0,
                    "pc": 16.0,
                }
                if unit in scales:
                    return "absolute", number * scales[unit]
                if unit in {"%", "em", "ex", "ch", "rem", "vw", "vh", "vmin", "vmax"}:
                    return "relative", None
                return "invalid", None

            width_kind, width = svg_length(root.attrib.get("width"))
            height_kind, height = svg_length(root.attrib.get("height"))
            if width_kind == "invalid" or height_kind == "invalid":
                return None, "SVG 显式宽高使用了无法静态验证的表达式或单位"
            for label, length in (("宽", width), ("高", height)):
                if length is not None and (length <= 0 or length > MAX_IMAGE_SIDE):
                    return None, f"SVG 显式{label}度 {length:.2f}px 超过解码上限"
            if width is not None and height is not None:
                dimensions = (int(width + 0.5), int(height + 0.5))
            elif root.attrib.get("viewBox"):
                values = re.split(r"[\s,]+", root.attrib["viewBox"].strip())
                if len(values) == 4:
                    dimensions = (
                        int(abs(float(values[2])) + 0.5),
                        int(abs(float(values[3])) + 0.5),
                    )
            if dimensions is None:
                dimensions = (300, 150)

    except (OverflowError, UnicodeError, ValueError, struct.error):
        return None, f"{mime} 容器结构无法可靠解析"

    if dimensions is not None:
        width, height = dimensions
        if width <= 0 or height <= 0:
            return None, "图片声明的宽高不是正数"
        if (
            width > MAX_IMAGE_SIDE
            or height > MAX_IMAGE_SIDE
            or width * height > MAX_IMAGE_PIXELS
        ):
            return None, (
                f"图片声明尺寸 {width}×{height} 超过 "
                f"{MAX_IMAGE_SIDE} 单边/{MAX_IMAGE_PIXELS:,} 像素解码上限"
            )
    return dimensions, None


def slot_data_uri(blob: bytes, filename: str | None) -> str:
    """Encode validated image bytes under their byte-detected MIME type."""
    mime = sniff_image_mime(blob) or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"


def collect_slots(
    params: dict,
    *,
    needed_slots: set[str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Normalize referenced PageSpec image slots without aborting the document.

    Dify versions and upstream nodes can expose one file parameter either as a
    single File or as a list. Every supplied candidate is tried in order until
    one passes byte-signature, browser-format, container, aggregate-byte and
    aggregate-decoded-pixel gates.
    """
    slots: dict[str, str] = {}
    errors: list[str] = []
    accepted_raw_bytes = 0
    accepted_decoded_pixels = 0
    file_cache: dict[str, tuple[bytes | None, str | None]] = {}
    fetch_budget = {
        "remaining": MAX_FILE_FETCH_ATTEMPTS,
        "deadline": time.monotonic() + MAX_FILE_FETCH_TOTAL_SECONDS,
    }

    for index in range(1, 21):
        slot_name = f"slot{index}"
        if needed_slots is not None and slot_name not in needed_slots:
            continue
        supplied = params.get(slot_name)
        if supplied is None:
            continue
        top_level = supplied if isinstance(supplied, (list, tuple)) else [supplied]
        candidates: list[Any] = []
        for item in top_level:
            if item is None:
                continue
            expanded = list(_iter_file_candidates(item))
            if expanded:
                candidates.extend(expanded)
            else:
                candidates.append(item)
            if len(candidates) >= MAX_FILE_INPUT_CANDIDATES:
                candidates = candidates[:MAX_FILE_INPUT_CANDIDATES]
                break
        if not candidates:
            continue

        selected_blob: bytes | None = None
        selected_filename: str | None = None
        selected_pixels = 0
        selected_index = -1
        candidate_errors: list[str] = []

        for candidate_index, candidate in enumerate(candidates):
            try:
                candidate_name = _safe_get(candidate, "filename") or _safe_get(candidate, "name")
                candidate_size = _safe_get(candidate, "size")
            except Exception as exc:
                candidate_errors.append(
                    f"第 {candidate_index + 1} 项：文件元数据读取失败（{type(exc).__name__}）"
                )
                continue

            remaining_raw = MAX_TOTAL_SLOT_BYTES - accepted_raw_bytes
            if (
                isinstance(candidate_size, int)
                and not isinstance(candidate_size, bool)
                and candidate_size >= 0
                and candidate_size > remaining_raw
            ):
                candidate_errors.append(
                    f"第 {candidate_index + 1} 项（{candidate_name or '?'}）："
                    "声明大小超过本次导出剩余累计图片预算"
                )
                continue

            blob, read_error = file_bytes(candidate, file_cache, fetch_budget)
            if blob is None:
                candidate_errors.append(
                    f"第 {candidate_index + 1} 项（{candidate_name or '?'}）：{read_error}"
                )
                continue
            mime = sniff_image_mime(blob)
            if mime not in _DIRECT_BROWSER_IMAGE_MIMES:
                candidate_errors.append(
                    f"第 {candidate_index + 1} 项（{candidate_name or '?'}）："
                    f"已识别为 {mime or '未知格式'}，目标浏览器不能稳定直接显示"
                )
                continue

            svg_budget = {"resources": 0, "bytes": 0, "pixels": 0}
            try:
                dimensions, validation_error = validate_image_blob(
                    blob,
                    mime,
                    _svg_budget=svg_budget if mime == "image/svg+xml" else None,
                )
            except Exception as exc:
                dimensions, validation_error = None, (
                    f"图片容器验证异常（{type(exc).__name__}）"
                )
            if validation_error:
                candidate_errors.append(
                    f"第 {candidate_index + 1} 项（{candidate_name or '?'}）：{validation_error}"
                )
                continue

            decoded_pixels = (
                dimensions[0] * dimensions[1] * image_frame_count(blob, mime)
                if dimensions else 0
            ) + svg_budget["pixels"]
            if accepted_decoded_pixels + decoded_pixels > MAX_TOTAL_IMAGE_PIXELS:
                candidate_errors.append(
                    f"第 {candidate_index + 1} 项（{candidate_name or '?'}）：加入后累计解码像素超过 "
                    f"{MAX_TOTAL_IMAGE_PIXELS:,} 上限"
                )
                continue
            if accepted_raw_bytes + len(blob) > MAX_TOTAL_SLOT_BYTES:
                candidate_errors.append(
                    f"第 {candidate_index + 1} 项（{candidate_name or '?'}）：加入后会超过图片累计 "
                    f"{MAX_TOTAL_SLOT_BYTES // MIB} MiB 上限"
                )
                continue

            selected_blob = blob
            selected_filename = candidate_name
            selected_pixels = decoded_pixels
            selected_index = candidate_index
            break

        if selected_blob is None:
            detail = "；".join(candidate_errors[:4]) or "没有可读取的 Dify 图片文件"
            slots[slot_name] = slot_placeholder(slot_name, detail)
            errors.append(f"slot{index}：{detail}（已用占位图）")
            continue
        if selected_index > 0:
            errors.append(
                f"slot{index}：文件数组前 {selected_index} 项不可用，"
                f"已采用第 {selected_index + 1} 项"
            )
        accepted_raw_bytes += len(selected_blob)
        accepted_decoded_pixels += selected_pixels
        slots[slot_name] = slot_data_uri(selected_blob, selected_filename)
    return slots, errors
