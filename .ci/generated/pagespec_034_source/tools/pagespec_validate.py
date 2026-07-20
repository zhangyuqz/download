# -*- coding: utf-8 -*-
"""Canonicalisation and semantic validation for the closed PageSpec v1 DSL.

JSON Schema remains the machine-readable canonical contract. This module is the
runtime tolerance layer: it deterministically normalises recoverable variants,
including ambiguous ones, records every choice, validates cross-field
semantics, and turns an impossible block into an internal error/fallback block
instead of silently dropping it.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import json
import math
import re
from typing import Any, Callable


MAX_BLOCKS = 800
MAX_DEPTH = 6
MAX_TEXT = 200_000
MAX_MARKDOWN = 200_000
MAX_CODE = 200_000
MAX_MERMAID = 20_000
MAX_TABLE_ROWS = 3_000
MAX_TABLE_COLUMNS = 50
MAX_TABLE_CELLS = 50_000
MAX_CHART_POINTS = 20_000
MAX_GRAPH_NODES = 2_000
MAX_GRAPH_EDGES = 4_000
MAX_WORDS = 2_000
MAX_EVENTS = 5_000
MAX_QR_CHARS = 2_000
MAX_BARCODE_CHARS = 256
MAX_REPORTABLE_VALUE = 120


class ValidationError(ValueError):
    pass


class BlockError(ValueError):
    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(message)
        self.suggestion = suggestion


_TYPE_ALIASES = {
    "h": "heading", "title": "heading", "标题": "heading",
    "p": "text", "paragraph": "text", "段落": "text", "文本": "text",
    "md": "markdown", "note": "callout", "alert": "callout", "提示": "callout",
    "引用": "quote", "键值": "kv", "标签": "tags", "代码": "code", "公式": "formula",
    "分隔线": "divider", "stats": "stat_row", "metric": "stat_row", "指标": "stat_row",
    "表格": "table", "chart_bar": "chart", "图表": "chart", "词云": "wordcloud",
    "graph_dagre": "graph", "关系图": "graph", "flow": "mermaid", "流程图": "mermaid",
    "时间线": "timeline", "进度": "progress", "日历": "calendar",
    "img": "image", "图片": "image", "图册": "gallery", "qr": "qrcode",
    "二维码": "qrcode", "条形码": "barcode", "章节": "section", "卡片": "card",
    "分栏": "columns", "标签页": "tabs", "折叠": "collapse",
    "catalog": "catalog_demo", "catalogue": "catalog_demo", "全库测试": "catalog_demo",
    "库验证": "catalog_demo", "目录验证": "catalog_demo",
}

_KNOWN_TYPES = {
    "heading", "text", "markdown", "callout", "quote", "kv", "tags", "code", "formula",
    "divider", "stat_row", "table", "chart", "wordcloud", "graph", "mermaid", "timeline",
    "progress", "calendar", "image", "gallery", "qrcode", "barcode", "section", "card",
    "columns", "tabs", "collapse", "catalog_demo",
}

_COMMON_ALIASES = {
    "类型": "type", "组件": "type", "block_type": "type", "blockType": "type",
    "tyep": "type", "typee": "type", "备用文本": "fallback", "降级文本": "fallback",
}

_FIELD_ALIASES = {
    "heading": {"title": "text", "name": "text", "标题": "text", "文字": "text", "级别": "level"},
    "text": {"content": "text", "value": "text", "文字": "text", "内容": "text", "正文": "text"},
    "markdown": {"content": "text", "markdown": "text", "内容": "text", "正文": "text"},
    "callout": {"content": "text", "message": "text", "内容": "text", "文字": "text", "标题": "title", "样式": "style"},
    "quote": {"content": "text", "author": "source", "内容": "text", "文字": "text", "来源": "source"},
    "kv": {"项目": "items", "数据": "items", "列数": "columns"},
    "tags": {"项目": "items", "标签": "items"},
    "code": {"内容": "code", "代码": "code", "语言": "language"},
    "formula": {"公式": "latex", "块级": "display"},
    "stat_row": {"项目": "items", "指标": "items"},
    "table": {"列": "columns", "行": "rows", "功能": "features"},
    "chart": {"类型": "kind", "图表类型": "kind", "分类": "categories", "系列": "series",
              "标题": "title", "高度": "height", "单位": "unit"},
    "wordcloud": {"项目": "items", "词语": "items"},
    "graph": {"节点": "nodes", "边": "edges", "布局": "layout"},
    "mermaid": {"diagram": "code", "content": "code", "内容": "code", "代码": "code"},
    "timeline": {"项目": "items", "事件": "items"},
    "progress": {"项目": "items"},
    "calendar": {"事件": "events", "初始日期": "initial_date"},
    "image": {"插槽": "slot", "说明": "caption", "缩放": "zoom", "宽度": "width"},
    "gallery": {"插槽": "slots", "说明": "captions"},
    "qrcode": {"内容": "text", "文字": "text", "说明": "caption", "尺寸": "size"},
    "barcode": {"内容": "text", "文字": "text", "格式": "format"},
    "section": {"标题": "title", "内容块": "blocks", "正文": "blocks"},
    "card": {"标题": "title", "内容块": "blocks", "正文": "blocks"},
    "columns": {"内容块": "blocks", "比例": "ratio"},
    "tabs": {"项目": "items", "标签页": "items"},
    "collapse": {"项目": "items"},
    "catalog_demo": {"卷": "volume", "卷号": "volume", "分卷": "volume"},
}

_ALLOWED_FIELDS = {
    "heading": {"type", "text", "level", "fallback"},
    "text": {"type", "text", "fallback"},
    "markdown": {"type", "text", "fallback"},
    "callout": {"type", "text", "title", "style", "fallback"},
    "quote": {"type", "text", "source", "fallback"},
    "kv": {"type", "items", "columns", "fallback"},
    "tags": {"type", "items", "fallback"},
    "code": {"type", "code", "language", "fallback"},
    "formula": {"type", "latex", "display", "fallback"},
    "divider": {"type", "fallback"},
    "stat_row": {"type", "items", "fallback"},
    "table": {"type", "columns", "rows", "features", "fallback"},
    "chart": {"type", "kind", "title", "categories", "y_categories", "series", "horizontal",
              "stacked", "height", "unit", "nodes", "links", "fallback"},
    "wordcloud": {"type", "items", "fallback"},
    "graph": {"type", "nodes", "edges", "layout", "height", "fallback"},
    "mermaid": {"type", "code", "fallback"},
    "timeline": {"type", "items", "fallback"},
    "progress": {"type", "items", "fallback"},
    "calendar": {"type", "events", "initial_date", "fallback"},
    "image": {"type", "slot", "caption", "zoom", "width", "fallback"},
    "gallery": {"type", "slots", "captions", "fallback"},
    "qrcode": {"type", "text", "caption", "size", "fallback"},
    "barcode": {"type", "text", "format", "fallback"},
    "section": {"type", "title", "blocks", "fallback"},
    "card": {"type", "title", "blocks", "fallback"},
    "columns": {"type", "blocks", "ratio", "fallback"},
    "tabs": {"type", "items", "fallback"},
    "collapse": {"type", "items", "fallback"},
    "catalog_demo": {"type", "volume", "fallback"},
}


def _typed_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return set(a) == set(b) and all(_typed_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_typed_equal(x, y) for x, y in zip(a, b))
    return a == b


def _short(value: Any) -> str:
    try:
        text = repr(value)
    except Exception:
        if isinstance(value, int) and not isinstance(value, bool):
            # Python 3.11+ deliberately refuses decimal conversion of very
            # large integers.  An audit message must never become a new crash.
            digits = max(1, int(value.bit_length() * math.log10(2)) + 1)
            text = f"<{'-' if value < 0 else ''}int,约{digits}位>"
        else:
            text = f"<{type(value).__name__},无法安全显示>"
    return text if len(text) <= MAX_REPORTABLE_VALUE else text[:MAX_REPORTABLE_VALUE] + "…"


def _plain_text(value: Any) -> str:
    """Bounded, deterministic text for tolerant scalar/object coercion."""
    if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > 4096:
        digits = max(1, int(value.bit_length() * math.log10(2)) + 1)
        return f"{'-' if value < 0 else ''}超大整数（约{digits}位）"
    try:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                              default=lambda item: _short(item))
        return str(value)
    except Exception:
        return _short(value)


def _exact_int_text(value: int) -> str:
    """Exact decimal text without CPython's process-global digit limit."""
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


def _sanitize_string(text: str):
    changed = False
    out = []
    for ch in text:
        cp = ord(ch)
        invalid = (
            0xD800 <= cp <= 0xDFFF
            or 0xFDD0 <= cp <= 0xFDEF
            or (cp & 0xFFFF) in (0xFFFE, 0xFFFF)
            or (cp < 0x20 and cp not in (0x09, 0x0A, 0x0D))
            or 0x7F <= cp <= 0x9F
        )
        if invalid:
            out.append("\uFFFD")
            changed = True
        else:
            out.append(ch)
    return "".join(out), changed


def sanitize_tree(value: Any, rep, path: str = ""):
    if isinstance(value, str):
        clean, changed = _sanitize_string(value)
        if changed:
            rep.add("WARN", path or "/", "非法 Unicode/HTML 控制字符已替换为 U+FFFD",
                    "重新提供有效的 Unicode 文本可避免替换")
        return clean
    if isinstance(value, list):
        return [sanitize_tree(x, rep, f"{path}/{i}") for i, x in enumerate(value)]
    if isinstance(value, dict):
        out = {}
        original_for = {}
        for key, child in value.items():
            if not isinstance(key, str):
                original_key = key
                key = _plain_text(key)
                rep.add("WARN", path or "/",
                        f"非字符串对象键 {_short(original_key)} 已猜测为 {key!r}；置信度=高",
                        "JSON 对象键使用字符串可消除本条警告")
            clean_key, changed = _sanitize_string(key)
            if changed:
                rep.add("WARN", path or "/", "对象键中的非法字符已替换", "请改用有效 Unicode 键名")
            clean_child = sanitize_tree(child, rep, f"{path}/{clean_key}")
            if clean_key in out:
                same = _typed_equal(out[clean_key], clean_child)
                # Prefer an originally canonical/clean key.  Otherwise use the
                # later value, matching transport duplicate-key semantics.
                previous_key = original_for[clean_key]
                prefer_new = key == clean_key or previous_key != clean_key
                selected = clean_child if prefer_new else out[clean_key]
                rep.add(
                    "INFO" if same else "WARN", f"{path}/{clean_key}",
                    ("清洗后取值相同的重复键已合并" if same else
                     f"键清洗后冲突；候选={previous_key!r}/{key!r}；"
                     f"选择={(key if prefer_new else previous_key)!r}；"
                     "原因=原本已是规范键优先，否则 last-wins；置信度=高"),
                    "使用唯一且有效的 Unicode 键名可消除本条审计",
                )
                out[clean_key] = selected
                if prefer_new:
                    original_for[clean_key] = key
                continue
            out[clean_key] = clean_child
            original_for[clean_key] = key
        return out
    if isinstance(value, float) and not math.isfinite(value):
        rep.add("WARN", path or "/",
                f"非有限数字 {_short(value)} 已猜测为 0；原因=JSON/浏览器不支持该数值；置信度=高")
        return 0
    return value


def _apply_aliases(obj: dict, aliases: dict[str, str], rep, path: str):
    out = dict(obj)
    for alias, canonical in aliases.items():
        if alias not in out or alias == canonical:
            continue
        if canonical in out:
            if not _typed_equal(out[alias], out[canonical]):
                rep.add(
                    "WARN", f"{path}/{alias}",
                    f"字段别名冲突；候选={canonical!r}:{_short(out[canonical])} / "
                    f"{alias!r}:{_short(out[alias])}；选择={canonical!r}；"
                    "原因=规范字段优先于别名；置信度=高",
                    f"只保留 {canonical!r} 可消除本条审计",
                )
            else:
                rep.add("INFO", f"{path}/{alias}", f"重复别名字段已合并到 {canonical}")
        else:
            out[canonical] = out[alias]
            rep.add("INFO", f"{path}/{alias}", f"字段别名已归一为 {canonical}")
        del out[alias]
    return out


