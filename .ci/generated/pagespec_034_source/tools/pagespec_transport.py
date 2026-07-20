# -*- coding: utf-8 -*-
"""High-tolerance, bounded transport decoder for PageSpec.

Strict JSON is the canonical format and therefore the zero-change fast path.
Inputs transported through Dify, templates, LLM markdown, or Python/Jinja
representations are recovered with bounded, non-executable transforms.  When
more than one recovery is plausible we *always* choose deterministically and
write the candidates, choice, reason, and confidence to the audit stream.

This module never calls ``eval`` and never executes input-controlled code.
"""
from __future__ import annotations

import ast
import html
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any


MAX_SPEC_BYTES = 2_000_000
MAX_STRING_LAYERS = 64
MAX_WRAPPER_LAYERS = 64
# Wrapper depth is counted independently from the PageSpec document's own
# block/layout nesting.  Keep enough tree budget for 64 transport envelopes
# plus a normal document without weakening the node/byte ceilings below.
MAX_TREE_DEPTH = 128
MAX_TREE_NODES = 120_000
MAX_CANDIDATES = 64
MAX_COMPLETION_CLOSERS = 8


# A single JSON slash before b/f/n/r/t is syntactically valid, but in a
# ``latex`` field it can be a Template/Dify-collapsed LaTeX command.  This is
# intentionally field-scoped and allowlisted: ordinary text and real control
# characters are not rewritten.
_LATEX_CONTROL_COMMANDS = frozenset({
    "backepsilon", "backprime", "backslash", "backsim", "backsimeq", "bar", "because",
    "begin", "belowbaseline", "beta", "beth", "between", "bf", "bfseries", "bgroup",
    "big", "bigcap", "bigcirc", "bigcup", "bigodot", "bigoplus", "bigotimes",
    "bigsqcup", "bigstar", "bigtriangledown", "bigtriangleup", "biguplus", "bigvee",
    "bigwedge", "binom", "bmod", "boldsymbol", "blacklozenge", "blacktriangle",
    "blacktriangledown", "blacktriangleleft", "blacktriangleright", "bm", "bot",
    "bowtie", "boxed", "brace", "brack", "breve", "bullet", "fbox", "fcolorbox",
    "female", "fi", "flat", "flushleft", "flushright", "footnotesize", "forall",
    "frac", "frown", "nabla", "natural", "ne", "nearrow", "neg", "neq",
    "newcommand", "newline", "newpage", "nexists", "ngtr", "ni", "nLeftarrow",
    "nLeftrightarrow", "nRightarrow", "nVDash", "nVdash", "nleftarrow",
    "nleftrightarrow", "nleq", "nless", "nmid", "nobreak", "nolimits", "nonumber",
    "normalsize", "not", "notag", "notin", "nparallel", "nprec", "nrightarrow",
    "nshortmid", "nshortparallel", "nsimeq", "nsubseteq", "nsucc", "nsupseteq",
    "ntriangleleft", "ntrianglelefteq", "ntriangleright", "ntrianglerighteq", "nu",
    "nwarrow", "raise", "raisebox", "rang", "rangle", "rbrace", "rbrack", "rceil",
    "rfloor", "rgroup", "rhd", "rho", "right", "rightarrow", "rightharpoondown",
    "rightharpoonup", "rightleftarrows", "rightleftharpoons", "rightrightarrows",
    "rightsquigarrow", "rightthreetimes", "rlap", "rm", "rmoustache", "root",
    "rotatebox", "rule", "rVert", "tag", "tan", "tanh", "tau", "text", "textbf",
    "textcolor", "textit", "textmd", "textnormal", "textrm", "textsc", "textsf",
    "textsl", "textstyle", "texttt", "textup", "tfrac", "theta", "therefore",
    "thickapprox", "thicksim", "thinspace", "tilde", "times", "tiny", "to", "top",
    "triangle", "triangledown", "triangleleft", "trianglelefteq", "triangleq",
    "triangleright", "trianglerighteq", "tt", "ttfamily", "twoheadleftarrow",
    "twoheadrightarrow",
})


def _int_decimal_digits(value: int) -> int:
    """Return a safe decimal digit estimate without calling ``str(int)``."""
    magnitude = abs(value)
    if not magnitude:
        return 1
    # bit_length*log10(2) is exact enough for sizing; the boundary correction
    # in _int_decimal_text does not rely on this estimate for value semantics.
    return max(1, int(magnitude.bit_length() * math.log10(2)) + 1)


def _int_decimal_text(value: int) -> str:
    """Exact int->decimal conversion independent of Python's global digit cap.

    Dify Code nodes can hand the SDK a native Python integer, and strict JSON
    can contain a decimal integer longer than CPython's default 4,300-digit
    conversion limit.  Changing the process-global limit would be unsafe in a
    shared plugin process, so use bounded base-1e9 chunks instead.
    """
    if value == 0:
        return "0"
    negative = value < 0
    number = -value if negative else value
    chunks: list[int] = []
    base = 1_000_000_000
    while number:
        number, chunk = divmod(number, base)
        chunks.append(chunk)
    text = str(chunks.pop()) + "".join(f"{chunk:09d}" for chunk in reversed(chunks))
    return "-" + text if negative else text


class TransportError(ValueError):
    pass


class _Pairs(list):
    """Marker used to distinguish decoded objects from decoded arrays."""


@dataclass
class ParseOutcome:
    value: Any = None
    error: str | None = None
    events: list[dict[str, str]] = field(default_factory=list)


@dataclass
class _Candidate:
    value: Any
    events: list[dict[str, str]]
    source: str
    score: int
    order: int


def _event(level: str, message: str, where: str = "/", suggestion: str = ""):
    return {"level": level, "where": where, "message": message, "suggestion": suggestion}


def _decode_bytes(value: bytes | bytearray | memoryview, events: list[dict[str, str]],
                  path: str = "") -> str:
    """Decode one native byte value using deterministic, BOM-aware rules."""
    raw = bytes(value)
    if len(raw) > MAX_SPEC_BYTES:
        raise TransportError(f"{path or '/'} 的 bytes 超过 {MAX_SPEC_BYTES // 1_000_000} MB 上限")
    bom_encodings = (
        (b"\x00\x00\xfe\xff", "utf-32", "UTF-32 BOM"),
        (b"\xff\xfe\x00\x00", "utf-32", "UTF-32 BOM"),
        (b"\xfe\xff", "utf-16", "UTF-16 BOM"),
        (b"\xff\xfe", "utf-16", "UTF-16 BOM"),
        (b"\xef\xbb\xbf", "utf-8-sig", "UTF-8 BOM"),
    )
    for prefix, encoding, label in bom_encodings:
        if raw.startswith(prefix):
            try:
                text = raw.decode(encoding)
                events.append(_event(
                    "INFO", f"bytes 已按 {label} 解码",
                    path or "/", "直接传 UTF-8 文本可减少传输转换",
                ))
                return text
            except UnicodeDecodeError:
                break
    try:
        text = raw.decode("utf-8-sig")
        events.append(_event("INFO", "bytes 输入已按 UTF-8 解码", path or "/"))
        return text
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
            events.append(_event(
                "WARN", "bytes 不是 UTF-8；已按 GB18030 兼容解码并继续",
                path or "/",
                "选择=GB18030；原因=可逆解码成功且覆盖常见中文系统编码；置信度=中",
            ))
            return text
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "replace")
            events.append(_event(
                "WARN", "bytes 无法按 UTF-8/UTF BOM/GB18030 完整解码；已用 U+FFFD 替换并继续",
                path or "/",
                "选择=UTF-8 replacement；原因=保证输出仍为有效 Unicode；置信度=低",
            ))
            return text


def _ptr(path: str, key: str | int) -> str:
    token = str(key).replace("~", "~0").replace("/", "~1")
    return (path or "") + "/" + token


def _short(value: Any, limit: int = 180) -> str:
    try:
        text = repr(value)
    except Exception:
        if isinstance(value, int) and not isinstance(value, bool):
            text = f"<{'-' if value < 0 else ''}int,约{_int_decimal_digits(value)}位>"
        else:
            text = f"<{type(value).__name__},无法安全显示>"
    return text if len(text) <= limit else text[:limit] + "…"


def _typed_fingerprint(value: Any):
    """JSON equality that never conflates bool/int/float."""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        # Decimal conversion is subject to CPython's process-wide digit cap.
        # A sign plus the exact magnitude bytes is collision-free and keeps a
        # 10,000-digit native integer auditable without changing that cap.
        magnitude = abs(value)
        raw = magnitude.to_bytes((magnitude.bit_length() + 7) // 8, "big")
        return ("int", value < 0, raw)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TransportError("JSON 不允许未归一的 NaN 或 Infinity")
        return ("float", value.hex())
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, list):
        return ("list", tuple(_typed_fingerprint(x) for x in value))
    if isinstance(value, dict):
        return ("dict", tuple(sorted((k, _typed_fingerprint(v)) for k, v in value.items())))
    raise TransportError(f"不支持的值类型：{type(value).__name__}")


def _convert_pairs(value: Any, path: str, events: list[dict[str, str]]):
    if isinstance(value, _Pairs):
        out: dict[str, Any] = {}
        for key, child in value:
            if not isinstance(key, str):
                raise TransportError(f"{path or '/'} 的对象键不是字符串")
            child_path = _ptr(path, key)
            converted = _convert_pairs(child, child_path, events)
            if key in out:
                same = _typed_fingerprint(out[key]) == _typed_fingerprint(converted)
                events.append(_event(
                    "INFO" if same else "WARN",
                    ("重复键取值相同，已合并；规则=last-wins" if same else
                     f"重复键取值冲突；候选=旧值 {_short(out[key])} / 新值 {_short(converted)}；"
                     "选择=最后一个值；规则=last-wins；原因=与主流 JSON 解析器及模板覆盖语义一致；置信度=高"),
                    child_path,
                    "删除重复键可消除本条审计",
                ))
            out[key] = converted
        return out
    if isinstance(value, list):
        return [_convert_pairs(x, _ptr(path, i), events) for i, x in enumerate(value)]
    return value


def _strict_loads(text: str):
    events: list[dict[str, str]] = []

    def integer(token: str):
        try:
            return int(token)
        except ValueError:
            # Preserve the exact lexical value as a decimal string.  The
            # field-aware semantic layer can then clamp a level/size/value or
            # keep it as visible text, instead of losing the entire PageSpec.
            digits = len(token.lstrip("+-"))
            events.append(_event(
                "WARN",
                f"JSON 整数约 {digits} 位，超过运行时直接整数转换上限；"
                "已保留为等值十进制字符串并交给字段容错层裁剪/显示；置信度=高",
                "/", "缩短该整数可消除本条审计",
            ))
            return token

    def constant(token: str):
        events.append(_event(
            "WARN", f"非标准数字 {token} 已归一为 null；原因=JSON 无有限等价值；置信度=高",
            "/", "把该值改成有限数字可保留数值语义",
        ))
        return None

    decoded = json.loads(
        text,
        object_pairs_hook=_Pairs,
        parse_int=integer,
        parse_constant=constant,
    )
    return _convert_pairs(decoded, "", events), events


def _validate_tree(value: Any):
    nodes = 0
    string_bytes = 0
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_TREE_NODES:
            raise TransportError(f"JSON 节点超过 {MAX_TREE_NODES} 上限")
        if depth > MAX_TREE_DEPTH:
            raise TransportError(f"JSON 结构深度超过 {MAX_TREE_DEPTH} 上限")
        if isinstance(current, str):
            string_bytes += len(current.encode("utf-8", "surrogatepass"))
            if string_bytes > MAX_SPEC_BYTES:
                raise TransportError(f"JSON 字符串内容超过 {MAX_SPEC_BYTES // 1_000_000} MB 上限")
        elif current is None or isinstance(current, (bool, int)):
            pass
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise TransportError("JSON 不允许未归一的 NaN 或 Infinity")
        elif isinstance(current, list):
            stack.extend((x, depth + 1) for x in current)
        elif isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    raise TransportError("对象键必须是字符串")
                string_bytes += len(key.encode("utf-8", "surrogatepass"))
                if string_bytes > MAX_SPEC_BYTES:
                    raise TransportError(f"JSON 字符串内容超过 {MAX_SPEC_BYTES // 1_000_000} MB 上限")
                stack.append((child, depth + 1))
        else:
            raise TransportError(f"JSON 中出现不支持的类型：{type(current).__name__}")


_LATEX_CONTROL_PREFIX = {
    "\b": "b", "\f": "f", "\n": "n", "\r": "r", "\t": "t",
}


def _repair_latex_control_text(text: str):
    """Recover JSON control escapes that spell an allowlisted LaTeX command."""
    out: list[str] = []
    commands: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        prefix = _LATEX_CONTROL_PREFIX.get(ch)
        if prefix is None:
            out.append(ch)
            i += 1
            continue
        end = i + 1
        while end < len(text) and text[end].isascii() and text[end].isalpha():
            end += 1
        command = prefix + text[i + 1:end]
        if command in _LATEX_CONTROL_COMMANDS:
            out.append("\\" + command)
            commands.append(command)
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out), commands


def _normalize_latex_fields(value: Any, events: list[dict[str, str]], path: str = ""):
    """Repair only ``latex`` values; keep all other strings byte-for-byte."""
    if isinstance(value, dict):
        for key, child in list(value.items()):
            child_path = _ptr(path, key)
            if key == "latex" and isinstance(child, str):
                repaired, commands = _repair_latex_control_text(child)
                if commands:
                    value[key] = repaired
                    events.append(_event(
                        "WARN",
                        "latex 字段中的 JSON 控制转义已按 LaTeX 命令恢复；"
                        f"命令={','.join('\\\\' + command for command in commands)}；"
                        "选择=LaTeX 字面反斜杠；原因=命令在冻结白名单内且字段名精确为 latex；置信度=高",
                        child_path,
                        "在 JSON 中把 LaTeX 反斜杠写成 \\\\ 可消除本条审计",
                    ))
            else:
                _normalize_latex_fields(child, events, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _normalize_latex_fields(child, events, _ptr(path, index))
    return value


_FENCE = re.compile(r"^\s*```(?:json|jsonc|javascript|js|pagespec)?\s*\n?(.*?)\n?```\s*$", re.I | re.S)
_META_KEYS = {
    "status", "success", "id", "request_id", "created_at", "elapsed_time",
    "task_id", "workflow_run_id", "metadata", "usage", "files",
}
_GENERIC_WRAPPERS = (
    "structured_output", "spec", "pagespec", "page_spec", "payload", "input", "inputs",
    "result", "results", "answer", "message", "response", "body", "content", "text",
    "value", "json", "tool_output", "tool_result", "data", "output", "outputs",
)

_DEEP_PAYLOAD_PRIORITY = {
    "structured_output": 150, "page_spec": 142, "pagespec": 141, "spec": 140,
    "output": 125, "result": 120, "answer": 118, "message": 116,
    "content": 114, "text": 112, "response": 110, "payload": 108,
    "value": 104, "json": 104, "tool_output": 104, "tool_result": 104,
}


def _strip_fence(text: str):
    match = _FENCE.match(text)
    return match.group(1).strip() if match else None


def _strip_jsonc_comments(text: str):
    out: list[str] = []
    quote = ""
    normalized_quote = False
    escaped = False
    i = 0
    changed = False
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            changed = True
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            changed = True
            i += 2
            while i + 1 < len(text) and text[i:i + 2] != "*/":
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            i = min(len(text), i + 2)
            continue
        out.append(ch)
        i += 1
    return "".join(out), changed


_PUNCT_TRANS = str.maketrans({
    "｛": "{", "｝": "}", "［": "[", "］": "]", "：": ":", "，": ",",
    "“": '"', "”": '"', "‘": "'", "’": "'",
})

_DIRTY_QUOTE_CLOSE = {
    '"': '"', "'": "'", "“": "”", "『": "』", "「": "」", "‘": "’", "`": "`",
}


def _normalize_punctuation(text: str):
    out = text.translate(_PUNCT_TRANS)
    return out, out != text


def _rewrite_value_undefined(text: str):
    """Rewrite only a bare ``undefined`` in a JSON value position to null.

    Keys, quoted strings, and identifier substrings are deliberately left
    alone.  This is the same deterministic compatibility rule used by the
    DOCX 0.0.19 transport decoder.
    """
    out: list[str] = []
    quote = ""
    i = 0
    changed = 0
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in _DIRTY_QUOTE_CLOSE:
            quote = _DIRTY_QUOTE_CLOSE[ch]
            out.append(ch)
            i += 1
            continue
        if not text.startswith("undefined", i):
            out.append(ch)
            i += 1
            continue

        before_word = text[i - 1] if i else ""
        after_index = i + len("undefined")
        after_word = text[after_index] if after_index < len(text) else ""
        if (before_word.isalnum() or before_word in "_$" or
                after_word.isalnum() or after_word in "_$"):
            out.append(ch)
            i += 1
            continue

        previous = i - 1
        while previous >= 0 and text[previous].isspace():
            previous -= 1
        following = after_index
        while following < len(text) and text[following].isspace():
            following += 1
        previous_char = text[previous] if previous >= 0 else ""
        following_char = text[following] if following < len(text) else ""
        value_start = previous_char in (":", "：", "[", "［", "【", ",", "，") or previous < 0
        value_end = following_char in ("", ",", "，", "}", "｝", "]", "］", "】", "/")
        if value_start and value_end and following_char not in (":", "："):
            out.append("null")
            i = after_index
            changed += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), bool(changed)


def _normalize_structural_delimiters(text: str):
    """Normalize non-ASCII JSON structure without touching string content."""
    out: list[str] = []
    quote = ""
    escaped = False
    changed = False
    opening_quotes = {"“": "”", "『": "』", "「": "」", "‘": "’", "`": "`"}
    punctuation = {
        "｛": "{", "｝": "}", "［": "[", "］": "]", "【": "[", "】": "]",
        "：": ":", "，": ",",
    }
    for ch in text:
        if quote:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == quote:
                out.append('"' if normalized_quote else ch)
                quote = ""
                if normalized_quote:
                    changed = True
                normalized_quote = False
                continue
            # A literal double quote inside a non-double quoted value is data.
            if normalized_quote and ch == '"':
                out.append('\\"')
                changed = True
                continue
            out.append(ch)
            continue
        if ch in "\u200b\u200c\u200d\ufeff":
            changed = True
            continue
        if ch in "\u00a0\u3000":
            out.append(" ")
            changed = True
            continue
        if ch in opening_quotes:
            out.append('"')
            quote = opening_quotes[ch]
            normalized_quote = True
            changed = True
            continue
        if ch in ('"', "'"):
            out.append(ch)
            quote = ch
            normalized_quote = False
            continue
        replacement = punctuation.get(ch)
        if replacement is not None:
            out.append(replacement)
            changed = True
            continue
        out.append(ch)
    return "".join(out), changed