def _alias_value(obj: dict, canonical: str, alias: str, rep, path: str, default=None):
    """Read aliases deterministically; canonical spelling always wins."""
    if canonical in obj and alias in obj:
        if not _typed_equal(obj[canonical], obj[alias]):
            rep.add(
                "WARN", f"{path}/{alias}",
                f"字段别名冲突；候选={canonical!r}:{_short(obj[canonical])} / "
                f"{alias!r}:{_short(obj[alias])}；选择={canonical!r}；"
                "原因=规范字段优先；置信度=高",
                f"只保留 {canonical!r} 可消除本条审计",
            )
        else:
            rep.add("INFO", f"{path}/{alias}", f"重复别名字段已合并到 {canonical}")
        return obj[canonical]
    if canonical in obj:
        return obj[canonical]
    if alias in obj:
        rep.add("INFO", f"{path}/{alias}", f"字段别名已归一为 {canonical}")
        return obj[alias]
    return default


def _string(value: Any, rep, path: str, *, required: bool = False, max_len: int = MAX_TEXT,
            allow_empty: bool = False, default: str = "") -> str:
    field = path.rsplit('/', 1)[-1]
    if value is None:
        if required:
            guessed = default if default else f"（未提供{field}）"
            rep.add("WARN", path,
                    f"缺少必填字段 {field}；已猜测为 {_short(guessed)}；置信度=低")
            return guessed
        return default
    if not isinstance(value, str):
        original_type = type(value).__name__
        if isinstance(value, list):
            value = "\n".join(_plain_text(item) for item in value)
            reason = "数组项目按原顺序连接"
        else:
            value = _plain_text(value)
            reason = "采用确定性的文本表示"
        rep.add("WARN", path,
                f"{field} 收到 {original_type}；已猜测为字符串；原因={reason}；置信度=中")
    if len(value) > max_len:
        rep.add("WARN", path, f"{field} 长度 {len(value)} 超过 {max_len}；已稳定截断",
                "拆分内容可保留全部文本")
        value = value[:max_len]
    if required and not allow_empty and not value.strip():
        guessed = default if default else f"（未提供{field}）"
        rep.add("WARN", path, f"{field} 为空；已猜测为 {_short(guessed)}；置信度=低")
        value = guessed
    return value


def _list(value: Any, rep, path: str, *, required: bool = False) -> list:
    if value is None:
        if required:
            rep.add("WARN", path,
                    f"缺少必填数组字段 {path.rsplit('/', 1)[-1]}；已猜测为空数组；置信度=低")
        return []
    if isinstance(value, list):
        return value
    rep.add("INFO", path, "单值已包成数组")
    return [value]


_NUMBER_TEXT = re.compile(
    r"^[+-]?(?:(?:\d{1,3}(?:,\d{3})+)|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?$"
    r"|^[+-]?\.\d+(?:[eE][+-]?\d+)?$"
)
_DECIMAL_COMMA_TEXT = re.compile(
    r"^[+-]?\d+,\d+(?:[eE][+-]?\d+)?$"
)


def _number(value: Any, rep, path: str, *, required: bool = False, default: float = 0,
            minimum: float | None = None, maximum: float | None = None) -> float:
    field = path.rsplit('/', 1)[-1]
    if value is None:
        if required:
            rep.add("WARN", path,
                    f"缺少必填数字字段 {field}；已猜测为 {default!r}；置信度=低")
        number = default
    elif isinstance(value, bool):
        number = int(value)
        rep.add("WARN", path,
                f"布尔值 {_short(value)} 已猜测为数字 {number}；原因=false/true 对应 0/1；置信度=中")
    elif isinstance(value, (list, tuple)):
        selected = value[0] if value else default
        rep.add("WARN", path,
                f"数字字段收到数组；候选={_short(value)}；选择={_short(selected)}；"
                "原因=稳定采用第一项；置信度=低")
        return _number(selected, rep, path, required=False, default=default,
                       minimum=minimum, maximum=maximum)
    elif isinstance(value, dict):
        keys = [key for key in ("value", "number", "num", "值", "数值") if key in value]
        selected = value[keys[0]] if keys else default
        rep.add("WARN", path,
                f"数字字段收到对象；候选键={keys!r}；选择={_short(selected)}；"
                f"原因={'采用首个常见数值键' if keys else '没有数值键，采用默认值'}；置信度=低")
        return _number(selected, rep, path, required=False, default=default,
                       minimum=minimum, maximum=maximum)
    elif isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        raw = value.strip()
        decimal_comma = False
        if _NUMBER_TEXT.fullmatch(raw):
            candidate = raw.replace(",", "")
        elif _DECIMAL_COMMA_TEXT.fullmatch(raw):
            # A single comma that is not valid thousands grouping is most
            # commonly a locale decimal separator.  Keep the alternative
            # (delete the comma) in the audit so this deterministic guess is
            # never silent.  A canonical value such as 1,234 still takes the
            # zero-ambiguity thousands path above.
            candidate = raw.replace(",", ".", 1)
            decimal_comma = True
        else:
            # Human/LLM output often contains a unit or percent sign.  The
            # first numeric token is a deterministic, auditable best effort.
            match = re.search(r"[+-]?(?:\d+(?:[.,]\d+)?|\.\d+)(?:[eE][+-]?\d+)?", raw)
            if match:
                candidate = match.group(0).replace(",", ".")
                decimal_comma = "," in match.group(0)
                rep.add("WARN", path,
                        f"数字 {_short(value)} 含额外文本；选择={candidate!r}；"
                        "原因=采用首个可识别数字；置信度=低")
            else:
                number = default
                rep.add("WARN", path,
                        f"数字 {_short(value)} 无法识别；选择={default!r}；"
                        "原因=采用字段默认值；置信度=低")
                candidate = None
        try:
            if candidate is not None:
                number = float(candidate)
        except Exception:
            number = default
            rep.add("WARN", path,
                    f"数字 {_short(value)} 转换失败；选择={default!r}；原因=采用字段默认值；置信度=低")
        if decimal_comma and candidate is not None:
            try:
                removed = float(raw.replace(",", ""))
            except Exception:
                removed = default
            rep.add(
                "WARN", path,
                f"数字字符串 {_short(value)} 存在歧义；"
                f"候选=[{number!r}, {removed!r}]；选择={number!r}；"
                "原因=单个逗号不符合千分位分组，按常见小数逗号解释；置信度=中",
                "改用小数点可消除歧义",
            )
        else:
            rep.add("INFO", path, f"数字字符串 {_short(value)} 已转为数字")
    else:
        number = default
        rep.add("WARN", path,
                f"数字字段收到 {type(value).__name__}；选择={default!r}；"
                "原因=采用字段默认值；置信度=低")
    try:
        finite = math.isfinite(float(number))
    except (OverflowError, ValueError, TypeError):
        finite = False
    if not finite:
        if isinstance(number, (int, float)) and not isinstance(number, bool) and number < 0:
            replacement = minimum if minimum is not None else -1e308
        else:
            replacement = maximum if maximum is not None else (minimum if minimum is not None else default)
        if replacement is None or not math.isfinite(float(replacement)):
            replacement = default
        rep.add("WARN", path,
                f"数字 {_short(number)} 超出有限数范围；选择={replacement!r}；"
                "原因=裁剪到可序列化边界/默认值；置信度=高")
        number = replacement
    if minimum is not None and number < minimum:
        rep.add("WARN", path,
                f"{field}={_short(number)} 小于下限 {minimum}；已裁剪为 {minimum}；置信度=高")
        number = minimum
    if maximum is not None and number > maximum:
        rep.add("WARN", path,
                f"{field}={_short(number)} 大于上限 {maximum}；已裁剪为 {maximum}；置信度=高")
        number = maximum
    return number


def _integer(value: Any, rep, path: str, *, required=False, default=0, minimum=None, maximum=None):
    number = _number(value, rep, path, required=required, default=default,
                     minimum=minimum, maximum=maximum)
    if int(number) != number:
        original = number
        number = int(number)
        rep.add("WARN", path,
                f"{path.rsplit('/', 1)[-1]}={original!r} 不是整数；已向零取整为 {number}；置信度=高")
    return int(number)