def _repair_legacy_near_json(raw: str):
    """Bounded structural repair for common human/Template near-JSON.

    This is a PageSpec-scoped port of the DOCX 0.0.19 state-machine branch.
    It never executes input and only inserts syntax when the current container
    state requires it.  Correct JSON never reaches this function because the
    strict candidate is accepted first.
    """
    out: list[str] = []
    i, length = 0, len(raw)
    in_string = False
    quote = '"'
    role_key = False
    stack: list[str] = []
    expect = ["v"]  # k=key, c=colon, v=value, m=comma/end
    full_open = "“『「"
    full_close = "”』」"
    controls = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}
    whitespace = " \t\r\n\u00a0\u3000"
    zero_width = "\u200b\u200c\u200d\ufeff"

    def peek_struct(index: int):
        while index < length:
            current = raw[index]
            if current in whitespace or current in zero_width:
                index += 1
                continue
            if current == "\\" and index + 1 < length and raw[index + 1] in "nrt":
                index += 2
                continue
            return raw[index]
        return ""

    structural_after = (",", ":", "}", "]", "，", "：", "｝", "】", "］", "")

    def peek_bare_key(index: int):
        while index < length and (raw[index] in whitespace or raw[index] in zero_width):
            index += 1
        end = index
        while end < length and (raw[end].isalnum() or raw[end] == "_"):
            end += 1
        if end == index:
            return False
        while end < length and (raw[end] in whitespace or raw[end] in zero_width):
            end += 1
        return end < length and raw[end] in (":", "：")

    def need_comma():
        if expect and expect[-1] == "m":
            out.append(",")
            expect[-1] = "k" if stack and stack[-1] == "o" else "v"

    def after_value():
        if expect:
            expect[-1] = "m"

    while i < length:
        ch = raw[i]
        if in_string:
            if ch == "\\":
                nxt = raw[i + 1] if i + 1 < length else ""
                keep = False
                if nxt in '"\\/':
                    keep = True
                elif (nxt == "u" and i + 5 < length and
                      all(c in "0123456789abcdefABCDEF" for c in raw[i + 2:i + 6])):
                    keep = True
                elif nxt in "bfnrt":
                    following = raw[i + 2] if i + 2 < length else ""
                    keep = not (following.isascii() and following.isalpha())
                if keep:
                    out.append(ch)
                    if i + 1 < length:
                        out.append(raw[i + 1])
                        i += 2
                        continue
                    i += 1
                    continue
                # Invalid JSON slash inside a string is most usefully treated
                # as a literal slash (Windows path, regex, or LaTeX command).
                out.append("\\\\")
                i += 1
                continue
            closing = (
                (ch == '"' and quote == '"')
                or (quote in full_open and ch in full_close)
                or (quote == "'" and ch == "'")
                or (quote == "‘" and ch == "’")
                or (quote == "`" and ch == "`")
            )
            if closing:
                nxt = peek_struct(i + 1)
                if quote == '"' and nxt not in structural_after and nxt != '"' and not peek_bare_key(i + 1):
                    out.append('\\"')
                    i += 1
                    continue
                out.append('"')
                in_string = False
                if role_key:
                    if expect:
                        expect[-1] = "c"
                else:
                    after_value()
                i += 1
                continue
            if quote != '"' and ch == '"':
                out.append('\\"')
                i += 1
                continue
            if ord(ch) < 0x20:
                out.append(controls.get(ch, f"\\u{ord(ch):04x}"))
                i += 1
                continue
            out.append(ch)
            i += 1
            continue

        if ch in zero_width:
            i += 1
            continue
        if ch in "\u00a0\u3000":
            out.append(" ")
            i += 1
            continue
        if ch in " \t\r\n":
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < length and raw[i + 1] in "nrt":
            out.append({"n": "\n", "r": "\r", "t": "\t"}[raw[i + 1]])
            i += 2
            continue
        if ch == '"' or ch in full_open or ch in ("'", "‘", "`"):
            need_comma()
            role_key = bool(stack and stack[-1] == "o" and expect and expect[-1] == "k")
            in_string = True
            quote = ch
            out.append('"')
            i += 1
            continue
        if ch in ("{", "｛"):
            need_comma()
            out.append("{")
            stack.append("o")
            expect.append("k")
            i += 1
            continue
        if ch in ("[", "【", "［"):
            need_comma()
            out.append("[")
            stack.append("a")
            expect.append("v")
            i += 1
            continue
        if ch in ("}", "｝"):
            out.append("}")
            if stack:
                stack.pop()
            if len(expect) > 1:
                expect.pop()
            after_value()
            i += 1
            continue
        if ch in ("]", "】", "］"):
            out.append("]")
            if stack:
                stack.pop()
            if len(expect) > 1:
                expect.pop()
            after_value()
            i += 1
            continue
        if ch in (":", "："):
            out.append(":")
            if expect:
                expect[-1] = "v"
            i += 1
            continue
        if ch in (",", "，"):
            nxt = peek_struct(i + 1)
            if nxt in ("}", "]", "｝", "】", "］"):
                i += 1
                continue
            if nxt in (",", "，"):
                i += 1
                continue
            if expect and expect[-1] in ("k", "v"):
                i += 1
                continue
            out.append(",")
            if expect:
                expect[-1] = "k" if stack and stack[-1] == "o" else "v"
            i += 1
            continue
        if ch == "/" and i + 1 < length and raw[i + 1] == "/":
            while i < length and raw[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < length and raw[i + 1] == "*":
            end = raw.find("*/", i + 2)
            i = length if end < 0 else end + 2
            continue
        if ch in "+-.0123456789":
            start = i
            if raw[i] in "+-":
                i += 1
            if i + 1 < length and raw[i] == "0" and raw[i + 1] in "xX":
                end = i + 2
                while end < length and raw[end] in "0123456789abcdefABCDEF":
                    end += 1
                token = raw[start:end]
                try:
                    number = int(token, 16)
                    need_comma()
                    out.append(str(number))
                    after_value()
                    i = end
                    continue
                except ValueError:
                    i = start
            end = i
            while end < length and (raw[end].isdigit() or raw[end] in ".eE+-"):
                if raw[end] in "+-" and end > start and raw[end - 1] not in "eE":
                    break
                end += 1
            token = raw[start:end]
            body = token[1:] if token.startswith("+") else token
            if body.startswith("."):
                body = "0" + body
            if body.startswith("-."):
                body = "-0" + body[1:]
            if body.endswith("."):
                body += "0"
            need_comma()
            out.append(body)
            after_value()
            i = end
            continue
        if ch.isalpha() or ch == "_":
            end = i
            while end < length and (raw[end].isalnum() or raw[end] == "_"):
                end += 1
            word = raw[i:end]
            nxt = peek_struct(end)
            if nxt in (":", "："):
                need_comma()
                out.append(json.dumps(word, ensure_ascii=False))
                if expect:
                    expect[-1] = "c"
            elif word in ("true", "false", "null", "NaN", "Infinity"):
                need_comma()
                out.append(word)
                after_value()
            elif word in ("True", "False", "None"):
                need_comma()
                out.append({"True": "true", "False": "false", "None": "null"}[word])
                after_value()
            elif stack:
                need_comma()
                out.append(json.dumps(word, ensure_ascii=False))
                after_value()
            else:
                out.append(word)
            i = end
            continue
        out.append(ch)
        i += 1
    repaired = "".join(out)
    return repaired, repaired != raw


_KEY_TOKEN = r"(?:[A-Za-z_][\w.\-]*|[\u3400-\u9fff][\w.\-\u3400-\u9fff]*)"


def _repair_orphan_key_quote(text: str):
    """Repair one-sided quotes on an otherwise obvious object key.

    The opening ``{``/``,`` and following colon make this substantially less
    ambiguous than attempting to balance arbitrary quotes in body text.
    Values are never touched by this transform.
    """
    repaired, first = re.subn(
        r"([,{]\s*)(" + _KEY_TOKEN + r")\"(\s*:)",
        r'\1"\2"\3',
        text,
    )
    repaired, second = re.subn(
        r"([,{]\s*)\"(" + _KEY_TOKEN + r")(\s*:)",
        r'\1"\2"\3',
        repaired,
    )
    return repaired, bool(first or second)


def _repair_missing_key_colon(text: str):
    """Insert a colon only between an object-position quoted key and value."""
    value_start = r"(?=[\"'\[{{+\-\dA-Za-z_\u3400-\u9fff])"
    pattern = re.compile(
        r"([,{]\s*\"(?:\\.|[^\"\\])*\")(\s*)" + value_start
    )
    repaired, count = pattern.subn(r"\1:\2", text)
    return repaired, bool(count)


def _repair_orphan_value_quote(text: str):
    """Balance one missing quote when a scalar's delimiters are explicit."""
    member = r'([,{]\s*"(?:\\.|[^"\\])*"\s*:\s*)'
    # Missing opening quote: :正文" followed by the member/container end.
    repaired, first = re.subn(
        member + r"([^\"',}\]]+?)\"(\s*[,}\]])",
        r'\1"\2"\3',
        text,
    )
    # Missing closing quote: :"正文 followed by the member/container end.
    repaired, second = re.subn(
        member + r"\"([^\"',}\]]+?)(\s*[,}\]])",
        r'\1"\2"\3',
        repaired,
    )
    return repaired, bool(first or second)


def _escape_raw_string_controls(text: str):
    """Escape literal JSON control characters only while inside a string."""
    mapping = {"\n": r"\n", "\r": r"\r", "\t": r"\t", "\b": r"\b", "\f": r"\f"}
    out: list[str] = []
    quote = ""
    escaped = False
    changed = False
    for ch in text:
        if quote:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == quote:
                out.append(ch)
                quote = ""
                continue
            if ch in mapping:
                out.append(mapping[ch])
                changed = True
                continue
            if ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
                changed = True
                continue
            out.append(ch)
            continue
        out.append(ch)
        if ch in "\"'":
            quote = ch
    return "".join(out), changed


def _escape_invalid_string_backslashes(text: str):
    """Preserve common Windows/regex backslashes that are invalid JSON escapes."""
    out: list[str] = []
    quote = ""
    i = 0
    changed = False
    while i < len(text):
        ch = text[i]
        if not quote:
            out.append(ch)
            if ch in "\"'":
                quote = ch
            i += 1
            continue
        if ch == quote:
            out.append(ch)
            quote = ""
            i += 1
            continue
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(text):
            out.append("\\\\")
            changed = True
            i += 1
            continue
        token = text[i + 1]
        valid = token in '\"\\/bnrtf'
        if quote == "'" and token == "'":
            valid = True
        if token == "u":
            valid = i + 5 < len(text) and bool(re.fullmatch(r"[0-9a-fA-F]{4}", text[i + 2:i + 6]))
        if valid:
            out.extend((ch, token))
        else:
            out.extend(("\\", "\\", token))
            changed = True
        i += 2
    return "".join(out), changed


_BARE_VALUE = re.compile(
    r"([:\[,]\s*)([A-Za-z_\u3400-\u9fff][\w.\-/\u3400-\u9fff]*)(?=\s*[,}\]])"
)


def _quote_bare_values(text: str):
    """Quote JSON5-like simple bare values without touching strings/constants."""
    quote = ""
    escaped = False
    outside = [True] * (len(text) + 1)
    for i, ch in enumerate(text):
        outside[i] = not quote
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
    constants = {"true", "false", "null", "nan", "infinity"}
    pieces: list[str] = []
    last = 0
    changed = False
    for match in _BARE_VALUE.finditer(text):
        if not outside[match.start()] or match.group(2).lower() in constants:
            continue
        pieces.extend((text[last:match.start()], match.group(1),
                       json.dumps(match.group(2), ensure_ascii=False)))
        last = match.end()
        changed = True
    if not changed:
        return text, False
    pieces.append(text[last:])
    return "".join(pieces), True


def _quote_bare_free_text(text: str):
    """Quote an unquoted scalar value up to its object/array delimiter.

    This covers common human/LLM near-JSON such as ``text:中文 报告`` or
    ``text:2026-07-20 12:30``.  We only act after the ordinary bare-key/value
    and missing-comma repairs.  Anything containing another quote/container
    opener is left alone because its boundary is not unambiguous enough.
    """
    replacements: list[tuple[int, int, str]] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(text):
        ch = text[index]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            index += 1
            continue
        if ch in "\"'":
            quote = ch
            index += 1
            continue
        if ch != ":":
            index += 1
            continue
        start = index + 1
        while start < len(text) and text[start].isspace():
            start += 1
        if start >= len(text) or text[start] in "\"'[{":
            index += 1
            continue
        end = start
        while end < len(text) and text[end] not in ",}]":
            end += 1
        raw = text[start:end]
        token = raw.rstrip()
        if not token:
            index += 1
            continue
        lowered = token.lower()
        canonical_scalar = (
            lowered in {"true", "false", "null", "nan", "infinity"}
            or bool(_NUMBER_TEXT_FOR_FREE.fullmatch(token))
        )
        # Quotes/open containers indicate a likely missing comma or a nested
        # structure rather than one scalar.  Do not guess across that boundary.
        if canonical_scalar or any(mark in token for mark in ('"', "'", "[", "{")):
            index += 1
            continue
        replacements.append((start, start + len(token), json.dumps(token, ensure_ascii=False)))
        index = end
    if not replacements:
        return text, False
    out = text
    for start, end, replacement in reversed(replacements):
        out = out[:start] + replacement + out[end:]
    return out, True


_NUMBER_TEXT_FOR_FREE = re.compile(
    r"[+\-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?)"
)


def _json_constants_to_python(text: str):
    """Make lowercase JSON constants parseable in an otherwise Python repr."""
    out: list[str] = []
    quote = ""
    escaped = False
    i = 0
    changed = False
    mapping = {"true": "True", "false": "False", "null": "None",
               "NaN": "None", "Infinity": "None"}
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        matched = False
        for token, replacement in mapping.items():
            if text.startswith(token, i):
                before = text[i - 1] if i else ""
                after = text[i + len(token)] if i + len(token) < len(text) else ""
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    out.append(replacement)
                    i += len(token)
                    changed = True
                    matched = True
                    break
        if not matched:
            out.append(ch)
            i += 1
    return "".join(out), changed


def _decode_escape_layer(text: str):
    """Decode one Dify/template escaping layer without a codec or execution."""
    if not re.search(r"\\(?:[\"'\\/bnrtf]|u[0-9a-fA-F]{4})", text):
        return text, False
    mapping = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
               '"': '"', "'": "'", "\\": "\\", "/": "/"}
    out: list[str] = []
    i = 0
    changed = False
    while i < len(text):
        if text[i] != "\\" or i + 1 >= len(text):
            out.append(text[i])
            i += 1
            continue
        token = text[i + 1]
        if token in mapping:
            out.append(mapping[token])
            i += 2
            changed = True
        elif token == "u" and i + 5 < len(text) and re.fullmatch(r"[0-9a-fA-F]{4}", text[i + 2:i + 6]):
            out.append(chr(int(text[i + 2:i + 6], 16)))
            i += 6
            changed = True
        else:
            out.append(text[i])
            i += 1
    return "".join(out), changed


def _remove_trailing_commas(text: str):
    chars = list(text)
    quote = ""
    escaped = False
    changed = False
    for i, ch in enumerate(chars):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == ",":
            j = i + 1
            while j < len(chars) and chars[j].isspace():
                j += 1
            if j < len(chars) and chars[j] in "}]":
                chars[i] = ""
                changed = True
    return "".join(chars), changed


_BARE_KEY = re.compile(r"([{,]\s*)([A-Za-z_][\w.\-]*|[\u3400-\u9fff][\w.\-\u3400-\u9fff]*)(\s*:)")


def _quote_bare_keys(text: str):
    # Match positions are accepted only when the prefix is outside a string.
    quote = ""
    escaped = False
    outside = [True] * (len(text) + 1)
    for i, ch in enumerate(text):
        outside[i] = not quote
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
    pieces, last, changed = [], 0, False
    for match in _BARE_KEY.finditer(text):
        if not outside[match.start()]:
            continue
        pieces.extend((text[last:match.start()], match.group(1), json.dumps(match.group(2), ensure_ascii=False), match.group(3)))
        last = match.end()
        changed = True
    if not changed:
        return text, False
    pieces.append(text[last:])
    return "".join(pieces), True


def _protect_strings(text: str):
    strings: list[str] = []
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] not in "\"'":
            out.append(text[i])
            i += 1
            continue
        quote = text[i]
        start = i
        i += 1
        escaped = False
        while i < len(text):
            ch = text[i]
            i += 1
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                break
        strings.append(text[start:i])
        out.append(f"\x00{len(strings)-1}\x00")
    return "".join(out), strings


def _insert_obvious_commas(text: str):
    protected, strings = _protect_strings(text)
    value = r"(?:\}|\]|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?|\x00\d+\x00)"
    key = r"(?:\x00\d+\x00|[A-Za-z_\u3400-\u9fff][\w.\-\u3400-\u9fff]*)"
    repaired, count = re.subn(rf"({value})(\s*)({key})(\s*:)", r"\1,\2\3\4", protected)
    if not count:
        return text, False
    for i, token in enumerate(strings):
        repaired = repaired.replace(f"\x00{i}\x00", token)
    return repaired, True


def _complete_truncated(text: str):
    """Complete only an unfinished outer container; never synthesize content."""
    stripped = text.rstrip()
    if not stripped or stripped.lstrip()[:1] not in "[{":
        return text, False
    stack: list[str] = []
    quote = ""
    escaped = False
    for ch in stripped:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if not stack or (ch == "]" and stack[-1] != "[") or (ch == "}" and stack[-1] != "{"):
                return text, False
            stack.pop()
    if not quote and not stack:
        return text, False
    needed = len(stack) + (1 if quote else 0)
    if needed > MAX_COMPLETION_CLOSERS or (escaped and quote):
        return text, False
    base = stripped
    if quote:
        base += quote
    base = re.sub(r",\s*$", "", base)
    if re.search(r":\s*$", base):
        base += " null"
    base += "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    return base, True


def _balanced_fragments(text: str):
    out: list[str] = []
    start: int | None = None
    stack: list[str] = []
    quote = ""
    escaped = False
    for index, ch in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            if not stack:
                start = index
            stack.append(ch)
        elif ch in "]}" and stack:
            expected = "[" if ch == "]" else "{"
            if stack[-1] != expected:
                stack.clear()
                start = None
                continue
            stack.pop()
            if not stack and start is not None:
                out.append(text[start:index + 1])
                start = None
                if len(out) >= MAX_CANDIDATES:
                    break
    return out