def _boolean(value: Any, rep, path: str, *, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            finite = False
        result = bool(value) if finite else default
        rep.add("WARN" if value not in (0, 1) else "INFO", path,
                f"数字 {_short(value)} 已猜测为布尔值 {str(result).lower()}；"
                + ("原因=0 为 false、其他有限数字为 true；置信度=中" if finite else
                   "原因=数值超出有限范围，采用字段默认值；置信度=高"))
        return result
    if isinstance(value, str):
        token = value.strip().lower()
        true_set = {"true", "1", "yes", "y", "是", "开", "on"}
        false_set = {"false", "0", "no", "n", "否", "关", "off"}
        if token in true_set | false_set:
            result = token in true_set
            rep.add("INFO", path, f"字符串 {_short(value)} 已转为 {str(result).lower()}")
            return result
        vocabulary = sorted(true_set | false_set)
        ranked = sorted(((difflib.SequenceMatcher(None, token, word).ratio(), word)
                         for word in vocabulary), key=lambda item: (-item[0], item[1]))
        if ranked and ranked[0][0] >= 0.65:
            result = ranked[0][1] in true_set
            reason = f"与 {ranked[0][1]!r} 最接近"
        else:
            result = default
            reason = "没有可靠语义线索，采用字段默认值"
        rep.add("WARN", path,
                f"布尔值 {_short(value)} 需要猜测；候选=[true, false]；"
                f"选择={str(result).lower()}；原因={reason}；置信度=低")
        return result
    if isinstance(value, (list, tuple)) and len(value) == 1:
        rep.add("WARN", path, "布尔字段收到单项数组；已采用其中一项；置信度=中")
        return _boolean(value[0], rep, path, default=default)
    rep.add("WARN", path,
            f"布尔字段收到 {type(value).__name__}；候选=[true, false]；"
            f"选择={str(default).lower()}；原因=采用字段默认值；置信度=低")
    return default


def _enum(value: Any, allowed: set[str], aliases: dict[str, str], rep, path: str,
          *, required=False, default=None):
    if value is None:
        if required:
            selected = default if default in allowed else sorted(allowed)[0]
            rep.add("WARN", path,
                    f"缺少必填枚举字段；候选={sorted(allowed)!r}；选择={selected!r}；"
                    "原因=采用字段默认值/稳定排序首项；置信度=低")
            return selected
        return default
    if not isinstance(value, str):
        original = value
        value = _plain_text(value)
        rep.add("WARN", path,
                f"枚举字段收到 {type(original).__name__}；已转为字符串 {_short(value)}；置信度=低")
    token = value.strip().lower()
    normalized = aliases.get(token, token)
    if normalized not in allowed:
        # Compare against both canonical spellings and aliases, then rank the
        # canonical results.  Sorting before scoring makes ties stable across
        # Python/hash seeds.  This is intentionally typo recovery, not a way
        # to accept arbitrary new enum semantics.
        ranked = []
        for spelling in sorted(allowed | set(aliases)):
            ratio = difflib.SequenceMatcher(None, token, spelling).ratio()
            canonical = aliases.get(spelling, spelling)
            ranked.append((ratio, canonical, spelling))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        canonical_ranked = []
        seen = set()
        for ratio, canonical, spelling in ranked:
            if canonical in seen:
                continue
            seen.add(canonical)
            canonical_ranked.append((ratio, canonical, spelling))
        if canonical_ranked:
            top_ratio, normalized, matched_spelling = canonical_ranked[0]
            second_ratio = canonical_ranked[1][0] if len(canonical_ranked) > 1 else 0.0
            gap = top_ratio - second_ratio
            confidence = (
                "高" if top_ratio >= 0.88 and gap >= 0.08 else
                "中" if top_ratio >= 0.78 and gap >= 0.03 else "低"
            )
            candidates = [canonical for _, canonical, _ in canonical_ranked[:5]]
            if top_ratio < 0.5 and default in allowed:
                normalized = default
                matched_spelling = "字段默认值"
                confidence = "低"
            rep.add(
                "WARN", path,
                f"枚举值 {_short(value)} 需要猜测；候选={candidates!r}；"
                f"选择={normalized!r}；原因=与可用拼写 {matched_spelling!r} "
                f"相似度 {top_ratio:.2f} 最高；置信度={confidence}",
                f"改用规范值 {normalized!r} 可消除猜测",
            )
        else:
            normalized = default
    if normalized != value:
        rep.add("INFO", path, f"枚举值 {_short(value)} 已归一为 {normalized!r}")
    return normalized


def _fallback(obj: dict, rep, path: str):
    if "fallback" not in obj:
        return ""
    return _string(obj.get("fallback"), rep, path + "/fallback", max_len=2_000)


def _bad_block(obj: Any, path: str, message: str, suggestion: str, rep):
    fallback = ""
    if isinstance(obj, dict) and obj.get("fallback") is not None:
        try:
            fallback = _string(obj.get("fallback"), rep, path + "/fallback", max_len=2_000)
        except Exception:
            fallback = ""
    rep.add("SKIP", path, message, suggestion)
    if fallback:
        return {"type": "__fallback__", "text": fallback, "reason": message}
    return {"type": "__error__", "title": "内容块无法处理", "reason": message,
            "suggestion": suggestion}


def _simple_item_strings(values, rep, path, max_items=2_000):
    values = _list(values, rep, path, required=True)
    if len(values) > max_items:
        rep.add("WARN", path, f"项目数 {len(values)} 超过 {max_items}；已稳定截断",
                "拆分为多个块可保留全部项目")
        values = values[:max_items]
    out = []
    for i, item in enumerate(values):
        out.append(_string(item, rep, f"{path}/{i}", required=True, max_len=5_000))
    if not out:
        rep.add("WARN", path, "项目数组为空；已生成可见占位项目")
        out = ["（未提供项目）"]
    return out


def _dict_items(values, rep, path, *, max_items: int, fields: dict[str, tuple], required: set[str]):
    values = _list(values, rep, path, required=True)
    if len(values) > max_items:
        rep.add("WARN", path, f"项目数 {len(values)} 超过 {max_items}；已稳定截断",
                "拆分为多个块可保留全部项目")
        values = values[:max_items]
    if not values:
        values = [{}]
        rep.add("WARN", path, "项目数组为空；已生成一个可见占位项目")
    out = []
    for i, item in enumerate(values):
        p = f"{path}/{i}"
        if not isinstance(item, dict):
            scalar = item
            item = {}
            # Put a scalar in the most semantically likely field; remaining
            # required fields are filled by the tolerant primitives below.
            preferred = next((key for key in ("value", "text", "title", "weight")
                              if key in fields), sorted(fields)[0])
            item[preferred] = scalar
            if "label" in fields and preferred != "label":
                item["label"] = f"项目 {i + 1}"
            rep.add("WARN", p,
                    f"非对象项目 {type(scalar).__name__} 已猜测为对象并写入 {preferred!r}；置信度=低")
        normalized = {}
        unknown = set(item) - set(fields)
        for key in sorted(unknown):
            rep.add("WARN", f"{p}/{key}", "未知字段未处理")
        try:
            for key, rule in fields.items():
                if key not in item and key not in required:
                    continue
                kind = rule[0]
                if kind == "str":
                    normalized[key] = _string(item.get(key), rep, f"{p}/{key}",
                                              required=key in required, max_len=rule[1])
                elif kind == "num":
                    normalized[key] = _number(item.get(key), rep, f"{p}/{key}",
                                              required=key in required,
                                              default=rule[1], minimum=rule[2], maximum=rule[3])
                elif kind == "bool":
                    normalized[key] = _boolean(item.get(key), rep, f"{p}/{key}", default=rule[1])
            out.append(normalized)
        except BlockError as exc:
            # Only a hard safety rule should still reach this branch.  Keep a
            # visible item instead of silently deleting the user's position.
            rep.add("WARN", p, f"项目无法完整归一：{exc}；已使用占位值",
                    getattr(exc, "suggestion", ""))
            placeholder = {}
            for key, rule in fields.items():
                if key not in required:
                    continue
                placeholder[key] = (f"（未提供{key}）" if rule[0] == "str" else rule[1])
            out.append(placeholder)
    if not out:
        rep.add("WARN", path, "没有可处理项目；已生成占位项目")
        out = [{key: (f"（未提供{key}）" if fields[key][0] == "str" else fields[key][1])
                for key in required}]
    return out


def _normalize_table(obj, rep, path):
    columns_in = _list(obj.get("columns"), rep, path + "/columns", required=True)
    if not columns_in:
        columns_in = ["内容"]
        rep.add("WARN", path + "/columns", "columns 为空；已生成占位列 '内容'")
    if len(columns_in) > MAX_TABLE_COLUMNS:
        rep.add("WARN", path + "/columns",
                f"columns 数量 {len(columns_in)} 超过 {MAX_TABLE_COLUMNS}；已稳定截断")
        columns_in = columns_in[:MAX_TABLE_COLUMNS]
    columns = []
    column_sources = []
    keys = set()
    key_tokens = set()
    for i, col in enumerate(columns_in):
        cp = f"{path}/columns/{i}"
        if isinstance(col, dict):
            unknown = set(col) - {"label", "key", "align"}
            for key in sorted(unknown):
                rep.add("WARN", f"{cp}/{key}", "未知字段未处理")
            label = _string(col.get("label"), rep, cp + "/label", required=True, max_len=500)
            key = col.get("key", i)
            if not isinstance(key, (str, int)) or isinstance(key, bool):
                old_key = key
                key = _plain_text(key)
                rep.add("WARN", cp + "/key",
                        f"第 {i + 1} 列 key {_short(old_key)} 已猜测为字符串 {key!r}；置信度=中")
            original_key = key
            try:
                token = str(key)
            except ValueError:
                # A column key is an identifier, not a numeric quantity.  Its
                # exact decimal spelling is therefore the least-lossy JSON
                # representation; rows and renderer use this canonical token.
                token = _exact_int_text(key)
                key = token
                original_key = token
                rep.add(
                    "WARN", cp + "/key",
                    f"超大整数列 key（约 {len(token.lstrip('-'))} 位）已精确转为十进制字符串；"
                    "原因=JSON 对象键必须可序列化且不可受运行时位数上限影响；置信度=高",
                    "直接使用字符串 key 可消除本条警告",
                )
            if key in keys or token in key_tokens:
                suffix = 2
                renamed = f"{token}__{suffix}"
                while renamed in keys or renamed in key_tokens:
                    suffix += 1
                    renamed = f"{token}__{suffix}"
                conflicts = [column["key"] for column in columns
                             if column["key"] == key or str(column["key"]) == token]
                rep.add(
                    "WARN", cp + "/key",
                    f"表格列 key 冲突；候选={conflicts + [original_key]!r}；"
                    f"选择=保留前列并把当前列重命名为 {renamed!r}；"
                    "原因=JSON 对象键会字符串化，按列顺序 first-wins 可稳定解歧；"
                    "置信度=高",
                    "使用唯一列 key 可消除重命名",
                )
                key = renamed
            keys.add(key)
            key_tokens.add(str(key))
            align = _enum(col.get("align", "left"), {"left", "center", "right"}, {}, rep,
                          cp + "/align", default="left")
            columns.append({"label": label, "key": key, "align": align})
            column_sources.append({"canonical": key, "original": original_key})
        else:
            label = _string(col, rep, cp, required=True, max_len=500)
            original_key = i
            key = original_key
            token = str(key)
            if key in keys or token in key_tokens:
                suffix = 2
                renamed = f"{token}__{suffix}"
                while renamed in keys or renamed in key_tokens:
                    suffix += 1
                    renamed = f"{token}__{suffix}"
                rep.add(
                    "WARN", cp,
                    f"表格默认列 key 冲突；候选={[original_key]!r}；"
                    f"选择={renamed!r}；原因=按列顺序保留先出现键；置信度=高",
                )
                key = renamed
            columns.append({"label": label, "key": key, "align": "left"})
            column_sources.append({"canonical": key, "original": original_key})
            keys.add(key)
            key_tokens.add(str(key))
    rows_in = _list(obj.get("rows"), rep, path + "/rows", required=True)
    if len(rows_in) > MAX_TABLE_ROWS:
        rep.add("WARN", path + "/rows", f"行数 {len(rows_in)} 超过 {MAX_TABLE_ROWS}，已截断",
                "拆分为多个表格可保留全部数据")
        rows_in = rows_in[:MAX_TABLE_ROWS]
    if len(rows_in) * len(columns) > MAX_TABLE_CELLS:
        keep = max(1, MAX_TABLE_CELLS // len(columns))
        rep.add("WARN", path + "/rows",
                f"表格单元格超过 {MAX_TABLE_CELLS}；已稳定保留前 {keep} 行")
        rows_in = rows_in[:keep]
    rows = []
    for i, row in enumerate(rows_in):
        rp = f"{path}/rows/{i}"

        def cell(value, cell_path):
            if isinstance(value, str):
                if len(value) > 20_000:
                    rep.add("WARN", cell_path, "单元格文本超过 20000；已稳定截断")
                    return value[:20_000]
                return value
            if value is None or isinstance(value, (bool, int)):
                return value
            if isinstance(value, float):
                if not math.isfinite(value):
                    rep.add("WARN", cell_path, "非有限单元格数字已猜测为 0")
                    return 0
                return value
            rep.add("WARN", cell_path,
                    f"复杂单元格 {type(value).__name__} 已猜测为 JSON/文本；置信度=中")
            return _plain_text(value)[:20_000]

        if isinstance(row, list):
            original_length = len(row)
            if original_length < len(columns):
                rep.add(
                    "WARN", rp,
                    f"第 {i + 1} 行有 {original_length} 格，少于 {len(columns)} 列；"
                    f"已在行尾补 {len(columns) - original_length} 个空值",
                    "补齐该行可消除警告",
                )
                row = row + [""] * (len(columns) - original_length)
            elif original_length > len(columns):
                rep.add(
                    "WARN", rp,
                    f"第 {i + 1} 行有 {original_length} 格，多于 {len(columns)} 列；"
                    f"已按列顺序保留前 {len(columns)} 格并截断其余值",
                    "删除多余单元格或增加列可消除警告",
                )
                row = row[:len(columns)]
            rows.append([cell(x, f"{rp}/{j}") for j, x in enumerate(row)])
        elif isinstance(row, dict):
            normalized_row = {}
            consumed = set()
            assignments = {}

            def typed_source(candidate):
                for source_key in row:
                    if type(source_key) is type(candidate) and source_key == candidate:
                        return source_key
                return None

            # Exact typed matches are assigned first.  Thus a JSON string key
            # "1" maps to an originally-string "1" column, not an integer 1
            # column that merely stringifies the same way.
            for index, source in enumerate(column_sources):
                for candidate in (source["canonical"], source["original"]):
                    source_key = typed_source(candidate)
                    if source_key is not None and source_key not in consumed:
                        assignments[index] = source_key
                        consumed.add(source_key)
                        break
            # Only then use stringification as a deterministic fallback.
            for index, source in enumerate(column_sources):
                if index in assignments:
                    continue
                for candidate in (str(source["original"]), str(source["canonical"])):
                    if candidate in row and candidate not in consumed:
                        assignments[index] = candidate
                        consumed.add(candidate)
                        rep.add(
                            "WARN", rp,
                            f"对象行键 {candidate!r} 需要猜测映射；"
                            f"候选={[s['canonical'] for s in column_sources if str(s['original']) == candidate]!r}；"
                            f"选择={source['canonical']!r}；"
                            "原因=精确类型匹配优先，其次按列顺序 first-wins；置信度=中",
                        )
                        break
            # A serialized JSON object can only carry string keys.  When two
            # original column keys stringify alike, state exactly which
            # renamed column received that row value instead of leaving the
            # mapping implicit in the earlier column-renaming warning.
            for candidate in row:
                claims = [index for index, source in enumerate(column_sources)
                          if str(source["original"]) == str(candidate)]
                if len(claims) < 2:
                    continue
                selected = next((index for index, source_key in assignments.items()
                                 if source_key == candidate), None)
                if selected is not None:
                    rep.add(
                        "WARN", rp,
                        f"对象行键 {candidate!r} 可对应多列；"
                        f"候选={[column_sources[index]['canonical'] for index in claims]!r}；"
                        f"选择={column_sources[selected]['canonical']!r}；"
                        "原因=原始键类型精确匹配优先，仍并列时按列顺序 first-wins；"
                        "置信度=高",
                        "使用重命名后的唯一列 key 可消除歧义",
                    )
            for index, source in enumerate(column_sources):
                key = source["canonical"]
                source_key = assignments.get(index)
                if source_key is None:
                    rep.add("WARN", rp, f"对象行缺少列 {key!r}，已留空",
                            "补齐该列可消除警告")
                    value = ""
                else:
                    value = row[source_key]
                normalized_row[key] = cell(value, f"{rp}/{source_key if source_key is not None else key}")
            for extra in sorted(set(row) - consumed):
                rep.add("WARN", f"{rp}/{extra}", "对象行中无对应列的字段未处理")
            rows.append(normalized_row)
        else:
            rep.add("WARN", rp,
                    f"第 {i + 1} 行收到 {type(row).__name__}；已放入第一列并补齐其余列")
            rows.append([cell(row, rp + "/0")] + [""] * (len(columns) - 1))
    features = []
    for i, feature in enumerate(_list(obj.get("features"), rep, path + "/features")):
        name = _enum(feature, {"search", "sort"}, {}, rep, f"{path}/features/{i}", required=True)
        if name not in features:
            features.append(name)
    return {"type": "table", "columns": columns, "rows": rows, "features": features}


_CHART_KINDS = {"bar", "line", "area", "pie", "donut", "scatter", "radar", "gauge",
                "funnel", "heatmap", "sankey"}
_CHART_ALIASES = {"column": "bar", "doughnut": "donut", "ring": "donut"}


def _finite_number(value, rep, path):
    return _number(value, rep, path, required=True)


def _normalize_chart(obj, rep, path):
    kind = _enum(obj.get("kind"), _CHART_KINDS, _CHART_ALIASES, rep, path + "/kind",
                 required=True, default="bar")
    meaningful = {
        "horizontal": {"bar", "line", "area"},
        "stacked": {"bar", "line", "area"},
        "unit": {"bar", "line", "area"},
        "categories": {"bar", "line", "area", "pie", "donut", "radar", "funnel", "heatmap"},
        "y_categories": {"heatmap"},
        "nodes": {"sankey"},
        "links": {"sankey"},
        "series": _CHART_KINDS - {"sankey"},
    }
    for field, kinds in meaningful.items():
        if field in obj and kind not in kinds:
            rep.add("WARN", f"{path}/{field}", f"{kind} 不使用 {field}，该字段未处理")
    result = {"type": "chart", "kind": kind}
    if "title" in obj:
        result["title"] = _string(obj.get("title"), rep, path + "/title", max_len=500)
    if "unit" in obj and kind in meaningful["unit"]:
        result["unit"] = _string(obj.get("unit"), rep, path + "/unit", max_len=500)
    result["height"] = _integer(obj.get("height", 320), rep, path + "/height",
                                default=320, minimum=120, maximum=800)
    if kind in meaningful["horizontal"]:
        result["horizontal"] = _boolean(obj.get("horizontal"), rep, path + "/horizontal")
    if kind in meaningful["stacked"]:
        result["stacked"] = _boolean(obj.get("stacked"), rep, path + "/stacked")
    categories = _simple_item_strings(obj.get("categories"), rep, path + "/categories", 20_000) \
        if kind in meaningful["categories"] and obj.get("categories") is not None else []
    y_categories = _simple_item_strings(obj.get("y_categories"), rep, path + "/y_categories", 20_000) \
        if kind in meaningful["y_categories"] and obj.get("y_categories") is not None else []
    if categories and kind in meaningful["categories"]:
        result["categories"] = categories
    if y_categories and kind in meaningful["y_categories"]:
        result["y_categories"] = y_categories

    if kind == "sankey":
        nodes_in = _list(obj.get("nodes"), rep, path + "/nodes", required=True)
        links_in = _list(obj.get("links"), rep, path + "/links", required=True)
        if len(nodes_in) < 2 or not links_in:
            rep.add("WARN", path,
                    "Sankey 节点/连接不足；已生成两个占位节点和一条占位连接")
            nodes_in = [{"name": "起点"}, {"name": "终点"}]
            links_in = [{"source": "起点", "target": "终点", "value": 1}]
        if len(nodes_in) + len(links_in) > MAX_CHART_POINTS:
            node_keep = min(len(nodes_in), MAX_CHART_POINTS // 2)
            link_keep = max(0, MAX_CHART_POINTS - node_keep)
            rep.add("WARN", path,
                    f"Sankey 节点和连接总数超过 {MAX_CHART_POINTS}；"
                    f"已稳定保留前 {node_keep} 个节点和前 {link_keep} 条连接")
            nodes_in, links_in = nodes_in[:node_keep], links_in[:link_keep]
        nodes, names = [], set()
        for i, node in enumerate(nodes_in):
            if not isinstance(node, dict):
                rep.add("WARN", f"{path}/nodes/{i}",
                        f"Sankey 节点 {type(node).__name__} 已猜测为名称对象")
                node = {"name": node}
            for key in sorted(set(node) - {"name", "id"}):
                rep.add("WARN", f"{path}/nodes/{i}/{key}", "Sankey 节点未知字段未处理")
            name = _string(_alias_value(node, "name", "id", rep, f"{path}/nodes/{i}"),
                           rep, f"{path}/nodes/{i}/name",
                           required=True, max_len=500)
            if name in names:
                original = name
                suffix = 2
                while f"{original} ({suffix})" in names:
                    suffix += 1
                name = f"{original} ({suffix})"
                rep.add("WARN", f"{path}/nodes/{i}/name",
                        f"Sankey 节点名称重复；已重命名为 {name!r}")
            names.add(name)
            nodes.append({"name": name})
        links = []
        for i, link in enumerate(links_in):
            if not isinstance(link, dict):
                rep.add("WARN", f"{path}/links/{i}",
                        f"Sankey 连接 {type(link).__name__} 无法识别；已使用首尾节点")
                link = {"source": nodes[0]["name"], "target": nodes[-1]["name"], "value": 1}
            for key in sorted(set(link) - {"source", "from", "target", "to", "value"}):
                rep.add("WARN", f"{path}/links/{i}/{key}", "Sankey 连接未知字段未处理")
            source = _string(_alias_value(link, "source", "from", rep, f"{path}/links/{i}"), rep,
                             f"{path}/links/{i}/source", required=True, max_len=500)
            target = _string(_alias_value(link, "target", "to", rep, f"{path}/links/{i}"), rep,
                             f"{path}/links/{i}/target", required=True, max_len=500)
            if source not in names or target not in names:
                old = (source, target)
                source = source if source in names else nodes[0]["name"]
                target = target if target in names else nodes[-1]["name"]
                rep.add("WARN", f"{path}/links/{i}",
                        f"Sankey 连接 {old!r} 指向不存在节点；已猜测为 {source!r}→{target!r}")
            value = _finite_number(link.get("value", 1), rep, f"{path}/links/{i}/value")
            if value < 0:
                rep.add("WARN", f"{path}/links/{i}/value", "负连接值已裁剪为 0")
                value = 0
            links.append({"source": source, "target": target, "value": value})
        result["nodes"], result["links"] = nodes, links
        return result

    series_in = _list(obj.get("series"), rep, path + "/series", required=True)
    if not series_in:
        series_in = [{"name": "", "data": [0]}]
        rep.add("WARN", path + "/series", "series 为空；已生成一个值为 0 的占位系列")
    if kind in {"pie", "donut", "funnel", "heatmap"} and len(series_in) != 1:
        merged_data = []
        names = []
        for index, raw_series in enumerate(series_in):
            if isinstance(raw_series, dict):
                names.append(_plain_text(raw_series.get("name", "")))
                raw_data = raw_series.get("data", [])
            else:
                names.append("")
                raw_data = raw_series
            merged_data.extend(raw_data if isinstance(raw_data, list) else [raw_data])
        rep.add("WARN", path + "/series",
                f"{kind} 收到 {len(series_in)} 个 series；已按原顺序合并为一个系列")
        series_in = [{"name": " / ".join(name for name in names if name), "data": merged_data}]
    elif kind == "gauge" and len(series_in) != 1:
        rep.add("WARN", path + "/series",
                f"gauge 收到 {len(series_in)} 个 series；已稳定采用第一个系列")
        series_in = series_in[:1]
    series = []
    total = 0
    for i, item in enumerate(series_in):
        sp = f"{path}/series/{i}"
        if isinstance(item, dict):
            for key in sorted(set(item) - {"name", "data"}):
                rep.add("WARN", f"{sp}/{key}", "chart series 未知字段未处理")
            name = _string(item.get("name", ""), rep, sp + "/name", max_len=500)
            data_in = _list(item.get("data"), rep, sp + "/data", required=True)
        elif isinstance(item, list):
            rep.add("INFO", sp, "数组系列已归一为 {name:'',data:[...]} 对象")
            name, data_in = "", item
        else:
            rep.add("WARN", sp,
                    f"series/{i} 收到 {type(item).__name__}；已猜测为单点数据系列")
            name, data_in = "", [item]
        if not data_in:
            data_in = [0]
            rep.add("WARN", sp + "/data", "系列数据为空；已生成一个值为 0 的占位点")
        remaining = MAX_CHART_POINTS - total
        if len(data_in) > remaining:
            rep.add("WARN", sp + "/data",
                    f"图表数据点超过 {MAX_CHART_POINTS}；已稳定保留本系列前 {max(0, remaining)} 点")
            data_in = data_in[:max(0, remaining)]
        total += len(data_in)
        if not data_in:
            break
        data = []
        for j, point in enumerate(data_in):
            pp = f"{sp}/data/{j}"
            if kind in {"bar", "line", "area", "radar", "gauge"}:
                data.append(_finite_number(point, rep, pp))
            elif kind in {"pie", "donut", "funnel"}:
                if isinstance(point, dict):
                    for key in sorted(set(point) - {"name", "value"}):
                        rep.add("WARN", f"{pp}/{key}", "图表数据点未知字段未处理")
                    pname = _string(point.get("name"), rep, pp + "/name", required=True, max_len=500)
                    pvalue = _finite_number(point.get("value"), rep, pp + "/value")
                    data.append({"name": pname, "value": pvalue})
                else:
                    data.append(_finite_number(point, rep, pp))
            elif kind == "scatter":
                if not isinstance(point, list) or len(point) != 2:
                    original = point
                    if isinstance(point, list):
                        point = (point + [0, 0])[:2]
                    else:
                        point = [j, point]
                    rep.add("WARN", pp,
                            f"scatter 点 {_short(original)} 已猜测为 {_short(point)}；置信度=低")
                data.append([_finite_number(point[0], rep, pp + "/0"),
                             _finite_number(point[1], rep, pp + "/1")])
            elif kind == "heatmap":
                if not isinstance(point, list) or len(point) != 3:
                    original = point
                    if isinstance(point, list):
                        point = (point + [0, 0, 0])[:3]
                    else:
                        point = [j, 0, point]
                    rep.add("WARN", pp,
                            f"heatmap 点 {_short(original)} 已猜测为 {_short(point)}；置信度=低")
                x = _integer(point[0], rep, pp + "/0", required=True, minimum=0)
                y = _integer(point[1], rep, pp + "/1", required=True, minimum=0)
                val = _finite_number(point[2], rep, pp + "/2")
                data.append([x, y, val])
        series.append({"name": name, "data": data})
    if not series:
        series = [{"name": "", "data": [0]}]
        rep.add("WARN", path + "/series", "数据点预算耗尽；已保留一个占位点")
    if kind in {"bar", "line", "area", "radar"}:
        target = max([len(categories)] + [len(item["data"]) for item in series])
        if not categories:
            categories = [f"项目 {index + 1}" for index in range(target)]
            rep.add("WARN", path + "/categories",
                    f"{kind} 缺少 categories；已按最长系列生成 {target} 个分类")
        elif len(categories) < target:
            old = len(categories)
            categories.extend(f"项目 {index + 1}" for index in range(old, target))
            rep.add("WARN", path + "/categories",
                    f"categories 少于数据点；已从 {old} 项补到 {target} 项")
        for index, item in enumerate(series):
            if len(item["data"]) < len(categories):
                missing = len(categories) - len(item["data"])
                item["data"].extend([0] * missing)
                rep.add("WARN", f"{path}/series/{index}/data",
                        f"数据少于 categories；已在末尾补 {missing} 个 0")
        result["categories"] = categories
    if kind == "heatmap":
        max_x = max((point[0] for point in series[0]["data"]), default=0)
        max_y = max((point[1] for point in series[0]["data"]), default=0)
        if len(categories) <= max_x:
            old = len(categories)
            categories.extend(f"X{index + 1}" for index in range(old, max_x + 1))
            rep.add("WARN", path + "/categories", "heatmap categories 缺失/过短；已按数据索引补齐")
        if len(y_categories) <= max_y:
            old = len(y_categories)
            y_categories.extend(f"Y{index + 1}" for index in range(old, max_y + 1))
            rep.add("WARN", path + "/y_categories", "heatmap y_categories 缺失/过短；已按数据索引补齐")
        result["categories"], result["y_categories"] = categories, y_categories
    if kind == "gauge" and len(series[0]["data"]) != 1:
        old = len(series[0]["data"])
        series[0]["data"] = series[0]["data"][:1] or [0]
        rep.add("WARN", path + "/series/0/data",
                f"gauge 收到 {old} 个值；已稳定采用第一个值")
    result["series"] = series
    return result


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date(value, rep, path):
    text = _string(value, rep, path, required=True, max_len=40)
    original = text
    match = re.fullmatch(r"\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?:[T\s].*)?\s*", text)
    if match:
        year, month, day = map(int, match.groups())
    else:
        rep.add("WARN", path,
                f"日期 {_short(original)} 无法识别；已猜测为 '1970-01-01'；置信度=低")
        return "1970-01-01"
    month = min(12, max(1, month))
    try:
        max_day = (_dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
                   - _dt.timedelta(days=1)).day
    except Exception:
        rep.add("WARN", path,
                f"日期 {_short(original)} 年份超出范围；已猜测为 '1970-01-01'；置信度=低")
        return "1970-01-01"
    day = min(max_day, max(1, day))
    normalized = f"{year:04d}-{month:02d}-{day:02d}"
    if normalized != original:
        rep.add("WARN", path,
                f"日期 {_short(original)} 已归一/裁剪为 {normalized!r}；置信度=高")
    return normalized


_WIDTH = re.compile(r"^(?:100|[1-9]?\d)(?:%|vw)$|^(?:[1-9]\d{0,3})(?:px|rem|em)$", re.I)
_BARE_WIDTH = re.compile(r"^(?:0|[1-9]\d{0,3})(?:\.\d+)?$")


def _width(value, rep, path):
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            finite = False
        if not finite:
            rep.add("WARN", path,
                    f"width {_short(value)} 超出有限数范围；"
                    "已不设置宽度并交给响应式布局；置信度=高")
            return ""
        value = f"{value:g}px"
        rep.add("INFO", path, f"裸数字宽度已归一为 {value}")
    elif isinstance(value, str) and _BARE_WIDTH.fullmatch(value.strip()):
        value = value.strip() + "px"
        rep.add("INFO", path, f"无单位数字宽度已归一为 {value}")
    if not isinstance(value, str) or not _WIDTH.fullmatch(value.strip()):
        rep.add("WARN", path,
                f"width {_short(value)} 无法安全识别；已不设置宽度并交给响应式布局；置信度=高")
        return ""
    return value.strip().lower()


def _gtin_check_digit(body: str) -> str:
    total = 0
    for index, char in enumerate(reversed(body)):
        total += int(char) * (3 if index % 2 == 0 else 1)
    return str((-total) % 10)


def _normalize_blocks(values: list, rep, path: str, depth: int, state: dict) -> list[dict]:
    """Normalize a block sequence while enforcing one global output budget."""
    out = []
    for i, value in enumerate(values):
        if state["count"] >= MAX_BLOCKS:
            if not state.get("overflow_reported"):
                state["overflow_reported"] = True
                message = f"块总数超过 {MAX_BLOCKS} 上限，后续块未处理"
                rep.add("WARN", f"{path}/{i}", message, "拆分为多个页面")
                out.append({"type": "callout", "style": "warning", "title": "内容已截断",
                            "text": message})
            break
        out.append(_normalize_block(value, rep, f"{path}/{i}", depth, state))
    return out


def _guess_block_type(obj: dict, raw_type: Any):
    """Return (type, candidates, reason, confidence) without executing input.

    The decision order is deliberately stable: close spelling, distinctive
    fields, item shape, then a safe text/divider fallback.  This makes an
    ambiguous human/LLM input usable while keeping the guess auditable.
    """
    if isinstance(raw_type, str) and raw_type.strip():
        token = raw_type.strip().lower()
        vocabulary = sorted(_KNOWN_TYPES | set(_TYPE_ALIASES))
        close = difflib.get_close_matches(token, vocabulary, n=4, cutoff=0.68)
        mapped = []
        for candidate in close:
            canonical = _TYPE_ALIASES.get(candidate, candidate)
            if canonical not in mapped:
                mapped.append(canonical)
        if mapped:
            ratio = difflib.SequenceMatcher(None, token, close[0]).ratio()
            return mapped[0], mapped, f"块类型拼写与 {close[0]!r} 最接近", \
                ("高" if ratio >= 0.88 else "中")

    keys = set(obj)
    # Include common Chinese/human field spellings before per-type aliasing.
    scores: dict[str, int] = {}

    def add(kind: str, points: int):
        scores[kind] = scores.get(kind, 0) + points

    if keys & {"volume", "卷", "卷号", "分卷"}:
        add("catalog_demo", 120)
    if "latex" in keys or "公式" in keys:
        add("formula", 120)
    if (keys & {"rows", "行"}) and (keys & {"columns", "列"}):
        add("table", 120)
    if keys & {"series", "系列", "categories", "分类", "kind", "图表类型", "y_categories"}:
        add("chart", 95)
    if (keys & {"nodes", "节点"}) and (keys & {"edges", "边"}):
        add("graph", 115)
    if "slot" in keys or "插槽" in keys:
        add("image", 110)
    if "slots" in keys or "插槽们" in keys:
        add("gallery", 110)
    if "events" in keys or "事件" in keys or "initial_date" in keys:
        add("calendar", 95)
    if "ratio" in keys or "比例" in keys:
        add("columns", 80)
    if "blocks" in keys or "内容块" in keys or "正文" in keys:
        if "ratio" in keys or "比例" in keys:
            add("columns", 60)
        elif "title" in keys or "标题" in keys:
            add("section", 75)
        else:
            add("card", 45)
    code_value = obj.get("code", obj.get("代码", obj.get("内容")))
    if isinstance(code_value, str) and ("code" in keys or "代码" in keys):
        if re.match(r"\s*(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|"
                    r"gantt|pie|timeline|mindmap|gitGraph)\b", code_value, re.I):
            add("mermaid", 105)
            add("code", 20)
        else:
            add("code", 85)
    if "language" in keys or "语言" in keys:
        add("code", 55)
    if "level" in keys or "级别" in keys:
        add("heading", 80)
    if "source" in keys or "来源" in keys:
        add("quote", 65)
    if "style" in keys or "样式" in keys:
        add("callout", 45)
    if "format" in keys or "格式" in keys:
        add("barcode", 75)
    if "size" in keys or "尺寸" in keys:
        add("qrcode", 40)

    items = obj.get("items", obj.get("项目", obj.get("数据", obj.get("标签"))))
    if items is not None:
        sample = items[0] if isinstance(items, list) and items else items
        if isinstance(sample, dict):
            item_keys = set(sample)
            if "blocks" in item_keys or "内容块" in item_keys:
                add("collapse" if ("open" in item_keys or "展开" in item_keys) else "tabs", 105)
            if item_keys & {"weight", "权重"} and item_keys & {"text", "词语", "文字"}:
                add("wordcloud", 110)
            if item_keys & {"time", "时间"} and item_keys & {"title", "标题"}:
                add("timeline", 105)
            if item_keys & {"max", "最大值"}:
                add("progress", 105)
            if item_keys & {"delta", "unit", "变化", "单位"}:
                add("stat_row", 90)
            if item_keys & {"label", "标签"} and item_keys & {"value", "值"}:
                add("kv", 55)
                add("stat_row", 50)
        elif isinstance(items, (list, str, int, float, bool)):
            add("tags", 65)

    if keys & {"text", "文字", "内容", "正文"}:
        add("text", 45)
    if keys & {"title", "标题"} and not (keys & {"blocks", "内容块", "正文"}):
        add("heading", 30)

    if scores:
        ranked = sorted(scores, key=lambda kind: (-scores[kind], kind))
        top = scores[ranked[0]]
        second = scores[ranked[1]] if len(ranked) > 1 else 0
        confidence = "高" if top >= 100 and top - second >= 30 else "中" if top - second >= 15 else "低"
        return ranked[0], ranked[:5], "根据字段与项目结构评分最高", confidence

    # An explicit fallback is a stronger statement than an uninformed guess.
    if obj.get("fallback") is not None:
        return None, [], "没有足够结构线索，使用作者提供的 fallback", "高"
    if obj:
        return "text", ["text", "kv"], "没有专属字段；选择最少解释的文本块", "低"
    return "callout", ["callout"], "空对象必须留下可见提示", "高"


def _normalize_block(obj: Any, rep, path: str, depth: int, state: dict) -> dict:
    state["count"] += 1
    if depth > MAX_DEPTH:
        return _bad_block(obj, path, f"嵌套超过 {MAX_DEPTH} 层", "减少容器嵌套", rep)
    if not isinstance(obj, dict):
        if isinstance(obj, (str, int, float, bool)):
            rep.add("WARN", path, f"非对象块 {type(obj).__name__} 已猜测为 text；置信度=高")
            return {"type": "text", "text": _plain_text(obj)}
        if obj is None:
            rep.add("WARN", path, "空块已生成可见提示")
            return {"type": "callout", "style": "warning", "title": "空内容块",
                    "text": "此位置没有收到可渲染内容。"}
        if isinstance(obj, (list, tuple)):
            rep.add("WARN", path, "数组块已猜测为文本；置信度=低")
            return {"type": "text", "text": _plain_text(obj)}
        return _bad_block(obj, path, f"块必须是对象，收到 {type(obj).__name__}",
                          '写成 {"type":"text","text":"…"}', rep)
    if not obj:
        rep.add("WARN", path, "空对象块已生成可见提示")
        return {"type": "callout", "style": "warning", "title": "空内容块",
                "text": "此位置收到空对象，未找到可渲染字段。"}
    try:
        obj = _apply_aliases(obj, _COMMON_ALIASES, rep, path)
        raw_type = obj.get("type")
        token = raw_type.strip().lower() if isinstance(raw_type, str) else ""
        block_type = _TYPE_ALIASES.get(token, token) if token else ""
        if block_type and block_type != token:
            rep.add("INFO", path + "/type", f"块类型 {raw_type!r} 已归一为 {block_type!r}")
        if block_type not in _KNOWN_TYPES:
            guessed, candidates, reason, confidence = _guess_block_type(obj, raw_type)
            if guessed:
                rep.add(
                    "WARN", path + "/type",
                    f"块类型需要猜测；原值={_short(raw_type)}；候选={candidates}；"
                    f"选择={guessed!r}；原因={reason}；置信度={confidence}",
                    "本次已继续处理；提供规范 type 可消除猜测",
                )
                block_type = guessed
            else:
                fallback = _fallback(obj, rep, path)
                if fallback:
                    rep.add("WARN", path, f"未知块类型 {_short(raw_type)}；已把 fallback 作为可见提示")
                    return {"type": "callout", "style": "warning", "title": "未知内容块",
                            "text": fallback}
                rep.add("WARN", path, f"无法推断块类型 {_short(raw_type)}；已生成可见提示")
                return {"type": "callout", "style": "warning", "title": "未知内容块",
                        "text": f"无法识别此内容块（type={_short(raw_type)}）。"}
        if block_type not in _KNOWN_TYPES:
            fallback = _fallback(obj, rep, path)
            if fallback:
                rep.add("WARN", path, f"未知块类型 {_short(raw_type)}；已把 fallback 作为可见提示")
                return {"type": "callout", "style": "warning", "title": "未知内容块",
                        "text": fallback}
            rep.add("WARN", path, f"未知块类型 {_short(raw_type)}；已生成可见提示")
            return {"type": "callout", "style": "warning", "title": "未知内容块",
                    "text": f"无法识别此内容块（type={_short(raw_type)}）。"}
        obj["type"] = block_type
        if token == "chart_bar" and "kind" not in obj and "类型" not in obj and "图表类型" not in obj:
            obj["kind"] = "bar"
            rep.add("INFO", path + "/kind", "chart_bar 已无歧义补全 kind='bar'")
        if token == "graph_dagre" and "layout" not in obj and "布局" not in obj:
            obj["layout"] = "dagre"
            rep.add("INFO", path + "/layout", "graph_dagre 已无歧义补全 layout='dagre'")
        obj = _apply_aliases(obj, _FIELD_ALIASES.get(block_type, {}), rep, path)
        # A low-confidence object-to-text guess still needs a usable payload.
        if block_type == "text" and "text" not in obj:
            values = [value for key, value in obj.items() if key not in {"type", "fallback"}]
            obj["text"] = _plain_text(values[0] if len(values) == 1 else values if values else "")
            rep.add("WARN", path + "/text", "缺少 text；已把其余字段值串联为文本；置信度=低")
        unknown = set(obj) - _ALLOWED_FIELDS[block_type]
        for key in sorted(unknown):
            rep.add("WARN", f"{path}/{key}", "未知字段未处理并已从规范化结果移除")
            obj.pop(key, None)
        fallback = _fallback(obj, rep, path)

        if block_type == "heading":
            out = {"type": block_type,
                   "text": _string(obj.get("text"), rep, path + "/text", required=True),
                   "level": _integer(obj.get("level", 2), rep, path + "/level", default=2,
                                     minimum=1, maximum=4)}
        elif block_type == "text":
            raw_text = obj.get("text")
            if isinstance(raw_text, list):
                parts = _simple_item_strings(raw_text, rep, path + "/text", 2_000)
                out = {"type": block_type, "text": parts}
            else:
                out = {"type": block_type,
                       "text": _string(raw_text, rep, path + "/text", required=True)}
        elif block_type == "markdown":
            out = {"type": block_type, "text": _string(obj.get("text"), rep, path + "/text",
                                                         required=True, max_len=MAX_MARKDOWN)}
            if re.search(r"<\s*/?\s*(?:script|style|iframe|object|embed|img|link|meta|base)\b", out["text"], re.I):
                rep.add("WARN", path + "/text", "Markdown 中的原始 HTML/加载标签将作为纯文本显示")
        elif block_type == "callout":
            out = {"type": block_type,
                   "text": _string(obj.get("text"), rep, path + "/text", required=True),
                   "style": _enum(obj.get("style", "info"), {"info", "success", "warning", "danger"},
                                  {"warn": "warning", "err": "danger", "error": "danger", "ok": "success"},
                                  rep, path + "/style", default="info")}
            if "title" in obj:
                out["title"] = _string(obj.get("title"), rep, path + "/title", max_len=500)
        elif block_type == "quote":
            out = {"type": block_type,
                   "text": _string(obj.get("text"), rep, path + "/text", required=True)}
            if "source" in obj:
                out["source"] = _string(obj.get("source"), rep, path + "/source", max_len=500)
        elif block_type == "kv":
            out = {"type": block_type,
                   "items": _dict_items(obj.get("items"), rep, path + "/items", max_items=2_000,
                                        fields={"label": ("str", 500), "value": ("str", 20_000)},
                                        required={"label", "value"}),
                   "columns": _integer(obj.get("columns", 2), rep, path + "/columns",
                                       default=2, minimum=1, maximum=4)}
        elif block_type == "tags":
            out = {"type": block_type,
                   "items": _simple_item_strings(obj.get("items"), rep, path + "/items", 2_000)}
        elif block_type == "code":
            out = {"type": block_type,
                   "code": _string(obj.get("code"), rep, path + "/code", required=True, max_len=MAX_CODE)}
            if "language" in obj:
                out["language"] = _string(obj.get("language"), rep, path + "/language", max_len=30)
        elif block_type == "formula":
            out = {"type": block_type,
                   "latex": _string(obj.get("latex"), rep, path + "/latex", required=True, max_len=20_000),
                   "display": _boolean(obj.get("display"), rep, path + "/display", default=True)}
        elif block_type == "divider":
            out = {"type": block_type}
        elif block_type == "stat_row":
            out = {"type": block_type,
                   "items": _dict_items(obj.get("items"), rep, path + "/items", max_items=200,
                                        fields={"label": ("str", 500), "value": ("str", 2_000),
                                                "unit": ("str", 100), "delta": ("str", 100)},
                                        required={"label", "value"})}
        elif block_type == "progress":
            out = {"type": block_type,
                   "items": _dict_items(obj.get("items"), rep, path + "/items", max_items=2_000,
                                        fields={"label": ("str", 500), "value": ("num", 0, 0, None),
                                                "max": ("num", 100, 1e-12, None)},
                                        required={"label", "value"})}
            for item in out["items"]:
                if "max" not in item:
                    item["max"] = 100
                if item["value"] > item["max"]:
                    original = item["value"]
                    item["value"] = item["max"]
                    rep.add("WARN", path + "/items",
                            f"progress value {original} 大于 max {item['max']}；已裁剪为 max")
        elif block_type == "timeline":
            out = {"type": block_type,
                   "items": _dict_items(obj.get("items"), rep, path + "/items", max_items=2_000,
                                        fields={"time": ("str", 500), "title": ("str", 1_000),
                                                "desc": ("str", 20_000)},
                                        required={"time", "title"})}
        elif block_type == "table":
            out = _normalize_table(obj, rep, path)
        elif block_type == "chart":
            out = _normalize_chart(obj, rep, path)
        elif block_type == "wordcloud":
            out = {"type": block_type,
                   "items": _dict_items(obj.get("items"), rep, path + "/items", max_items=MAX_WORDS,
                                        fields={"text": ("str", 500), "weight": ("num", 1, 0, 10_000)},
                                        required={"text", "weight"})}
        elif block_type == "graph":
            nodes_in = _list(obj.get("nodes"), rep, path + "/nodes", required=True)
            edges_in = _list(obj.get("edges", []), rep, path + "/edges")
            if not nodes_in:
                nodes_in = [{"id": "节点1", "label": "（未提供节点）"}]
                rep.add("WARN", path + "/nodes", "graph.nodes 为空；已生成可见占位节点")
            if len(nodes_in) > MAX_GRAPH_NODES:
                rep.add("WARN", path + "/nodes",
                        f"graph 节点超过 {MAX_GRAPH_NODES}；已稳定截断")
                nodes_in = nodes_in[:MAX_GRAPH_NODES]
            if len(edges_in) > MAX_GRAPH_EDGES:
                rep.add("WARN", path + "/edges",
                        f"graph 边超过 {MAX_GRAPH_EDGES}；已稳定截断")
                edges_in = edges_in[:MAX_GRAPH_EDGES]
            nodes, ids = [], set()
            for i, node in enumerate(nodes_in):
                if not isinstance(node, dict):
                    rep.add("WARN", f"{path}/nodes/{i}",
                            f"graph 节点 {type(node).__name__} 已猜测为 id/label 对象")
                    node = {"id": node, "label": node}
                for key in sorted(set(node) - {"id", "label", "group"}):
                    rep.add("WARN", f"{path}/nodes/{i}/{key}", "graph 节点未知字段未处理")
                nid = _string(node.get("id"), rep, f"{path}/nodes/{i}/id", required=True, max_len=500)
                if nid in ids:
                    original = nid
                    suffix = 2
                    while f"{original}__{suffix}" in ids:
                        suffix += 1
                    nid = f"{original}__{suffix}"
                    rep.add("WARN", f"{path}/nodes/{i}/id",
                            f"graph 节点 id 重复；已重命名为 {nid!r}")
                ids.add(nid)
                nodes.append({"id": nid,
                              "label": _string(node.get("label", nid), rep, f"{path}/nodes/{i}/label", max_len=500),
                              "group": _string(node.get("group", ""), rep, f"{path}/nodes/{i}/group", max_len=200)})
            edges = []
            for i, edge in enumerate(edges_in):
                if not isinstance(edge, dict):
                    rep.add("WARN", f"{path}/edges/{i}",
                            f"graph 边 {type(edge).__name__} 无法辨认；已猜测连接首尾节点")
                    edge = {"from": nodes[0]["id"], "to": nodes[-1]["id"], "label": edge}
                for key in sorted(set(edge) - {"from", "source", "to", "target", "label"}):
                    rep.add("WARN", f"{path}/edges/{i}/{key}", "graph 边未知字段未处理")
                source = _string(_alias_value(edge, "from", "source", rep, f"{path}/edges/{i}"), rep,
                                 f"{path}/edges/{i}/from", required=True, max_len=500)
                target = _string(_alias_value(edge, "to", "target", rep, f"{path}/edges/{i}"), rep,
                                 f"{path}/edges/{i}/to", required=True, max_len=500)
                if source not in ids or target not in ids:
                    original = (source, target)
                    source = source if source in ids else nodes[0]["id"]
                    target = target if target in ids else nodes[-1]["id"]
                    rep.add("WARN", f"{path}/edges/{i}",
                            f"graph 边 {original!r} 指向不存在节点；已猜测为 {source!r}→{target!r}")
                edges.append({"from": source, "to": target,
                              "label": _string(edge.get("label", ""), rep,
                                               f"{path}/edges/{i}/label", max_len=500)})
            layout = _enum(obj.get("layout", "force"), {"force", "circle", "grid", "dagre"}, {},
                           rep, path + "/layout", default="force")
            out = {"type": block_type, "nodes": nodes, "edges": edges, "layout": layout,
                   "height": _integer(obj.get("height", 320), rep, path + "/height",
                                      default=320, minimum=160, maximum=900)}
        elif block_type == "mermaid":
            code = _string(obj.get("code"), rep, path + "/code", required=True, max_len=MAX_MERMAID)
            if len(code.splitlines()) > 1_500 or code.count(";") > 1_500:
                raise BlockError("mermaid 语句/行数超过 1500 上限", "拆成多个图")
            if any(len(line) > 2_000 for line in code.splitlines()):
                raise BlockError("mermaid 单行超过 2000 字符上限", "把图拆成多行或多个图")
            if len(re.findall(r"-->|---|==>|-.->|--x|--o", code)) > 2_000:
                raise BlockError("mermaid 连接数量超过 2000 上限", "拆成多个图")
            if re.search(
                r"%%\s*\{\s*(?:init|config)|\bclick\s+|\bhref\s+|"
                r"(?:https?|javascript|data|file|blob)\s*:|<\s*/?\s*[a-z][^>]*>",
                code,
                re.I,
            ):
                raise BlockError(
                    "mermaid 只允许离线图形语法；配置指令、click/href、HTML 和 URL 被禁止"
                )
            out = {"type": block_type, "code": code}
        elif block_type == "calendar":
            events_in = _list(obj.get("events"), rep, path + "/events", required=True)
            if not events_in:
                events_in = [{"date": "1970-01-01", "title": "（未提供事件）"}]
                rep.add("WARN", path + "/events", "calendar events 为空；已生成可见占位事件")
            if len(events_in) > MAX_EVENTS:
                rep.add("WARN", path + "/events",
                        f"calendar events 超过 {MAX_EVENTS}；已稳定截断")
                events_in = events_in[:MAX_EVENTS]
            events = []
            for i, event in enumerate(events_in):
                if not isinstance(event, dict):
                    rep.add("WARN", f"{path}/events/{i}",
                            f"事件 {type(event).__name__} 已猜测为标题；置信度=低")
                    event = {"date": "1970-01-01", "title": event}
                for key in sorted(set(event) - {"date", "title"}):
                    rep.add("WARN", f"{path}/events/{i}/{key}", "calendar 事件未知字段未处理")
                events.append({"date": _date(event.get("date"), rep, f"{path}/events/{i}/date"),
                               "title": _string(event.get("title"), rep, f"{path}/events/{i}/title",
                                                required=True, max_len=1_000)})
            out = {"type": block_type, "events": events}
            if obj.get("initial_date") is not None:
                out["initial_date"] = _date(obj.get("initial_date"), rep, path + "/initial_date")
        elif block_type == "image":
            out = {"type": block_type,
                   "slot": _integer(obj.get("slot"), rep, path + "/slot", required=True,
                                    minimum=1, maximum=20),
                   "zoom": _boolean(obj.get("zoom"), rep, path + "/zoom")}
            if "caption" in obj:
                out["caption"] = _string(obj.get("caption"), rep, path + "/caption", max_len=2_000)
            width = _width(obj.get("width"), rep, path + "/width")
            if width:
                out["width"] = width
        elif block_type == "gallery":
            slots_in = _list(obj.get("slots"), rep, path + "/slots", required=True)
            if not slots_in:
                slots_in = [1]
                rep.add("WARN", path + "/slots", "gallery slots 为空；已猜测为插槽 1")
            if len(slots_in) > 20:
                rep.add("WARN", path + "/slots", "gallery slots 超过 20 项；已稳定截断")
                slots_in = slots_in[:20]
            slots = [_integer(x, rep, f"{path}/slots/{i}", required=True, minimum=1, maximum=20)
                     for i, x in enumerate(slots_in)]
            if len(set(slots)) != len(slots):
                rep.add("INFO", path + "/slots", "重复插槽已去重")
                slots = list(dict.fromkeys(slots))
            captions = _simple_item_strings(obj.get("captions"), rep, path + "/captions", 20) \
                if obj.get("captions") is not None else []
            if len(captions) > len(slots):
                rep.add("WARN", path + "/captions", "多余说明文字已忽略")
                captions = captions[:len(slots)]
            out = {"type": block_type, "slots": slots, "captions": captions}
        elif block_type == "qrcode":
            out = {"type": block_type,
                   "text": _string(obj.get("text"), rep, path + "/text", required=True,
                                   max_len=MAX_QR_CHARS),
                   "size": _integer(obj.get("size", 132), rep, path + "/size",
                                    default=132, minimum=96, maximum=512)}
            if "caption" in obj:
                out["caption"] = _string(obj.get("caption"), rep, path + "/caption", max_len=2_000)
        elif block_type == "barcode":
            raw_fmt = obj.get("format", "CODE128")
            enum_input = raw_fmt.strip().lower() if isinstance(raw_fmt, str) else raw_fmt
            normalized_fmt = _enum(
                enum_input,
                {"code128", "code39", "ean13", "ean8", "upc", "itf14", "msi", "pharmacode"},
                {"code-128": "code128", "code_128": "code128", "code-39": "code39",
                 "ean-13": "ean13", "ean-8": "ean8", "upca": "upc", "upc-a": "upc",
                 "itf-14": "itf14", "pharma": "pharmacode"},
                rep, path + "/format", default="code128",
            )
            fmt = normalized_fmt.upper()
            text = _string(obj.get("text"), rep, path + "/text", required=True,
                           max_len=MAX_BARCODE_CHARS)
            check_lengths = {"EAN13": (12, 13), "EAN8": (7, 8), "UPC": (11, 12),
                             "ITF14": (13, 14)}
            if fmt in check_lengths:
                body_len, full_len = check_lengths[fmt]
                digits = "".join(ch for ch in text if ch.isascii() and ch.isdigit())
                original = text
                if len(digits) == full_len and digits[-1] == _gtin_check_digit(digits[:-1]):
                    text = digits
                else:
                    body = digits[:body_len].rjust(body_len, "0")
                    text = body + _gtin_check_digit(body)
                    rep.add("WARN", path + "/text",
                            f"{fmt} 内容 {_short(original)} 已提取/补齐/裁剪数字并重算校验位为 {text!r}")
            elif fmt == "CODE39":
                original = text
                text = "".join(ch if re.fullmatch(r"[0-9A-Z .$/+%\-]", ch) else "-"
                               for ch in text.upper()) or "0"
                if text != original:
                    rep.add("WARN", path + "/text", "CODE39 内容已转大写并把不支持字符替换为 '-'")
            elif fmt == "CODE128":
                original = text
                text = "".join(ch if ch.isascii() and 32 <= ord(ch) <= 126 else "?" for ch in text) or "?"
                if text != original:
                    rep.add("WARN", path + "/text", "CODE128 不支持字符已替换为 '?'")
            elif fmt == "MSI":
                original = text
                text = "".join(ch for ch in text if ch.isascii() and ch.isdigit()) or "0"
                if text != original:
                    rep.add("WARN", path + "/text", "MSI 已提取数字；无数字时采用 0")
            elif fmt == "PHARMACODE":
                number = _integer(text, rep, path + "/text", default=3, minimum=3, maximum=131070)
                text = str(number)
            out = {"type": block_type, "text": text, "format": fmt}
        elif block_type == "catalog_demo":
            raw_volume = obj.get("volume", 1)
            if isinstance(raw_volume, str):
                token = raw_volume.strip().lower()
                named = {"一": 1, "二": 2, "三": 3, "四": 4,
                         "one": 1, "two": 2, "three": 3, "four": 4}
                match = re.fullmatch(r"(?:volume|vol|v|卷|第)?\s*0*([1-4])\s*(?:卷)?", token)
                if match:
                    raw_volume = int(match.group(1))
                    rep.add("INFO", path + "/volume", f"卷号 {_short(obj.get('volume'))} 已归一为 {raw_volume}")
                elif token in named:
                    raw_volume = named[token]
                    rep.add("INFO", path + "/volume", f"卷号 {_short(obj.get('volume'))} 已归一为 {raw_volume}")
            out = {"type": block_type,
                   "volume": _integer(raw_volume, rep, path + "/volume",
                                      default=1, minimum=1, maximum=4)}
        elif block_type in {"section", "card"}:
            children = _list(obj.get("blocks"), rep, path + "/blocks", required=True)
            if not children:
                children = [{"type": "callout", "style": "warning", "title": "空容器",
                             "text": "此容器没有收到内容块。"}]
                rep.add("WARN", path + "/blocks", "blocks 为空；已生成可见提示块")
            out = {"type": block_type,
                   "blocks": _normalize_blocks(children, rep, path + "/blocks", depth + 1, state)}
            if block_type == "section":
                out["title"] = _string(obj.get("title"), rep, path + "/title", required=True, max_len=1_000)
            elif "title" in obj:
                out["title"] = _string(obj.get("title"), rep, path + "/title", max_len=1_000)
        elif block_type == "columns":
            groups = _list(obj.get("blocks"), rep, path + "/blocks", required=True)
            if not groups:
                groups = [[{"type": "callout", "style": "warning", "title": "空分栏",
                            "text": "此分栏没有收到内容块。"}]]
                rep.add("WARN", path + "/blocks", "columns.blocks 为空；已生成一个可见占位栏")
            elif all(isinstance(group, dict) for group in groups):
                groups = [groups]
                rep.add("WARN", path + "/blocks",
                        "columns.blocks 看起来是单栏块数组；已外包一层为一个栏；置信度=高")
            else:
                repaired = []
                for index, group in enumerate(groups):
                    if isinstance(group, list):
                        repaired.append(group)
                    else:
                        repaired.append([group])
                        rep.add("WARN", f"{path}/blocks/{index}",
                                "非数组栏已包成单块数组；置信度=高")
                groups = repaired
            if len(groups) > 6:
                rep.add("WARN", path + "/blocks", "columns 栏数超过 6；已稳定保留前 6 栏")
                groups = groups[:6]
            nested = [_normalize_blocks(group, rep, f"{path}/blocks/{i}", depth + 1, state)
                      for i, group in enumerate(groups)]
            ratios_in = _list(obj.get("ratio"), rep, path + "/ratio") if obj.get("ratio") is not None else [1] * len(groups)
            if len(ratios_in) < len(groups):
                missing = len(groups) - len(ratios_in)
                ratios_in.extend([1] * missing)
                rep.add("WARN", path + "/ratio",
                        f"ratio 少于栏数；已在末尾补 {missing} 个 1")
            elif len(ratios_in) > len(groups):
                rep.add("WARN", path + "/ratio",
                        f"ratio 多于栏数；已稳定保留前 {len(groups)} 项")
                ratios_in = ratios_in[:len(groups)]
            ratios = [_number(x, rep, f"{path}/ratio/{i}", required=True, minimum=0.01, maximum=100)
                      for i, x in enumerate(ratios_in)]
            out = {"type": block_type, "blocks": nested, "ratio": ratios}
        elif block_type in {"tabs", "collapse"}:
            items_in = _list(obj.get("items"), rep, path + "/items", required=True)
            if not items_in:
                items_in = [{"label": "（空）", "blocks": [{"type": "callout", "style": "warning",
                                                              "text": "未收到内容。"}]}]
                rep.add("WARN", path + "/items", f"{block_type}.items 为空；已生成可见占位项")
            if len(items_in) > 50:
                rep.add("WARN", path + "/items", f"{block_type}.items 超过 50；已稳定截断")
                items_in = items_in[:50]
            items = []
            for i, item in enumerate(items_in):
                if not isinstance(item, dict):
                    rep.add("WARN", f"{path}/items/{i}",
                            f"{block_type} 项 {type(item).__name__} 已猜测为文本页；置信度=低")
                    item = {"label": f"项目 {i + 1}", "blocks": [{"type": "text", "text": item}]}
                item_allowed = {"label", "blocks", "open"} if block_type == "collapse" else {"label", "blocks"}
                for key in sorted(set(item) - item_allowed):
                    rep.add("WARN", f"{path}/items/{i}/{key}", f"{block_type} 项未知字段未处理")
                label = _string(item.get("label"), rep, f"{path}/items/{i}/label",
                                required=True, max_len=500)
                children = _list(item.get("blocks"), rep, f"{path}/items/{i}/blocks", required=True)
                if not children:
                    children = [{"type": "callout", "style": "warning", "text": "未收到内容。"}]
                    rep.add("WARN", f"{path}/items/{i}/blocks", "空内容已替换为可见提示块")
                normalized = {"label": label,
                              "blocks": _normalize_blocks(children, rep,
                                                           f"{path}/items/{i}/blocks",
                                                           depth + 1, state)}
                if block_type == "collapse":
                    normalized["open"] = _boolean(item.get("open"), rep, f"{path}/items/{i}/open")
                items.append(normalized)
            out = {"type": block_type, "items": items}
        else:
            raise BlockError(f"未实现块类型 {block_type}")
        if fallback:
            out["fallback"] = fallback
        return out
    except BlockError as exc:
        return _bad_block(obj, path, str(exc), getattr(exc, "suggestion", "") or "按 PageSpec 规范修正该块", rep)


def normalize_spec(spec: Any, rep):
    """Return (canonical_spec, hard_error)."""
    if isinstance(spec, list):
        rep.add("WARN", "/", "顶层数组已猜测为 blocks；置信度=高")
        spec = {"version": 1, "blocks": spec}
    elif isinstance(spec, (str, int, float, bool)):
        rep.add("WARN", "/", f"顶层 {type(spec).__name__} 已猜测为文本块；置信度=高")
        spec = {"version": 1, "blocks": [{"type": "text", "text": _plain_text(spec)}]}
    elif spec is None:
        rep.add("WARN", "/", "顶层为空；已生成可见提示块")
        spec = {"version": 1, "blocks": [{"type": "callout", "style": "warning",
                                                "title": "输入为空", "text": "未收到可渲染内容。"}]}
    if not isinstance(spec, dict):
        return None, f"顶层类型 {type(spec).__name__} 超出安全容错范围"
    try:
        spec = sanitize_tree(spec, rep)
        if "type" in spec and "blocks" not in spec and "内容块" not in spec and "正文" not in spec:
            rep.add("WARN", "/", "顶层对象含块 type；已猜测为单块并补 PageSpec 信封；置信度=高")
            spec = {"version": spec.get("version", 1), "blocks": [
                {key: value for key, value in spec.items() if key != "version"}
            ]}
        spec = _apply_aliases(spec, {"版本": "version", "文档": "doc", "页面": "doc",
                                     "内容块": "blocks", "正文": "blocks", "内容": "blocks",
                                     "模式": "profile", "用途": "profile"}, rep, "")
        unknown = set(spec) - {"version", "doc", "blocks", "profile"}
        for key in sorted(unknown):
            rep.add("WARN", f"/{key}", "未知顶层字段未处理并已移除")
        version = spec.get("version")
        if version is None:
            version = 1
            rep.add("INFO", "/version", "缺少 version，已按当前唯一版本 1 处理")
        if isinstance(version, bool):
            rep.add("WARN", "/version", f"version {version!r} 不是版本号；已猜测为当前版本 1；置信度=高")
            version = 1
        elif isinstance(version, str) and re.fullmatch(
                r"(?:pagespec\s*[/_-]?\s*|v(?:ersion)?\s*)?0*1(?:\.0+)?",
                version.strip().lower()):
            rep.add("INFO", "/version", f"version {version!r} 已归一为整数 1")
            version = 1
        elif isinstance(version, str):
            rep.add("WARN", "/version",
                    f"version {version!r} 无法精确匹配；候选=[1]；选择=1；"
                    "原因=当前渲染器只有 v1；置信度=中")
            version = 1
        elif isinstance(version, float) and math.isfinite(version) and int(version) == 1:
            rep.add("INFO", "/version", f"version {version!r} 已归一为整数 1")
            version = 1
        if version != 1 or type(version) is not int:
            rep.add("WARN", "/version",
                    f"version {_short(version)} 不受支持；候选=[1]；选择=1；"
                    "原因=当前渲染器只有 v1；置信度=高")
            version = 1
        blocks = spec.get("blocks")
        if isinstance(blocks, dict):
            blocks = [blocks]
            rep.add("INFO", "/blocks", "单个块对象已自动包装为 blocks 数组")
        if not isinstance(blocks, list):
            if blocks is not None:
                blocks = [blocks]
                rep.add("WARN", "/blocks", "非数组 blocks 已包成单项数组；置信度=高")
            else:
                residual = {key: value for key, value in spec.items()
                            if key not in {"version", "doc", "profile"}}
                if residual:
                    blocks = [residual]
                    rep.add("WARN", "/blocks", "缺少 blocks；已把剩余顶层字段猜测为单块；置信度=低")
                else:
                    blocks = []
        if not blocks:
            message = "blocks 为空；已生成可见提示块而非中止"
            rep.add("WARN", "/blocks", message, "添加内容块可替换提示")
            blocks = [{"type": "callout", "style": "warning", "title": "没有内容",
                       "text": "输入中没有可渲染的内容块。"}]
        state = {"count": 0}
        normalized_blocks = _normalize_blocks(blocks, rep, "/blocks", 0, state)
        catalog_blocks = [block for block in normalized_blocks if block.get("type") == "catalog_demo"]
        profile_raw = spec.get("profile")
        if catalog_blocks:
            if profile_raw not in (None, "catalog-verification", "catalog", "verification", "全库验证", "全库测试"):
                rep.add("WARN", "/profile", f"模式 {_short(profile_raw)} 与 catalog_demo 冲突，已采用 catalog-verification")
            if profile_raw != "catalog-verification":
                rep.add("INFO", "/profile", "已按 catalog_demo 推断 profile='catalog-verification'")
            if len(catalog_blocks) > 1 or len(normalized_blocks) != 1:
                rep.add("WARN", "/blocks", "目录验证页一次只生成一卷；已采用第一个 catalog_demo，其余块未处理",
                        "用四份独立工作流分别生成四卷可避免超出 30 MB")
            normalized = {"version": 1, "profile": "catalog-verification",
                          "blocks": [catalog_blocks[0]]}
        else:
            if profile_raw is not None:
                rep.add("INFO", "/profile", "未出现 catalog_demo，profile 对普通页面不产生影响")
            normalized = {"version": 1, "blocks": normalized_blocks}
        doc = spec.get("doc")
        if doc is not None:
            if not isinstance(doc, dict):
                rep.add("WARN", "/doc", f"doc {type(doc).__name__} 已猜测为标题；置信度=中")
                doc = {"title": _plain_text(doc)}
            doc = _apply_aliases(doc, {"标题": "title", "文件名": "filename", "主题": "theme",
                                       "目录": "toc", "页眉": "header", "页脚": "footer",
                                       "强调色": "accent", "语言": "lang", "language": "lang",
                                       "language_code": "lang", "locale": "lang"}, rep, "/doc")
            allowed = {"title", "filename", "theme", "toc", "header", "footer", "accent", "lang"}
            for key in sorted(set(doc) - allowed):
                rep.add("WARN", f"/doc/{key}", "未知 doc 字段未处理")
            ndoc = {}
            if "title" in doc:
                ndoc["title"] = _string(doc.get("title"), rep, "/doc/title", max_len=1_000)
            if "filename" in doc:
                ndoc["filename"] = _string(doc.get("filename"), rep, "/doc/filename", max_len=240)
            if "lang" in doc:
                language = _string(doc.get("lang"), rep, "/doc/lang", max_len=35).replace("_", "-")
                parts = [part for part in language.split("-") if part]
                if parts and re.fullmatch(r"[A-Za-z]{2,8}", parts[0]) and all(
                    re.fullmatch(r"[A-Za-z0-9]{1,8}", part) for part in parts[1:]
                ):
                    normalized_parts = [parts[0].lower()]
                    for part in parts[1:]:
                        normalized_parts.append(
                            part.upper() if len(part) in (2, 3) and part.isalpha()
                            else part.title() if len(part) == 4 and part.isalpha()
                            else part.lower()
                        )
                    normalized_language = "-".join(normalized_parts)
                    if normalized_language != language:
                        rep.add("INFO", "/doc/lang", f"语言标签 {language!r} 已归一为 {normalized_language!r}")
                    ndoc["lang"] = normalized_language
                else:
                    ndoc["lang"] = "zh-CN"
                    rep.add("WARN", "/doc/lang",
                            f"语言标签 {language!r} 无法可靠识别；候选=['zh-CN']；选择='zh-CN'；"
                            "原因=采用插件默认语言；置信度=低")
            ndoc["theme"] = _enum(doc.get("theme", "dark"), {"dark", "light"},
                                  {"深色": "dark", "浅色": "light"}, rep, "/doc/theme", default="dark")
            ndoc["toc"] = _boolean(doc.get("toc"), rep, "/doc/toc")
            if "footer" in doc:
                ndoc["footer"] = _string(doc.get("footer"), rep, "/doc/footer", max_len=10_000)
            if "accent" in doc:
                accent = _string(doc.get("accent"), rep, "/doc/accent", max_len=20)
                named = {"蓝": "#2563eb", "蓝色": "#2563eb", "绿": "#059669", "绿色": "#059669",
                         "红": "#dc2626", "红色": "#dc2626", "紫": "#7c3aed", "紫色": "#7c3aed"}
                if accent in named:
                    old_accent = accent
                    accent = named[accent]
                    rep.add("INFO", "/doc/accent", f"中文颜色 {old_accent!r} 已归一为 {accent}")
                if re.fullmatch(r"[0-9a-fA-F]{6}", accent):
                    accent = "#" + accent
                    rep.add("INFO", "/doc/accent", "六位十六进制颜色已补上 #")
                if not re.fullmatch(r"#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}", accent):
                    rep.add("WARN", "/doc/accent",
                            f"强调色 {accent!r} 不可安全识别；候选=['#2563eb']；"
                            "选择='#2563eb'；原因=内置默认蓝色；置信度=高")
                    accent = "#2563eb"
                ndoc["accent"] = accent.lower()
            header = doc.get("header")
            if header is not None:
                if not isinstance(header, dict):
                    rep.add("WARN", "/doc/header",
                            f"header {type(header).__name__} 已猜测为标题；置信度=中")
                    header = {"title": _plain_text(header)}
                header = _apply_aliases(header, {"标题": "title", "副标题": "subtitle", "标签": "badges"},
                                        rep, "/doc/header")
                for key in sorted(set(header) - {"title", "subtitle", "badges"}):
                    rep.add("WARN", f"/doc/header/{key}", "未知 header 字段未处理")
                nh = {}
                if "title" in header:
                    nh["title"] = _string(header.get("title"), rep, "/doc/header/title", max_len=1_000)
                if "subtitle" in header:
                    nh["subtitle"] = _string(header.get("subtitle"), rep, "/doc/header/subtitle", max_len=2_000)
                if "badges" in header:
                    nh["badges"] = _simple_item_strings(header.get("badges"), rep,
                                                         "/doc/header/badges", 100)
                ndoc["header"] = nh
            normalized["doc"] = ndoc
        return normalized, None
    except (ValidationError, BlockError) as exc:
        return None, str(exc)