def _python_literal(text: str):
    """Convert a restricted Python/Jinja repr using AST inspection only."""
    tree = ast.parse(text, mode="eval")
    allowed = (ast.Expression, ast.Dict, ast.List, ast.Tuple, ast.Constant,
               ast.UnaryOp, ast.USub, ast.UAdd, ast.Load)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise TransportError(f"Python 兼容写法含不允许的语法：{type(node).__name__}")
    events: list[dict[str, str]] = []

    def convert(node: ast.AST, path: str):
        if isinstance(node, ast.Constant):
            value = node.value
            if value is None or isinstance(value, (str, bool, int, float)):
                if isinstance(value, float) and not math.isfinite(value):
                    events.append(_event("WARN", "Python 非有限数字已归一为 null", path or "/"))
                    return None
                return value
            raise TransportError(f"Python 常量 {type(value).__name__} 不能转为 JSON")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            operand = convert(node.operand, path)
            if isinstance(operand, bool) or not isinstance(operand, (int, float)):
                raise TransportError("Python 正负号只能用于有限数字")
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, (ast.List, ast.Tuple)):
            if isinstance(node, ast.Tuple):
                events.append(_event("INFO", "Python tuple 已归一为 JSON 数组", path or "/"))
            return [convert(child, _ptr(path, i)) for i, child in enumerate(node.elts)]
        if isinstance(node, ast.Dict):
            out: dict[str, Any] = {}
            origins: dict[str, Any] = {}
            for key_node, value_node in zip(node.keys, node.values):
                if key_node is None:
                    raise TransportError("Python 兼容写法不允许 ** 展开")
                raw_key = convert(key_node, path)
                if isinstance(raw_key, str):
                    key = raw_key
                elif raw_key is None or isinstance(raw_key, (bool, int, float)):
                    # Match the native Code-node bridge: safe scalar keys have
                    # an exact, deterministic JSON string equivalent.  AST
                    # inspection keeps this conversion non-executable.
                    key = str(raw_key)
                    events.append(_event(
                        "WARN",
                        f"Python/Jinja repr 非字符串键已转为字符串；"
                        f"候选=[保留 {raw_key!r}, {key!r}]；选择={key!r}；"
                        "原因=JSON 对象键必须是字符串；置信度=高",
                        path or "/", "直接使用字符串键可消除本条审计",
                    ))
                else:
                    raise TransportError(f"{path or '/'} 的对象键 {type(raw_key).__name__} 无安全字符串等价")
                child = convert(value_node, _ptr(path, key))
                if key in out:
                    same = _typed_fingerprint(out[key]) == _typed_fingerprint(child)
                    if isinstance(origins[key], str) and isinstance(raw_key, str):
                        conflict = (
                            f"Python repr 重复键冲突；候选={_short(out[key])}/{_short(child)}；"
                            "选择=最后一个；原因=字典覆盖语义；置信度=高"
                        )
                    else:
                        conflict = (
                            f"Python repr 键字符串化后冲突；原始键={origins[key]!r}/{raw_key!r}；"
                            f"候选={_short(out[key])}/{_short(child)}；"
                            "选择=最后一个；原因=字典覆盖语义；置信度=高"
                        )
                    events.append(_event(
                        "INFO" if same else "WARN",
                        ("Python repr 重复键已合并；规则=last-wins" if same else
                         conflict),
                        _ptr(path, key),
                    ))
                out[key] = child
                origins[key] = raw_key
            return out
        raise TransportError(f"Python 兼容写法含不允许的语法：{type(node).__name__}")

    value = convert(tree.body, "")
    _validate_tree(value)
    return value, events


def _shape_score(value: Any) -> int:
    if isinstance(value, dict):
        score = 30
        if "version" in value:
            score += 180
        if isinstance(value.get("blocks"), list):
            score += 420
        if isinstance(value.get("type"), str):
            score += 240
        if any(key in value for key in ("doc", "profile", "内容块", "正文")):
            score += 60
        return score
    if isinstance(value, list):
        return 160 + (80 if value and all(isinstance(x, dict) for x in value) else 0)
    if isinstance(value, str):
        return 20
    return 5


def _text_candidates(text: str, *, nested: bool = False) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    seen_text: set[str] = set()
    order = 0

    def parse_text(candidate_text: str, source: str, score: int, events: list[dict[str, str]]):
        nonlocal order
        if len(candidates) >= MAX_CANDIDATES or candidate_text in seen_text:
            return False
        seen_text.add(candidate_text)
        try:
            value, parse_events = _strict_loads(candidate_text)
            candidates.append(_Candidate(value, events + parse_events, source, score, order))
            order += 1
            return True
        except Exception:
            return False

    seeds: list[tuple[str, str, int, list[dict[str, str]]]] = [(text, "strict", 1000, [])]
    # Exact Dify 1.7.1 runner corruption: a complete JSON string arrives with
    # one duplicated quote at each outer edge, e.g. ``""{\"version\":...}""``.
    # Remove exactly one quote per edge and let the ordinary strict bounded
    # string decoder prove the inner value.  Do not apply this to general text.
    if (len(text) >= 6 and text.startswith('""') and text.endswith('""') and
            text[2:3] in "{[" and "\\\"" in text):
        seeds.append((
            text[1:-1],
            "dify-1.7.1-duplicate-outer-quote",
            990,
            [_event(
                "WARN",
                "已恢复 Dify 1.7.1 runner 的重复外引号；"
                "选择=各删除一个外层引号；原因=内层可由严格 JSON 字符串链完整证明；置信度=高",
                "/",
                "模板节点直接输出 JSON 对象或只保留一层字符串包装可消除本条审计",
            )],
        ))
    fenced = _strip_fence(text)
    if fenced is not None:
        seeds.append((fenced, "markdown-fence", 960,
                      [_event("INFO", "已移除 Markdown JSON 代码围栏")]))
    entity = text
    entity_events: list[dict[str, str]] = []
    for layer in range(1, MAX_STRING_LAYERS + 1):
        decoded = html.unescape(entity)
        if decoded == entity:
            break
        entity = decoded
        entity_events = entity_events + [_event(
            "INFO", f"已解开第 {layer} 层 HTML 实体编码",
            "/", "直接传 JSON 字符可减少传输转换",
        )]
        seeds.append((entity, f"html-entity-{layer}", 930 - (layer - 1) * 16,
                      list(entity_events)))

    transforms = (
        ("value-undefined", 12, _rewrite_value_undefined,
         "值位置的裸 undefined 已归一为 null；字符串、键名与单词子串保持不变；置信度=高"),
        ("structural-delimiters", 18, _normalize_structural_delimiters,
         "已按 DOCX 0.0.19 同源规则归一结构区零宽/特殊空格、日式引号括号与反引号；"
         "字符串内容保持不变；置信度=高"),
        ("smart-fullwidth", 18, _normalize_punctuation, "智能引号/全角 JSON 标点已归一"),
        ("orphan-key-quote", 28, _repair_orphan_key_quote,
         "对象键缺少一侧引号；候选=正文中的引号/对象键引号；"
         "选择=补齐对象键引号；原因=键位于 { 或 , 后且紧接冒号；置信度=高"),
        ("missing-key-colon", 30, _repair_missing_key_colon,
         "对象键和值之间缺少冒号；候选=相邻正文/键值分隔；"
         "选择=补冒号；原因=带双引号的键位于对象成员起点且后接明显值；置信度=高"),
        ("orphan-value-quote", 30, _repair_orphan_value_quote,
         "标量值缺少一侧引号；候选=正文引号/字符串边界；"
         "选择=按紧邻的成员或容器分隔符补齐引号；"
         "原因=该分隔符给出了有界字符串终点；置信度=中"),
        ("raw-string-control", 20, _escape_raw_string_controls,
         "JSON/Python 字符串内的原始换行或控制字符已转为显式转义；候选=保留原字符/转义；"
         "选择=转义；原因=保持文本语义并恢复可解析结构；置信度=高"),
        ("invalid-backslash", 24, _escape_invalid_string_backslashes,
         "字符串内无效 JSON 反斜杠已按字面反斜杠保留；候选=转义前缀/字面字符；"
         "选择=字面字符；原因=Windows 路径与正则最常见；置信度=中"),
        ("escaped-layer", 22, _decode_escape_layer, "已解开一层模板/Dify 反斜杠转义"),
        ("jsonc-comments", 18, _strip_jsonc_comments, "已移除字符串外的 JSONC 注释"),
        ("trailing-comma", 10, _remove_trailing_commas, "已移除字符串外的尾随逗号"),
        ("bare-key", 22, _quote_bare_keys, "已为可识别的裸对象键补双引号"),
        ("bare-value", 32, _quote_bare_values,
         "简单裸值存在歧义；候选=标识符/字符串；选择=字符串；"
         "原因=PageSpec 的 type、枚举与正文值均为字符串；置信度=中"),
        ("missing-comma", 35, _insert_obvious_commas,
         "相邻对象成员缺少逗号；候选=连续正文/两个成员；选择=补逗号；"
         "原因=前值已完整结束且后项具有键加冒号结构；置信度=高"),
        ("bare-free-text", 38, _quote_bare_free_text,
         "未加引号的自由文本值存在歧义；候选=裸表达式/正文字符串；"
         "选择=到当前成员分隔符为止的正文字符串；"
         "原因=PageSpec 不执行表达式且该位置需要 JSON 值；置信度=中"),
        ("truncated", 45, _complete_truncated, "已有限补全未闭合字符串/容器；未猜测业务内容"),
        ("docx019-near-json", 60, _repair_legacy_near_json,
         "已按 DOCX 0.0.19 同源的有界状态机恢复近 JSON；"
         "覆盖结构零宽/特殊空格、日式引号括号、反引号、冗余逗号及值内裸引号；"
         "候选=保留原结构/有界语法恢复；选择=恢复；原因=恢复结果形成完整 PageSpec；置信度=中"),
    )
    for seed_text, seed_source, seed_score, seed_events in seeds:
        current, source, score, events = seed_text, seed_source, seed_score, list(seed_events)
        syntax_recovered = parse_text(current, source, score, events)
        # Try the original Python/Jinja representation before JSON-oriented
        # repairs quote bare keys.  ``None``/``True``/numeric object keys are
        # meaningful Python scalars and must reach _python_literal unchanged so
        # its explicit scalar-key normalization can audit them.
        initial_python_added = False
        try:
            value, literal_events = _python_literal(current)
            candidates.append(_Candidate(
                value,
                events + [_event("INFO", "受限 Python/Jinja 字面量已归一为 JSON；未执行代码")] + literal_events,
                source + "+python-literal", score - 28, order,
            ))
            order += 1
            initial_python_added = True
        except Exception:
            pass
        # Preserve the established Python/Jinja + lowercase JSON constant
        # route before the broader legacy state machine gets a chance to
        # produce an equivalent but less specifically audited candidate.
        initial_hybrid, initial_hybrid_changed = _json_constants_to_python(current)
        if initial_hybrid_changed:
            try:
                value, literal_events = _python_literal(initial_hybrid)
                candidates.append(_Candidate(
                    value,
                    events + [_event(
                        "WARN",
                        "Python/Jinja 引号与 JSON true/false/null 混用；候选=按 JSON/按 Python；"
                        "选择=Python 容器+JSON 常量等价转换；原因=不执行代码且保留值语义；置信度=高",
                    )] + literal_events,
                    source + "+hybrid-python-literal", score - 34, order,
                ))
                order += 1
            except Exception:
                pass
        for name, penalty, transform, message in transforms:
            # Once a seed is valid JSON, its escapes belong to JSON string
            # values (including legitimate nested string wrappers).  Let
            # ``_unwrap`` count those wrappers instead of decoding them here;
            # otherwise a ninth valid string wrapper could evade the exact
            # MAX_STRING_LAYERS boundary via the raw-escape recovery path.
            if syntax_recovered:
                break
            # A Template node can expose a JSON string without its outer
            # quotes.  Every serialization layer then remains as another
            # backslash layer (for example ``{\\\"version\\\": ...}``).
            # Decoding only once made two-or-more-layer values fall through as
            # ordinary body text.  Try every bounded intermediate result: the
            # first valid PageSpec is kept as a candidate, while the hard
            # 64-layer recovery ceiling still prevents an unbounded loop.
            attempts = MAX_STRING_LAYERS if name == "escaped-layer" else 1
            for layer in range(1, attempts + 1):
                repaired, changed = transform(current)
                if not changed:
                    break
                current = repaired
                source += "+" + name
                score -= penalty
                layer_message = message
                if name == "escaped-layer":
                    layer_message = f"已解开第 {layer} 层模板/Dify 反斜杠转义"
                events = events + [_event(
                    "WARN" if name in {"value-undefined", "structural-delimiters", "docx019-near-json",
                                       "orphan-key-quote", "missing-key-colon", "orphan-value-quote",
                                       "raw-string-control", "invalid-backslash", "bare-value",
                                       "missing-comma", "bare-free-text", "truncated"} else "INFO",
                    layer_message,
                )]
                if parse_text(current, source, score, events):
                    syntax_recovered = True
                    break
        if current != seed_text or not initial_python_added:
            try:
                value, literal_events = _python_literal(current)
                candidates.append(_Candidate(
                    value,
                    events + [_event("INFO", "受限 Python/Jinja 字面量已归一为 JSON；未执行代码")] + literal_events,
                    source + "+python-literal", score - 28, order,
                ))
                order += 1
            except Exception:
                pass
        python_compatible, hybrid_changed = _json_constants_to_python(current)
        if hybrid_changed:
            try:
                value, literal_events = _python_literal(python_compatible)
                candidates.append(_Candidate(
                    value,
                    events + [_event(
                        "WARN",
                        "Python/Jinja 引号与 JSON true/false/null 混用；候选=按 JSON/按 Python；"
                        "选择=Python 容器+JSON 常量等价转换；原因=不执行代码且保留值语义；置信度=高",
                    )] + literal_events,
                    source + "+hybrid-python-literal", score - 34, order,
                ))
                order += 1
            except Exception:
                pass

    # Mixed prose: complete containers must enter the *same* bounded repair
    # pipeline.  Previously only strict JSON fragments were tried, so a human
    # sentence containing a Python/Jinja repr or bare-key JSON was silently
    # downgraded to one large text block.
    if not nested:
        for index, fragment in enumerate(_balanced_fragments(fenced if fenced is not None else text)):
            if fragment in seen_text:
                continue
            extraction_event = _event(
                "WARN", f"已从混合说明文字中提取第 {index + 1} 个完整 JSON/Python 容器"
            )
            base_score = 760 - min(index, 20)
            for recovered in _text_candidates(fragment, nested=True):
                if len(candidates) >= MAX_CANDIDATES:
                    break
                # Nested strict starts at 1000; retain its relative repair
                # penalties but keep prose extraction below a direct payload.
                candidates.append(_Candidate(
                    recovered.value,
                    [extraction_event] + recovered.events,
                    f"balanced-fragment-{index}+{recovered.source}",
                    base_score + recovered.score - 1000,
                    order,
                ))
                order += 1
    return candidates[:MAX_CANDIDATES]


def _get_path(value: Any, path: tuple[str | int, ...]):
    current: Any = value
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, list) or not 0 <= key < len(current):
                return False, None
            current = current[key]
        else:
            if not isinstance(current, dict) or key not in current:
                return False, None
            current = current[key]
    return True, current


def _path_text(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


_KNOWN_ENVELOPE_KEYS = set(_GENERIC_WRAPPERS) | set(_DEEP_PAYLOAD_PRIORITY) | {
    "outputs", "data", "body", "choices",
}


def _wrapper_guess_details(value: dict, path: tuple[str | int, ...]):
    """Return unknown path tokens and siblings discarded by an unwrap.

    A standard, single ``output``-style envelope is a transport conversion and
    can remain INFO.  An arbitrary field name or any sibling that is dropped
    is a real guess: the output may be correct, but the user must see a WARN in
    the generated HTML audit rather than a misleading “0 warnings” summary.
    """
    unknown = [str(part) for part in path
               if isinstance(part, str) and part not in _KNOWN_ENVELOPE_KEYS]
    ignored: list[str] = []
    current: Any = value
    walked: tuple[str | int, ...] = ()
    for part in path:
        if isinstance(current, dict) and isinstance(part, str) and part in current:
            for sibling in current:
                if sibling != part:
                    ignored.append(_path_text(walked + (sibling,)))
            current = current[part]
            walked += (part,)
            continue
        if isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            if len(current) > 1:
                ignored.append(_path_text(walked + ("其他数组项目",)))
            current = current[part]
            walked += (part,)
            continue
        break
    return sorted(set(unknown)), sorted(set(ignored))


def _looks_pagespec_root(value: dict) -> bool:
    if "blocks" in value or "doc" in value or "profile" in value:
        return True
    strong_wrappers = set(_DEEP_PAYLOAD_PRIORITY) | {
        "outputs", "data", "body", "response", "tool_output", "tool_result",
    }
    if "type" in value and not (strong_wrappers & set(value)):
        return True
    if "version" in value and not (strong_wrappers & set(value)):
        return True
    return False


# If a top-level ``type`` object contains one of these fields it is much more
# likely to be a single PageSpec block than an API event envelope.  This keeps
# literal JSON shown by a text/code block from being stolen as a transport
# payload, while still allowing ``{"type":"workflow_finished",
# "futurePayload": ...}`` to recover the complete PageSpec below it.
_BLOCK_ROOT_HINTS = {
    "text", "title", "level", "style", "items", "children", "columns", "rows",
    "code", "language", "formula", "slot", "alt", "caption", "kind", "series",
    "nodes", "edges", "headers", "value", "label", "fallback",
}
_METADATA_TYPE = re.compile(
    r"(?:api|event|workflow|message|response|completion|finished|result|output|tool)", re.I
)


def _typed_object_is_block_root(value: dict) -> bool:
    type_value = value.get("type")
    return (
        isinstance(type_value, str)
        and bool(_BLOCK_ROOT_HINTS & set(value))
        and not _METADATA_TYPE.search(type_value)
    )


def _root_may_be_metadata_envelope(value: dict, options) -> bool:
    """Whether metadata-looking root keys should yield to a deeper PageSpec.

    ``version`` and ``type`` are common both in PageSpec and in Dify/API event
    envelopes.  A complete PageSpec root (blocks/doc/profile) always wins.  An
    incomplete metadata-looking root yields only when a bounded deep scan has
    found a substantially PageSpec-shaped child.  Unknown envelope field names
    are deliberately supported because Code/Template nodes let users choose
    their output variable names.
    """
    if any(key in value for key in ("blocks", "doc", "profile")):
        return False
    if _typed_object_is_block_root(value):
        return False
    return any(_payload_quality(payload) >= 55 for _, _, payload in options)


def _payload_quality(value: Any) -> int:
    if isinstance(value, dict):
        if isinstance(value.get("blocks"), list):
            return 90
        if "doc" in value or "profile" in value:
            return 70
        if isinstance(value.get("type"), str):
            return 55
        if set(value) & (set(_GENERIC_WRAPPERS) | {"outputs", "data"}):
            return 35
        return 0
    if isinstance(value, list):
        if len(value) == 1:
            return 35 + min(30, _payload_quality(value[0]))
        if value and all(isinstance(item, dict) for item in value):
            # A multi-block array is also a valid tolerant PageSpec payload;
            # generic API ``choices`` arrays normally lack a block ``type``
            # on every item and therefore retain the lower envelope score.
            return 65 if all(isinstance(item.get("type"), str) for item in value) else 30
        return 0
    if isinstance(value, str):
        stripped = value.strip().lstrip("\ufeff")
        if not stripped or (stripped[:1] not in "[{\"'" and "\\\"" not in stripped):
            return 0
        recovered = _recover_inner_string(stripped)
        return 15 + _payload_quality(recovered.value) if recovered is not None else 0
    return 0


def _deep_wrapper_options(value: dict):
    """Find PageSpec-like payloads under unknown historical envelopes."""
    options: list[tuple[int, tuple[str | int, ...], Any]] = []
    stack: list[tuple[Any, tuple[str | int, ...], int]] = [(value, (), 0)]
    visited = 0
    while stack and visited < MAX_TREE_NODES and len(options) < MAX_CANDIDATES:
        current, path, depth = stack.pop()
        visited += 1
        if depth >= MAX_WRAPPER_LAYERS:
            continue
        if isinstance(current, dict):
            items = list(current.items())
            for key, child in reversed(items):
                child_path = path + (key,)
                quality = _payload_quality(child)
                key_priority = _DEEP_PAYLOAD_PRIORITY.get(key, 0)
                if key_priority and quality:
                    options.append((key_priority + quality - depth * 3, child_path, child))
                elif quality >= 55 and key not in _META_KEYS:
                    options.append((100 + quality - depth * 3, child_path, child))
                if key not in _META_KEYS and isinstance(child, (dict, list)):
                    stack.append((child, child_path, depth + 1))
        elif isinstance(current, list):
            # Dify/OpenAI message envelopes normally contain one choice/content
            # item.  Eight is enough for recovery while preventing wide scans.
            for index in reversed(range(min(len(current), 8))):
                child = current[index]
                child_path = path + (index,)
                quality = _payload_quality(child)
                if quality >= 55:
                    options.append((96 + quality - depth * 3, child_path, child))
                if isinstance(child, (dict, list)):
                    stack.append((child, child_path, depth + 1))
    return options[:MAX_CANDIDATES]


def _wrapper_options(value: dict):
    # Stable order is part of the public recovery contract.
    preferred = (
        (150, ("structured_output",)),
        (149, ("outputs", "structured_output")),
        (148, ("data", "outputs", "structured_output")),
        (147, ("data", "structured_output")),
        (120, ("output",)),
        (119, ("outputs", "output")),
        (118, ("data", "outputs", "output")),
        (117, ("data", "output")),
        (116, ("result", "output")),
        (115, ("body", "data", "outputs", "output")),
        (114, ("body", "data", "output")),
        (113, ("outputs", "text")),
        (112, ("data", "result")),
        (111, ("message", "content")),
        (110, ("data", "message", "content")),
        (109, ("choices", 0, "message", "content")),
    )
    options: list[tuple[int, tuple[str | int, ...], Any]] = []
    for priority, path in preferred:
        exists, payload = _get_path(value, path)
        if exists:
            options.append((priority, path, payload))
    for index, key in enumerate(_GENERIC_WRAPPERS):
        if key in value:
            options.append((90 - index, (key,), value[key]))
    if len(value) == 1:
        key = next(iter(value))
        options.append((50, (key,), value[key]))
    options.extend(_deep_wrapper_options(value))
    # De-duplicate paths, retaining the highest priority.
    by_path: dict[tuple[str | int, ...], tuple[int, tuple[str | int, ...], Any]] = {}
    for item in options:
        if item[1] not in by_path or item[0] > by_path[item[1]][0]:
            by_path[item[1]] = item
    return sorted(by_path.values(), key=lambda x: (-x[0], x[1]))


def _choose(candidates: list[_Candidate], events: list[dict[str, str]] | None = None):
    if not candidates:
        raise TransportError("没有可恢复候选")
    ranked = sorted(candidates, key=lambda c: (-(c.score + _shape_score(c.value)), c.order, c.source))
    selected = ranked[0]
    distinct: list[_Candidate] = []
    seen = set()
    for candidate in ranked:
        fp = _typed_fingerprint(candidate.value)
        if fp not in seen:
            seen.add(fp)
            distinct.append(candidate)
    if len(distinct) > 1:
        top_score = selected.score + _shape_score(selected.value)
        second_score = distinct[1].score + _shape_score(distinct[1].value)
        gap = top_score - second_score
        confidence = "高" if gap >= 120 else "中" if gap >= 40 else "低"
        audit = _event(
            "WARN",
            "输入存在多个可恢复结果；候选=" + ", ".join(
                f"{c.source}(分数={c.score + _shape_score(c.value)})" for c in distinct[:6]
            ) + f"；选择={selected.source}；原因=PageSpec 结构完整度、修复量和来源优先级综合最高；置信度={confidence}",
            "/", "若猜测不符合预期，可直接传严格 JSON；本次仍继续生成",
        )
        selected.events = selected.events + [audit]
        if events is not None:
            events.append(audit)
    return selected


def _recover_inner_string(text: str):
    candidates = _text_candidates(text, nested=True)
    if not candidates:
        return None
    return _choose(candidates)


def _unwrap(value: Any, events: list[dict[str, str]]):
    string_layers = 0
    wrapper_layers = 0
    while True:
        if isinstance(value, str):
            stripped = value.strip().lstrip("\ufeff")
            if not stripped or (stripped[0] not in "[{\"'" and "\\\"" not in stripped):
                break
            recovered = _recover_inner_string(stripped)
            if recovered is None:
                break
            if string_layers >= MAX_STRING_LAYERS:
                raise TransportError(f"完整 JSON 字符串包装层数超过 {MAX_STRING_LAYERS} 上限")
            value = recovered.value
            string_layers += 1
            events.extend(recovered.events)
            events.append(_event(
                "INFO", f"已解开第 {string_layers} 层 JSON 字符串包装",
                "/", "直接传 PageSpec 对象可减少包装",
            ))
            continue
        if isinstance(value, list) and len(value) == 1:
            child = value[0]
            # A one-block PageSpec array remains a blocks array.  Everything
            # else is a common transport singleton and is safe to unwrap.
            if isinstance(child, dict) and isinstance(child.get("type"), str) and not _looks_pagespec_root(child):
                break
            if isinstance(child, dict) and set(child) <= {"type", "fallback"}:
                break
            if wrapper_layers >= MAX_WRAPPER_LAYERS:
                raise TransportError(f"对象/数组包装层数超过 {MAX_WRAPPER_LAYERS} 上限")
            value = child
            wrapper_layers += 1
            events.append(_event(
                "INFO", "已解开 singleton 数组包装；候选=blocks数组/传输包装；"
                "选择=传输包装；原因=唯一项目本身是完整 PageSpec/可恢复载荷；置信度=高",
                "/0", "直接传 PageSpec 对象可减少包装",
            ))
            continue
        if isinstance(value, dict):
            options = _wrapper_options(value)
            if _typed_object_is_block_root(value):
                break
            root_like = _looks_pagespec_root(value)
            if root_like and not _root_may_be_metadata_envelope(value, options):
                break
            if not options:
                break
            priority, path, payload = options[0]
            if wrapper_layers + len(path) > MAX_WRAPPER_LAYERS:
                raise TransportError(f"对象包装层数超过 {MAX_WRAPPER_LAYERS} 上限")
            distinct = []
            seen = set()
            for option_priority, option_path, option_payload in options:
                # ``data`` and ``data.outputs.output`` are the same wrapper
                # branch, not two competing payloads.  Only independent
                # branches should trigger the multiple-payload warning.
                shared = min(len(path), len(option_path))
                if option_path != path and path[:shared] == option_path[:shared]:
                    continue
                try:
                    fp = _typed_fingerprint(option_payload)
                except Exception:
                    continue
                if fp not in seen:
                    seen.add(fp)
                    distinct.append((option_priority, option_path))
            if len(distinct) > 1:
                events.append(_event(
                    "WARN",
                    "包装对象含多个不同载荷；候选=" + ", ".join(_path_text(p) for _, p in distinct[:8]) +
                    f"；选择={_path_text(path)}；原因=固定 Dify 传输优先级、"
                    "structured_output 优先及 PageSpec 结构完整度综合最高；置信度=高",
                    "/" + "/".join(str(part) for part in path),
                    "本次已继续处理，删除无关载荷可消除警告",
                ))
            unknown_tokens, ignored_paths = _wrapper_guess_details(value, path)
            value = payload
            wrapper_layers += len(path)
            where = "/" + "/".join(str(part) for part in path)
            if unknown_tokens or ignored_paths:
                reasons = []
                if unknown_tokens:
                    reasons.append("路径含未知包装字段 " + ", ".join(unknown_tokens))
                if ignored_paths:
                    reasons.append("提取载荷会忽略外层字段/项目 " + ", ".join(ignored_paths[:12]))
                confidence = "高" if _payload_quality(payload) >= 70 else "中"
                events.append(_event(
                    "WARN",
                    f"包装路径 {_path_text(path)} 的解码需要猜测；候选=保留外层对象 / 提取 {_path_text(path)}；"
                    f"选择=提取 {_path_text(path)}；原因=" + "；".join(reasons) +
                    f"，且所选载荷的 PageSpec 结构完整度最高；置信度={confidence}",
                    where,
                    "本次已继续生成；若要消除警告，请直接传 PageSpec 或仅使用标准单一 output 包装",
                ))
            else:
                events.append(_event(
                    "INFO", f"已解开标准单一包装路径 {_path_text(path)}（优先级 {priority}）",
                    where, "直接传 PageSpec 根对象可减少包装",
                ))
            continue
        break
    return value


def _canonicalize_root(value: Any, events: list[dict[str, str]]):
    if value is None:
        events.append(_event("WARN", "输入为 null/None；已生成可见提示块而非中止", "/", "提供内容可替换提示块"))
        return {"version": 1, "blocks": [{"type": "callout", "style": "warning",
                                             "title": "输入为空", "text": "未收到可渲染内容。"}]}
    if isinstance(value, list):
        events.append(_event("INFO", "顶层数组已按 blocks 数组包装", "/"))
        return {"version": 1, "blocks": value}
    if isinstance(value, dict):
        if {"version", "blocks", "doc", "profile"} & set(value):
            return value
        events.append(_event(
            "WARN", "顶层对象缺少 PageSpec 信封；已猜测为单个内容块并包装到 blocks",
            "/", "选择原因=对象字段更像内容块；置信度=中",
        ))
        return {"version": 1, "blocks": [value]}
    if isinstance(value, (str, int, float, bool)):
        events.append(_event(
            "WARN", f"顶层 {type(value).__name__} 已猜测为文本块；置信度=高",
            "/", "如需其他块类型，请提供对象字段",
        ))
        return {"version": 1, "blocks": [{"type": "text", "text": str(value)}]}
    raise TransportError(f"不支持的顶层类型：{type(value).__name__}")


def _normalize_native(value: Any, events: list[dict[str, str]], path: str = "",
                      depth: int = 0, state: dict[str, int] | None = None):
    """Convert safe native Dify/Python containers to the JSON value model.

    Dify normally serialises these values before invoking a string parameter,
    but Code-node tests and older plugin bridges can hand the SDK a native
    tuple, byte string, or dictionary with numeric keys.  They are data, not a
    reason to abort the whole page.  Conversion remains bounded and never
    invokes user-defined methods.
    """
    if state is None:
        state = {"nodes": 0}
    state["nodes"] += 1
    if state["nodes"] > MAX_TREE_NODES:
        raise TransportError(f"native JSON 节点超过 {MAX_TREE_NODES} 上限")
    if depth > MAX_TREE_DEPTH:
        raise TransportError(f"native JSON 结构深度超过 {MAX_TREE_DEPTH} 上限")
    if isinstance(value, float) and not math.isfinite(value):
        events.append(_event(
            "WARN", f"native 非有限数字 {value!r} 已归一为 null；置信度=高",
            path or "/", "改用有限数字可保留数值语义",
        ))
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _decode_bytes(value, events, path)
    if isinstance(value, tuple):
        events.append(_event(
            "INFO", "native tuple 已确定性归一为 JSON 数组",
            path or "/", "选择=list；原因=保持项目顺序；置信度=高",
        ))
        value = list(value)
    if isinstance(value, list):
        return [_normalize_native(item, events, _ptr(path, i), depth + 1, state)
                for i, item in enumerate(value)]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        origins: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str):
                normalized_key = key
            elif isinstance(key, (bytes, bytearray, memoryview)):
                normalized_key = _decode_bytes(key, events, path or "/")
                events.append(_event(
                    "WARN", f"native bytes 键已解码为字符串 {normalized_key!r}；置信度=高",
                    path or "/", "直接返回字符串键可消除转换",
                ))
            elif key is None or isinstance(key, (bool, int, float)):
                if isinstance(key, int) and not isinstance(key, bool):
                    normalized_key = _int_decimal_text(key)
                else:
                    normalized_key = str(key)
                events.append(_event(
                    "WARN",
                    f"native 非字符串键 {_short(key)} 已转为字符串；"
                    f"候选=[保留原类型, {_short(normalized_key)}]；"
                    f"选择={normalized_key!r}；原因=JSON 对象键必须是字符串；置信度=高",
                    path or "/", "在 Code 节点中直接返回字符串键可消除转换",
                ))
            else:
                raise TransportError(f"{path or '/'} 的 native 对象键 {type(key).__name__} 无安全字符串等价")
            child_path = _ptr(path, normalized_key)
            normalized_item = _normalize_native(item, events, child_path, depth + 1, state)
            if normalized_key in out:
                old_key = origins[normalized_key]
                same = _typed_fingerprint(out[normalized_key]) == _typed_fingerprint(normalized_item)
                events.append(_event(
                    "INFO" if same else "WARN",
                    ("native 键字符串化后取值相同，已合并" if same else
                     f"native 键字符串化后冲突；候选={_short(old_key)}/{_short(key)}；"
                     f"选择={_short(key)} 的值；规则=last-wins；原因=与 JSON 重复键规则一致；置信度=高"),
                    child_path, "使用唯一字符串键可消除本条审计",
                ))
            out[normalized_key] = normalized_item
            origins[normalized_key] = key
        return out
    return value


def _finish(value: Any, events: list[dict[str, str]]):
    value = _normalize_native(value, events)
    value = _unwrap(value, events)
    value = _canonicalize_root(value, events)
    value = _normalize_latex_fields(value, events)
    _validate_tree(value)
    return ParseOutcome(value=value, events=events)


def _looks_like_json_intent(text: str):
    """Whether failed text visibly attempts to be JSON/PageSpec.

    Plain prose remains a useful text block.  A broken JSON-looking document,
    however, must never silently become a page showing escaped source code.
    """
    stripped = text.strip().lstrip("\ufeff\u200b\u200c\u200d")
    if not stripped:
        return False
    if stripped.startswith(("{", "[", "｛", "［", "【", "```json", "```pagespec")):
        return True
    if re.match(r'^["\'`“‘『「]{1,4}[\\]*[{\[｛［【]', stripped):
        return True
    lowered = stripped.lower()
    structural = any(mark in stripped for mark in ("{", "[", "｛", "［", "【", "&quot;"))
    pagespec_key = bool(re.search(
        r'(?:["\'`“‘『「]|\\")?(?:version|blocks|pagespec|page_spec)'
        r'(?:["\'`”’』」]|\\")?\s*(?:[:：]|&(?:amp;)*quot;)',
        lowered,
    ))
    return structural and pagespec_key


def parse_spec(raw: Any) -> ParseOutcome:
    """Parse every transport form into a PageSpec-shaped object.

    Only hard resource/safety bounds return ``error``.  Syntax ambiguity is a
    recoverable condition and is always resolved by the deterministic scorer.
    """
    prefix_events: list[dict[str, str]] = []
    if raw is None:
        return _finish(None, prefix_events)
    if isinstance(raw, (bytes, bytearray, memoryview)):
        try:
            raw = _decode_bytes(raw, prefix_events, "/")
        except Exception as exc:
            return ParseOutcome(error=str(exc), events=prefix_events)
    if isinstance(raw, (dict, list)):
        try:
            return _finish(raw, prefix_events)
        except Exception as exc:
            return ParseOutcome(error=str(exc), events=prefix_events)
    if not isinstance(raw, str):
        try:
            return _finish(raw, prefix_events)
        except Exception as exc:
            return ParseOutcome(error=str(exc), events=prefix_events)
    if not raw.strip():
        prefix_events.append(_event("WARN", "空字符串已按空输入处理", "/"))
        return _finish(None, prefix_events)
    if "\x00" in raw:
        count = raw.count("\x00")
        raw = raw.replace("\x00", "")
        prefix_events.append(_event(
            "WARN", f"已移除 {count} 个 NUL 填充字符并继续解析",
            "/", "选择=删除 NUL；原因=JSON 文本不能包含原始 NUL，常见于界面/二进制复制；置信度=高",
        ))
    try:
        size = len(raw.encode("utf-8", "surrogatepass"))
    except Exception:
        size = len(raw) * 4
    if size > MAX_SPEC_BYTES:
        return ParseOutcome(error=f"spec 超过 {MAX_SPEC_BYTES // 1_000_000} MB 上限", events=prefix_events)

    original = raw.strip().lstrip("\ufeff")
    if original != raw.strip():
        prefix_events.append(_event("INFO", "已移除 UTF-8 BOM", "/"))

    candidates = _text_candidates(original)
    if not candidates:
        if _looks_like_json_intent(original):
            message = (
                "输入看起来像 PageSpec/JSON，但在有界容错后仍无法恢复；"
                "为防止把 JSON 源码静默当成正文，已显式停止本次解析"
            )
            prefix_events.append(_event(
                "WARN", message, "/",
                "检查错误位置附近的引号/括号；其余已知 Dify 包装与近 JSON 会自动修复",
            ))
            return ParseOutcome(error=message, events=prefix_events)
        # A human-entered plain string is useful content, not a syntax offence.
        prefix_events.append(_event(
            "WARN", "未识别为 JSON；已猜测整段输入是正文文本并继续",
            "/", "选择=文本块；原因=没有安全可恢复的 JSON 容器；置信度=高",
        ))
        return _finish(original, prefix_events)

    resolved: list[_Candidate] = []
    errors: list[str] = []
    for candidate in candidates:
        events = prefix_events + list(candidate.events)
        try:
            value = _unwrap(candidate.value, events)
            value = _canonicalize_root(value, events)
            value = _normalize_latex_fields(value, events)
            _validate_tree(value)
            resolved.append(_Candidate(value, events, candidate.source, candidate.score, candidate.order))
        except TransportError as exc:
            errors.append(str(exc))
    if not resolved:
        return ParseOutcome(error=(errors[0] if errors else "输入超过安全恢复边界"), events=prefix_events)
    selected = _choose(resolved)
    return ParseOutcome(value=selected.value, events=selected.events)
