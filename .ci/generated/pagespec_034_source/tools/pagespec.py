# -*- coding: utf-8 -*-
"""
PageSpec v1 renderer — turn a JSON page description into one offline HTML body.

The plugin, not the user, authors every byte of HTML/CSS/JS here. The user only
supplies a JSON "spec" describing WHAT is on the page (blocks); the renderer
decides HOW. That removes the largest historical failure class (parsing
user-authored HTML) entirely: there is no user HTML to parse.

Design mirrors the DOCX plugin's tolerance model:
  - correct spec renders unchanged (直通),
  - fixable fields are normalised and logged (归一),
  - an unfixable block becomes a visible error card while every other block
    still renders (单块降级),
  - only 3 conditions reject the whole input (硬错误).

Layout / text / table / stat / timeline / tabs blocks are pure plugin-generated
vanilla HTML+CSS+JS (ZERO third-party library). Only genuinely chart-shaped
blocks pull a bundled, version-locked library. The old arbitrary `html` block
is absent: the reliable tool has zero low-level escape hatches.
"""
from __future__ import annotations

import json
import re
from html import escape as _esc
from pathlib import Path

import pagespec_transport
import pagespec_validate


def _safe_text(v) -> str:
    text = "" if v is None else str(v)
    out = []
    for ch in text:
        cp = ord(ch)
        if (0xD800 <= cp <= 0xDFFF or 0xFDD0 <= cp <= 0xFDEF
                or (cp & 0xFFFF) in (0xFFFE, 0xFFFF)
                or (cp < 0x20 and cp not in (9, 10, 13)) or 0x7F <= cp <= 0x9F):
            out.append("\uFFFD")
        else:
            out.append(ch)
    return "".join(out)


def esc(v) -> str:
    return _esc(_safe_text(v), quote=True)


def js_json(value) -> str:
    """JSON safe inside an HTML script element (blocks </script> breakout)."""
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


# ---- library requirements per block (key -> vendor_map key) ------------------
# Only these libraries can ever be pulled in, and only when a block needs them.
LIB = {
    "markdown": ["marked", "dompurify"],
    "code": ["highlight.js"],
    "formula": ["katex"],
    "chart": ["echarts"],
    "wordcloud": ["wordcloud"],
    "graph": ["cytoscape"],
    "graph_dagre": ["cytoscape", "dagre"],
    "mermaid": ["mermaid"],
    "calendar": ["fullcalendar"],
    "qrcode": ["qr-code-styling"],
    "barcode": ["jsbarcode"],
}

_ID = re.compile(r"[^a-z0-9]+")

# resource gates (ChatGPT review: input-side limits were missing)
MAX_SPEC_BYTES = 2_000_000     # 2 MB of JSON text
MAX_BLOCKS = 800               # total blocks incl. nested
MAX_DEPTH = 6                  # container nesting depth
MAX_TABLE_ROWS = 3000          # rows beyond are truncated + warned
MAX_CHART_POINTS = 20_000      # total data points per chart


class Report:
    """Collects tolerance events; renders to 3 sinks (comment/panel/text)."""

    # Only the human-facing table is capped.  `items` itself is deliberately
    # complete: every normalisation/guess/ignored field remains recoverable
    # from #__ofx-report-data in the generated HTML.  The old implementation
    # silently discarded the tail after 2,000 entries, which made a large but
    # valid PageSpec impossible to audit.
    MAX_ITEMS = 2_000

    def __init__(self):
        self.items: list[dict] = []
        # Kept for backwards-compatible callers.  It now means "not retained
        # anywhere" and therefore must remain zero.
        self.omitted = 0
        self.total = 0

    def add(self, level: str, where: str, msg: str, suggestion: str = ""):
        self.total += 1
        self.items.append({"id": self.total, "level": _safe_text(level),
                           "where": _safe_text(where), "message": _safe_text(msg),
                           "suggestion": _safe_text(suggestion)})

    @property
    def counts(self):
        c = {"INFO": 0, "WARN": 0, "SKIP": 0}
        for it in self.items:
            c[it["level"]] = c.get(it["level"], 0) + 1
        return c


# ============================ tolerance / normalise ===========================

def parse_spec(raw):
    """Compatibility wrapper used by tests; returns value, error, audit events."""
    outcome = pagespec_transport.parse_spec(raw)
    return outcome.value, outcome.error, outcome.events


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def as_num(v, default=0):
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        t = v.strip().replace(",", "")
        try:
            return int(t)
        except Exception:
            try:
                return float(t)
            except Exception:
                return default
    return default


def as_bool(v, default=False):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "是", "开", "on")
    return default


_ENUM_ALIAS = {
    "warn": "warning", "err": "danger", "error": "danger", "ok": "success",
    "note": "info", "tip": "info",
    "column": "bar", "bar": "bar", "line": "line", "area": "area",
    "pie": "pie", "donut": "donut", "doughnut": "donut", "ring": "donut",
    "scatter": "scatter", "radar": "radar", "gauge": "gauge",
    "funnel": "funnel", "heatmap": "heatmap", "sankey": "sankey",
}


def norm_enum(v, allowed, default, rep, where):
    if not isinstance(v, str):
        return default
    t = v.strip().lower()
    t = _ENUM_ALIAS.get(t, t)
    if t in allowed:
        if t != v:
            rep.add("INFO", where, f"枚举值 '{v}' 已归一为 '{t}'")
        return t
    rep.add("INFO", where, f"未知枚举值 '{v}'，改用默认 '{default}'")
    return default


# ================================ block renderers =============================
# Each returns HTML string; may add to `need` (libs) and `rep` (report).
# Any exception is caught by render_block -> error card.

def _mk(tag, cls, inner, **attrs):
    a = "".join(f' {k}="{esc(v)}"' for k, v in attrs.items())
    return f'<{tag} class="{cls}"{a}>{inner}</{tag}>'


def r_heading(b, ctx):
    lvl = int(as_num(b.get("level"), 2)) or 2
    lvl = min(4, max(1, lvl))
    return f'<h{lvl+1} class="ps-h ps-h{lvl}">{esc(b.get("text"))}</h{lvl+1}>'


def r_text(b, ctx):
    parts = as_list(b.get("text"))
    return "".join(f'<p class="ps-p">{esc(p)}</p>' for p in parts)


def r_markdown(b, ctx):
    ctx["need"].update(LIB["markdown"])
    i = ctx["uid"]("md")
    raw = b.get("text") or ""
    data = js_json(raw)
    ctx["scripts"].append(
        f'try{{__ps_render_markdown("{i}",{data});}}catch(e){{__ps_fail("{i}",e)}}'
    )
    return f'<div class="ps-md" id="{i}"></div>'


def r_callout(b, ctx):
    style = norm_enum(b.get("style"), ("info", "success", "warning", "danger"), "info", ctx["rep"], "callout.style")
    title = f'<div class="ps-callout-t">{esc(b.get("title"))}</div>' if b.get("title") else ""
    return f'<div class="ps-callout ps-{style}">{title}<div>{esc(b.get("text"))}</div></div>'


def r_quote(b, ctx):
    source = f'<footer class="ps-quote-src">— {esc(b.get("source"))}</footer>' if b.get("source") else ""
    return f'<blockquote class="ps-quote">{esc(b.get("text"))}{source}</blockquote>'


def r_kv(b, ctx):
    cols = min(4, max(1, int(as_num(b.get("columns"), 2))))
    items = as_list(b.get("items"))
    cells = "".join(
        f'<div class="ps-kv-i"><span class="ps-kv-k">{esc(it.get("label"))}</span>'
        f'<span class="ps-kv-v">{esc(it.get("value"))}</span></div>'
        for it in items if isinstance(it, dict)
    )
    return f'<div class="ps-kv" style="grid-template-columns:repeat({cols},1fr)">{cells}</div>'


def r_tags(b, ctx):
    items = as_list(b.get("items"))
    return '<div class="ps-tags">' + "".join(f'<span class="ps-tag">{esc(t)}</span>' for t in items) + "</div>"


def r_code(b, ctx):
    ctx["need"].update(LIB["code"])
    i = ctx["uid"]("code")
    lang = re.sub(r"[^a-z0-9+#-]", "", str(b.get("language") or "").lower())[:20]
    cls = f"language-{lang}" if lang else ""
    ctx["scripts"].append(
        f'try{{hljs.highlightElement(document.getElementById("{i}"));}}catch(e){{__ps_fail("{i}",e)}}'
    )
    return f'<pre class="ps-code"><code id="{i}" class="{cls}">{esc(b.get("code"))}</code></pre>'


def r_formula(b, ctx):
    ctx["need"].update(LIB["formula"])
    i = ctx["uid"]("ktx")
    disp = as_bool(b.get("display"), True)
    latex = js_json(b.get("latex") or "")
    ctx["scripts"].append(
        f'try{{katex.render({latex},document.getElementById("{i}"),'
        f'{{displayMode:{"true" if disp else "false"},throwOnError:false,trust:false,'
        f'strict:"error",maxExpand:1000,maxSize:50}});}}catch(e){{__ps_fail("{i}",e)}}'
    )
    tag = "div" if disp else "span"
    return f'<{tag} class="ps-formula" id="{i}"></{tag}>'


def r_divider(b, ctx):
    return '<hr class="ps-divider">'


def r_stat_row(b, ctx):
    items = as_list(b.get("items"))
    cells = []
    for it in items:
        if not isinstance(it, dict):
            continue
        d = it.get("delta")
        dh = ""
        if d not in (None, ""):
            ds = str(d)
            dc = "up" if ds.strip().startswith("+") else ("down" if ds.strip().startswith("-") else "flat")
            dh = f'<span class="ps-stat-d ps-{dc}">{esc(d)}</span>'
        unit = f'<span class="ps-stat-u">{esc(it.get("unit"))}</span>' if it.get("unit") else ""
        cells.append(
            f'<div class="ps-stat"><div class="ps-stat-v">{esc(it.get("value"))}{unit}</div>'
            f'<div class="ps-stat-l">{esc(it.get("label"))}{dh}</div></div>'
        )
    return f'<div class="ps-stats">{"".join(cells)}</div>'


def r_progress(b, ctx):
    rows = []
    for it in as_list(b.get("items")):
        if not isinstance(it, dict):
            continue
        val = as_num(it.get("value"), 0)
        mx = as_num(it.get("max"), 100) or 100
        pct = max(0, min(100, val / mx * 100))
        rows.append(
            f'<div class="ps-prog-r"><span class="ps-prog-l">{esc(it.get("label"))}</span>'
            f'<span class="ps-prog-bar"><span style="width:{pct:.1f}%"></span></span>'
            f'<span class="ps-prog-v">{esc(it.get("value"))}</span></div>'
        )
    return f'<div class="ps-prog">{"".join(rows)}</div>'


def r_timeline(b, ctx):
    rows = []
    for it in as_list(b.get("items")):
        if not isinstance(it, dict):
            continue
        desc = f'<div class="ps-tl-d">{esc(it.get("desc"))}</div>' if it.get("desc") else ""
        rows.append(
            f'<div class="ps-tl-i"><div class="ps-tl-dot"></div>'
            f'<div class="ps-tl-time">{esc(it.get("time"))}</div>'
            f'<div class="ps-tl-body"><div class="ps-tl-t">{esc(it.get("title"))}</div>{desc}</div></div>'
        )
    return f'<div class="ps-tl">{"".join(rows)}</div>'


def r_table(b, ctx):
    # plugin-generated table + optional vanilla search/sort (ZERO library)
    cols = as_list(b.get("columns"))
    if not cols:
        raise ValueError('table 缺少 columns，示例："columns":["甲","乙"],"rows":[[1,2]]')
    labels, aligns, keys = [], [], []
    for j, c in enumerate(cols):
        labels.append(c.get("label", "") if isinstance(c, dict) else c)
        aligns.append(c.get("align", "left") if isinstance(c, dict) else "left")
        keys.append(c.get("key", j) if isinstance(c, dict) else j)
    feats = set(as_list(b.get("features")))
    rows = as_list(b.get("rows"))
    if len(rows) > MAX_TABLE_ROWS:
        ctx["rep"].add("WARN", "table", f"行数 {len(rows)} 超过 {MAX_TABLE_ROWS}，已截断")
        rows = rows[:MAX_TABLE_ROWS]
    body = []
    for row in rows:
        if isinstance(row, dict):
            vals = [row.get(k, "") for k in keys]
        elif isinstance(row, list):
            vals = row
        else:
            vals = [row]
        tds = "".join(f'<td style="text-align:{esc(aligns[j] if j < len(aligns) else "left")}">{esc(v)}</td>'
                      for j, v in enumerate(vals))
        body.append(f"<tr>{tds}</tr>")
    ths = "".join(
        f'<th style="text-align:{esc(aligns[j])}"'
        + (f' data-sort="{j}"' if "sort" in feats else "")
        + f'>{esc(labels[j])}</th>' for j in range(len(labels)))
    tid = ctx["uid"]("tb")
    search = ""
    if "search" in feats:
        search = f'<input class="ps-tb-search" data-tb="{tid}" placeholder="搜索…">'
    if "sort" in feats or "search" in feats:
        ctx["need_table_js"] = True
    return (f'<div class="ps-tbwrap">{search}'
            f'<table class="ps-tb" id="{tid}"><thead><tr>{ths}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _echart_option(b, rep):
    """Build a deterministic echarts option from spec data. Text stays theme-safe."""
    kind = norm_enum(b.get("kind"), ("bar", "line", "area", "pie", "donut", "scatter",
                                     "radar", "gauge", "funnel", "heatmap", "sankey"),
                     "bar", rep, "chart.kind")
    cats = as_list(b.get("categories"))
    series_in = as_list(b.get("series"))
    horizontal = as_bool(b.get("horizontal"))
    stacked = as_bool(b.get("stacked"))
    axis_txt = {"axisLabel": {"color": "#9aa3b2"}, "axisLine": {"lineStyle": {"color": "#3a4150"}},
                "splitLine": {"lineStyle": {"color": "rgba(120,130,150,.15)"}}}
    PAL = ["#5b8ff9", "#5ad8a6", "#f6bd16", "#e8684a", "#6dc8ec", "#9270ca", "#ff9d4d", "#269a99"]
    opt = {"color": PAL, "tooltip": {"trigger": "item" if kind in ("pie", "donut", "funnel") else "axis"},
           "textStyle": {"color": "#c7ccd6"}, "grid": {"left": 48, "right": 24, "top": 40, "bottom": 36, "containLabel": True}}
    if b.get("title"):
        opt["title"] = {"text": b["title"], "textStyle": {"color": "#e9ecf3", "fontSize": 14}}
    if len(series_in) > 1:
        opt["legend"] = {"textStyle": {"color": "#9aa3b2"}, "top": 8}

    def sdata(s):
        return s.get("data", []) if isinstance(s, dict) else s

    if kind in ("bar", "line", "area"):
        catax = dict({"type": "category", "data": cats}, **axis_txt)
        valax = dict({"type": "value"}, **axis_txt)
        opt["xAxis"], opt["yAxis"] = (valax, catax) if horizontal else (catax, valax)
        if b.get("unit"):
            (opt["xAxis"] if horizontal else opt["yAxis"])["name"] = b["unit"]
        opt["series"] = []
        for s in series_in:
            se = {"type": "line" if kind in ("line", "area") else "bar",
                  "name": (s.get("name") if isinstance(s, dict) else ""),
                  "data": sdata(s), "smooth": kind in ("line", "area")}
            if kind == "area":
                se["areaStyle"] = {"opacity": 0.25}
            if stacked:
                se["stack"] = "total"
            opt["series"].append(se)
    elif kind in ("pie", "donut"):
        s = series_in[0] if series_in else {}
        data = sdata(s)
        # accept [{name,value}] or parallel categories+data
        if data and not isinstance(data[0], dict):
            data = [{"name": (cats[i] if i < len(cats) else str(i)), "value": v} for i, v in enumerate(data)]
        opt["series"] = [{"type": "pie", "radius": ["45%", "70%"] if kind == "donut" else "65%",
                          "data": data, "label": {"color": "#c7ccd6"}}]
    elif kind == "radar":
        inds = [{"name": c} for c in cats]
        opt["radar"] = {"indicator": inds or [{"name": "A"}], "axisName": {"color": "#9aa3b2"}}
        opt["series"] = [{"type": "radar", "data": [{"name": (s.get("name") if isinstance(s, dict) else ""),
                          "value": sdata(s)} for s in series_in]}]
        opt.pop("grid", None)
    elif kind == "gauge":
        s = series_in[0] if series_in else {}
        d = sdata(s)
        val = as_num(d[0] if d else 0)
        opt["series"] = [{"type": "gauge", "data": [{"value": val, "name": s.get("name", "") if isinstance(s, dict) else ""}],
                          "axisLine": {"lineStyle": {"width": 14}}, "detail": {"color": "#e9ecf3"}}]
        opt.pop("grid", None)
    elif kind == "funnel":
        s = series_in[0] if series_in else {}
        data = sdata(s)
        if data and not isinstance(data[0], dict):
            data = [{"name": (cats[i] if i < len(cats) else str(i)), "value": v} for i, v in enumerate(data)]
        opt["series"] = [{"type": "funnel", "data": data, "label": {"color": "#c7ccd6"}}]
        opt.pop("grid", None)
    elif kind == "scatter":
        opt["xAxis"] = dict({"type": "value"}, **axis_txt)
        opt["yAxis"] = dict({"type": "value"}, **axis_txt)
        opt["series"] = [{"type": "scatter", "name": (s.get("name") if isinstance(s, dict) else ""), "data": sdata(s)}
                         for s in series_in]
    elif kind == "heatmap":
        s = series_in[0] if series_in else {}
        opt["xAxis"] = dict({"type": "category", "data": cats}, **axis_txt)
        opt["yAxis"] = dict({"type": "category", "data": as_list(b.get("y_categories"))}, **axis_txt)
        opt["visualMap"] = {"min": 0, "max": 100, "calculable": True, "orient": "horizontal",
                            "left": "center", "bottom": 0, "textStyle": {"color": "#9aa3b2"}}
        opt["series"] = [{"type": "heatmap", "data": sdata(s)}]
        opt["grid"]["bottom"] = 60
    elif kind == "sankey":
        s = series_in[0] if series_in else {}
        opt["series"] = [{"type": "sankey", "data": b.get("nodes", []), "links": b.get("links", sdata(s)),
                          "label": {"color": "#c7ccd6"}}]
        opt.pop("grid", None)
    return opt


def r_chart(b, ctx):
    kind = str(b.get("kind") or "").lower()
    if kind != "sankey" and not [x for x in as_list(b.get("series")) if x not in (None, [], {})]:
        raise ValueError('chart 缺少 series 数据，示例："series":[{"name":"甲","data":[1,2,3]}]')
    pts = sum(len(x.get("data", [])) if isinstance(x, dict) else len(x) if isinstance(x, list) else 0
              for x in as_list(b.get("series")))
    if pts > MAX_CHART_POINTS:
        raise ValueError(f'chart 数据点 {pts} 超过 {MAX_CHART_POINTS} 上限，请抽样或拆分')
    ctx["need"].update(LIB["chart"])
    i = ctx["uid"]("ec")
    h = int(as_num(b.get("height"), 320))
    h = min(800, max(120, h))
    opt = _echart_option(b, ctx["rep"])
    data = js_json(opt)
    ctx["scripts"].append(
        f'try{{var c=echarts.init(document.getElementById("{i}"));c.setOption({data});'
        f'window.__ps_charts.push(c);}}catch(e){{__ps_fail("{i}",e)}}'
    )
    return f'<div class="ps-chart" id="{i}" style="height:{h}px"></div>'


def r_wordcloud(b, ctx):
    if not [x for x in as_list(b.get("items")) if isinstance(x, dict)]:
        raise ValueError('wordcloud 缺少 items，示例："items":[{"text":"词","weight":10}]')
    ctx["need"].update(LIB["wordcloud"])
    i = ctx["uid"]("wc")
    items = [[str(it.get("text")), as_num(it.get("weight"), 1)]
             for it in as_list(b.get("items")) if isinstance(it, dict)]
    data = js_json(items)
    ctx["scripts"].append(
        f'try{{var el=document.getElementById("{i}");'
        f'WordCloud(el,{{list:{data},backgroundColor:"transparent",'
        f'color:function(){{var p=["#5b8ff9","#5ad8a6","#f6bd16","#e8684a","#6dc8ec"];'
        f'return p[Math.floor(el.__i=(el.__i||0)+1)%5];}},'
        f'weightFactor:function(s){{return Math.max(12,s*3);}}}});}}catch(e){{__ps_fail("{i}",e)}}'
    )
    return f'<div class="ps-wc"><canvas id="{i}" width="640" height="300"></canvas></div>'


def r_graph(b, ctx):
    if not as_list(b.get("nodes")):
        raise ValueError('graph 缺少 nodes，示例："nodes":[{"id":"A"}],"edges":[]')
    layout = norm_enum(b.get("layout"), ("force", "circle", "grid", "dagre"), "force", ctx["rep"], "graph.layout")
    ctx["need"].update(LIB["graph_dagre"] if layout == "dagre" else LIB["graph"])
    i = ctx["uid"]("gr")
    nodes = [{"data": {"id": str(n.get("id")), "label": str(n.get("label", n.get("id"))),
                       "group": str(n.get("group", ""))}}
             for n in as_list(b.get("nodes")) if isinstance(n, dict)]
    edges = [{"data": {"source": str(e.get("from")), "target": str(e.get("to")),
                       "label": str(e.get("label", ""))}}
             for e in as_list(b.get("edges")) if isinstance(e, dict)]
    lay = {"force": "cose", "circle": "circle", "grid": "grid", "dagre": "preset"}[layout]
    data = js_json({"nodes": nodes, "edges": edges})
    prelude = ""
    if layout == "dagre":
        prelude = (
            f'var __d={data},__g=new dagre.graphlib.Graph().setGraph({{rankdir:"TB",nodesep:36,ranksep:64}})'
            f'.setDefaultEdgeLabel(function(){{return {{}};}});'
            f'__d.nodes.forEach(function(n){{__g.setNode(n.data.id,{{width:90,height:42}});}});'
            f'__d.edges.forEach(function(e){{__g.setEdge(e.data.source,e.data.target);}});dagre.layout(__g);'
            f'__d.nodes.forEach(function(n){{var p=__g.node(n.data.id);n.position={{x:p.x,y:p.y}};}});'
        )
        elements = "__d"
    else:
        elements = data
    ctx["scripts"].append(
        f'try{{{prelude}cytoscape({{container:document.getElementById("{i}"),'
        f'elements:{elements},'
        f'style:[{{selector:"node",style:{{"background-color":"#5b8ff9","label":"data(label)",'
        f'"color":"#c7ccd6","font-size":"11px","text-valign":"bottom"}}}},'
        f'{{selector:"edge",style:{{"line-color":"#3a4150","target-arrow-color":"#3a4150",'
        f'"target-arrow-shape":"triangle","curve-style":"bezier","label":"data(label)",'
        f'"font-size":"9px","color":"#78829a"}}}}],'
        f'layout:{{name:"{lay}"}}}});}}catch(e){{__ps_fail("{i}",e)}}'
    )
    return f'<div class="ps-graph" id="{i}"></div>'


def r_mermaid(b, ctx):
    if not str(b.get("code") or "").strip():
        raise ValueError('mermaid 缺少 code，示例："code":"graph LR; A-->B"')
    ctx["need"].update(LIB["mermaid"])
    ctx["need_mermaid_init"] = True
    i = ctx["uid"]("mm")
    return f'<div id="{i}"><pre class="mermaid">{esc(b.get("code"))}</pre></div>'


def r_calendar(b, ctx):
    if not [x for x in as_list(b.get("events")) if isinstance(x, dict)]:
        raise ValueError('calendar 缺少 events，示例："events":[{"date":"2026-07-01","title":"事件"}]')
    ctx["need"].update(LIB["calendar"])
    i = ctx["uid"]("cal")
    events = [{"title": str(e.get("title")), "date": str(e.get("date"))}
              for e in as_list(b.get("events")) if isinstance(e, dict)]
    init = str(b.get("initial_date") or (events[0]["date"] if events else ""))
    data = js_json(events)
    idate = f',initialDate:{js_json(init)}' if init else ""
    ctx["scripts"].append(
        f'try{{new FullCalendar.Calendar(document.getElementById("{i}"),'
        f'{{initialView:"dayGridMonth",height:460,headerToolbar:{{left:"",center:"title",right:""}}'
        f'{idate},events:{data}}}).render();}}catch(e){{__ps_fail("{i}",e)}}'
    )
    return f'<div class="ps-cal" id="{i}"></div>'


def _slot_img(b, ctx, slot, caption, zoom, width=""):
    slot = int(as_num(slot, 0))
    if slot < 1 or slot > 20:
        raise ValueError(f"slot 必须是 1-20，收到 {slot}")
    uri = ctx["slots"].get(f"slot{slot}")
    if uri is None:
        uri = ctx["placeholder"](f"slot{slot}")
        ctx["rep"].add("WARN", f"image.slot{slot}", "该插槽未上传图片，已用占位图")
    st = f' style="max-width:{esc(width)}"' if width else ""
    cls = "ps-img ps-zoom" if zoom else "ps-img"
    cap = f'<figcaption class="ps-cap">{esc(caption)}</figcaption>' if caption else ""
    return f'<figure class="ps-fig"><img class="{cls}" src="{esc(uri)}" alt="{esc(caption)}"{st}>{cap}</figure>'


def r_image(b, ctx):
    if as_bool(b.get("zoom")):
        ctx["need_lightbox"] = True
    return _slot_img(b, ctx, b.get("slot"), b.get("caption"), as_bool(b.get("zoom")), b.get("width", ""))


def r_gallery(b, ctx):
    ctx["need_lightbox"] = True
    slots = as_list(b.get("slots"))
    caps = as_list(b.get("captions"))
    inner = "".join(_slot_img(b, ctx, s, caps[i] if i < len(caps) else "", True)
                    for i, s in enumerate(slots))
    return f'<div class="ps-gallery">{inner}</div>'


def r_qrcode(b, ctx):
    ctx["need"].update(LIB["qrcode"])
    i = ctx["uid"]("qr")
    txt = js_json(str(b.get("text") or ""))
    size = int(as_num(b.get("size"), 132))
    cap = f'<div class="ps-cap">{esc(b.get("caption"))}</div>' if b.get("caption") else ""
    ctx["scripts"].append(
        f'try{{new QRCodeStyling({{width:{size},height:{size},data:{txt},'
        f'dotsOptions:{{color:"#c7ccd6"}},backgroundOptions:{{color:"transparent"}}}})'
        f'.append(document.getElementById("{i}"));}}catch(e){{__ps_fail("{i}",e)}}'
    )
    return f'<div class="ps-qr"><div id="{i}"></div>{cap}</div>'


def r_barcode(b, ctx):
    ctx["need"].update(LIB["barcode"])
    i = ctx["uid"]("bc")
    txt = js_json(str(b.get("text") or ""))
    fmt = js_json(str(b.get("format") or "CODE128"))
    ctx["scripts"].append(
        f'try{{JsBarcode("#{i}",{txt},{{format:{fmt},lineColor:"#c7ccd6",'
        f'background:"transparent",width:2,height:60}});}}catch(e){{__ps_fail("{i}",e)}}'
    )
    return f'<div class="ps-bc"><svg id="{i}"></svg></div>'


# ---- containers (recursive) --------------------------------------------------

def r_section(b, ctx):
    inner = render_blocks(as_list(b.get("blocks")), ctx, ctx.get("_depth", 0) + 1)
    sid = ctx["uid"]("sec")
    ctx["toc"].append((b.get("title", ""), sid))
    return f'<section class="ps-section" id="{sid}"><h2 class="ps-sec-t">{esc(b.get("title"))}</h2>{inner}</section>'


def r_card(b, ctx):
    inner = render_blocks(as_list(b.get("blocks")), ctx, ctx.get("_depth", 0) + 1)
    title = f'<div class="ps-card-t">{esc(b.get("title"))}</div>' if b.get("title") else ""
    return f'<div class="ps-card">{title}{inner}</div>'


def r_columns(b, ctx):
    groups = as_list(b.get("blocks"))
    ratio = as_list(b.get("ratio")) or [1] * len(groups)
    cols = []
    for j, g in enumerate(groups):
        w = as_num(ratio[j] if j < len(ratio) else 1, 1)
        cols.append(f'<div class="ps-col" style="flex:{w}">{render_blocks(as_list(g), ctx, ctx.get("_depth", 0) + 1)}</div>')
    return f'<div class="ps-cols">{"".join(cols)}</div>'


def r_tabs(b, ctx):
    ctx["need_tabs_js"] = True
    tid = ctx["uid"]("tabs")
    heads, panes = [], []
    for j, it in enumerate(as_list(b.get("items"))):
        if not isinstance(it, dict):
            continue
        act = " active" if j == 0 else ""
        heads.append(f'<button class="ps-tabh{act}" data-tab="{tid}" data-i="{j}">{esc(it.get("label"))}</button>')
        panes.append(f'<div class="ps-tabp{act}" data-tab="{tid}" data-i="{j}">{render_blocks(as_list(it.get("blocks")), ctx, ctx.get("_depth", 0) + 1)}</div>')
    return f'<div class="ps-tabs" id="{tid}"><div class="ps-tabhs">{"".join(heads)}</div>{"".join(panes)}</div>'


def r_collapse(b, ctx):
    out = []
    for it in as_list(b.get("items")):
        if not isinstance(it, dict):
            continue
        op = " open" if as_bool(it.get("open")) else ""
        out.append(f'<details class="ps-collapse"{op}><summary>{esc(it.get("label"))}</summary>'
                   f'<div>{render_blocks(as_list(it.get("blocks")), ctx, ctx.get("_depth", 0) + 1)}</div></details>')
    return "".join(out)


_CATALOG_DIR = Path(__file__).resolve().parents[1] / "catalog"


# The catalogue is both release evidence and a page that a human must be able
# to inspect.  Keep this presentation layer outside the frozen fixtures: the
# fixtures remain byte-for-byte reproducible from the four development YMLs,
# while every catalogue renderer receives the same, independently tested UI
# constraints.  In particular, raw document/binary evidence must never turn a
# single row into a thousand-pixel wall of text.
_CATALOG_PRESENTATION_CSS = r"""
#suite-root [data-test-key]{min-width:0;overflow-wrap:anywhere}
#suite-root .case-stage,#suite-root .artifact-stage,#suite-root .yb-stage{
  max-width:100%!important;max-height:30rem!important;overflow:auto!important
}
#suite-root .yb-card{
  max-height:47.5rem!important;overflow:auto!important;scrollbar-gutter:stable
}
#suite-root details.ps-catalog-checks,#suite-root details.ps-catalog-full{
  margin:.45rem 0;border:1px solid #475569;border-radius:.5rem;padding:.4rem .55rem;
  background:#0f172a;color:#e2e8f0
}
#suite-root details.ps-catalog-checks>summary,#suite-root details.ps-catalog-full>summary{
  cursor:pointer;color:#bfdbfe;white-space:normal;overflow-wrap:anywhere
}
#suite-root .ps-catalog-summary{display:block;white-space:pre-wrap;overflow-wrap:anywhere}
#suite-root details.ps-catalog-full pre{
  max-height:24rem;margin:.5rem 0 0;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere
}
#suite-root [data-library-artifact]{max-width:100%!important;min-width:0!important}
#suite-root [data-library-artifact] canvas,
#suite-root [data-library-artifact] svg,
#suite-root [data-library-artifact] img,
#suite-root .apexcharts-canvas,
#suite-root .apexcharts-canvas svg,
#suite-root .plotly-graph-div{
  max-width:100%!important
}
#suite-root .apexcharts-canvas,#suite-root .apexcharts-canvas svg,
#suite-root .plotly-graph-div{width:100%!important}
"""


_CATALOG_PRESENTATION_JS = r"""
(function(){
'use strict';
var completed=false,finishing=false;
function str(value){
  var seen=[];
  try{return JSON.stringify(value,function(_key,item){
    if(typeof item==='bigint')return String(item)+'n';
    if(item&&typeof item==='object'){
      if(seen.indexOf(item)!==-1)return '[Circular]';
      seen.push(item);
    }
    return item;
  },2)}catch(_){try{return String(value)}catch(__){return '[unprintable]'}}
}
function brief(value){
  var text=String(value==null?'':value),points=Array.from(text);
  return points.length<=120?text:points.slice(0,119).join('')+'…';
}
function details(label,full){
  var box=document.createElement('details'),head=document.createElement('summary'),pre=document.createElement('pre'),code=document.createElement('code');
  box.className='ps-catalog-full';head.setAttribute('data-catalog-summary','');head.textContent=brief(label);code.textContent=full;
  pre.appendChild(code);box.appendChild(head);box.appendChild(pre);return box;
}
function foldChecks(item,row){
  var list=item.querySelector('.check-list,.checks,.yb-checks');
  if(!list||list.closest('details.ps-catalog-checks'))return;
  var checks=Array.isArray(row&&row.checks)?row.checks:[],entries=Array.from(list.querySelectorAll(':scope > li'));
  entries.forEach(function(entry,index){
    var check=checks[index]||{},full='期望: '+str(check.expected)+'\n实际: '+str(check.actual),summary=document.createElement('code');
    entry.querySelectorAll('code').forEach(function(node){node.remove()});
    summary.className='ps-catalog-summary';summary.setAttribute('data-catalog-summary','');summary.textContent=brief(full);
    entry.appendChild(summary);entry.appendChild(details('展开完整期望值与实际值',full));
  });
  var envelope=document.createElement('details'),head=document.createElement('summary'),passed=checks.filter(function(check){return check&&check.pass===true}).length;
  envelope.className='ps-catalog-checks';head.setAttribute('data-catalog-summary','');head.textContent=brief('验证 '+checks.length+' 项 · 通过 '+passed+' 项（展开查看）');
  list.parentNode.insertBefore(envelope,list);envelope.appendChild(head);envelope.appendChild(list);
  var host=envelope.parentNode;
  Array.from(host.querySelectorAll(':scope > details:not(.ps-catalog-checks):not(.ps-catalog-full)')).forEach(function(node){node.remove()});
  host.appendChild(details('完整机器 evidence（展开查看）',str({artifact:row&&row.artifact,evidence:row&&row.evidence})));
}
function fitArtifacts(){
  document.querySelectorAll('[data-library-artifact]').forEach(function(artifact){
    var width=artifact.getBoundingClientRect().width;if(width<=0)return;
    artifact.querySelectorAll('.apexcharts-canvas svg,svg.apexcharts-svg').forEach(function(svg){
      var intrinsicWidth=Number(svg.getAttribute('width')),intrinsicHeight=Number(svg.getAttribute('height'));
      if(!svg.getAttribute('viewBox')&&intrinsicWidth>0&&intrinsicHeight>0)svg.setAttribute('viewBox','0 0 '+intrinsicWidth+' '+intrinsicHeight);
      svg.setAttribute('preserveAspectRatio','xMidYMid meet');svg.style.setProperty('width','100%','important');svg.style.setProperty('height','auto','important');
    });
    artifact.querySelectorAll('canvas,svg,img,.apexcharts-canvas,.plotly-graph-div,[style*="width"]').forEach(function(node){
      var rect=node.getBoundingClientRect();if(rect.width<=width+1)return;
      node.style.setProperty('max-width','100%','important');
      node.style.setProperty('width','100%','important');
    });
  });
}
function isScrollable(node,axis){
  var style=getComputedStyle(node),overflow=axis==='x'?style.overflowX:style.overflowY;
  return /^(auto|scroll)$/.test(overflow)&&(axis==='x'?node.scrollWidth>node.clientWidth+1:node.scrollHeight>node.clientHeight+1);
}
function clipped(node){
  var rect=node.getBoundingClientRect();if(rect.width<=0||rect.height<=0)return false;
  var artifact=node.closest('[data-library-artifact]');
  if(artifact&&artifact!==node){
    var bound=artifact.getBoundingClientRect();
    if(!isScrollable(artifact,'x')&&(rect.left<bound.left-1||rect.right>bound.right+1))return true;
    if(!isScrollable(artifact,'y')&&(rect.top<bound.top-1||rect.bottom>bound.bottom+1))return true;
  }
  for(var parent=node.parentElement;parent&&parent!==document.documentElement;parent=parent.parentElement){
    var style=getComputedStyle(parent),bound=parent.getBoundingClientRect();
    if(/^(hidden|clip)$/.test(style.overflowX)&&(rect.left<bound.left-1||rect.right>bound.right+1))return true;
    if(/^(hidden|clip)$/.test(style.overflowY)&&(rect.top<bound.top-1||rect.bottom>bound.bottom+1))return true;
    if(isScrollable(parent,'x')||isScrollable(parent,'y'))break;
  }
  return false;
}
function presentationGate(suite){
  var rows=Array.isArray(suite.rows)?suite.rows:[],items=Array.from(document.querySelectorAll('[data-test-key]'));
  var summaries=Array.from(document.querySelectorAll('[data-catalog-summary]'));
  var candidates=Array.from(document.querySelectorAll('[data-library-artifact], [data-library-artifact] canvas, [data-library-artifact] svg, [data-library-artifact] img, [data-library-artifact] .apexcharts-canvas, [data-library-artifact] .plotly-graph-div'));
  var clippedNodes=candidates.filter(clipped),heights=items.map(function(node){return Math.round(node.getBoundingClientRect().height)}),pageHeight=Math.max(document.documentElement.scrollHeight,document.body?document.body.scrollHeight:0);
  var warningRows=Array.from(document.querySelectorAll('#__ofx-static-body tr')).filter(function(row){return /^(WARN|SKIP)$/.test(String(row.cells&&row.cells[0]&&row.cells[0].textContent||'').trim())});
  var result={
    summary_limit:120,
    summaries_at_most_120:summaries.every(function(node){return Array.from(node.textContent||'').length<=120}),
    clipped_element_count:clippedNodes.length,
    clipped_elements:clippedNodes.slice(0,20).map(function(node){var rect=node.getBoundingClientRect();return {tag:node.tagName,id:node.id||'',className:String(node.className&&node.className.baseVal||node.className||'').slice(0,120),width:Math.round(rect.width),height:Math.round(rect.height)}}),
    no_clipped_elements:clippedNodes.length===0,
    max_item_height_px:heights.length?Math.max.apply(Math,heights):0,
    items_at_most_760px:heights.length===rows.length&&heights.every(function(height){return height<=760}),
    page_height_px:pageHeight,
    page_height_per_library_px:rows.length?Math.round(pageHeight/rows.length):null,
    page_height_budget_per_library_px:650,
    page_height_within_budget:rows.length>0&&pageHeight/rows.length<=650,
    static_warning_count:warningRows.length,
    static_warn_and_skip_zero:warningRows.length===0
  };
  result.pass=result.summaries_at_most_120&&result.no_clipped_elements&&result.items_at_most_760px&&result.page_height_within_budget&&result.static_warn_and_skip_zero;
  return result;
}
function finish(){
  var suite=window.__MEANINGFUL_SUITE__;if(!suite||!Array.isArray(suite.rows))throw new Error('catalog suite result missing');
  var byKey=new Map(suite.rows.map(function(row){return [String(row.key),row]}));
  document.querySelectorAll('[data-test-key]').forEach(function(item){foldChecks(item,byKey.get(String(item.getAttribute('data-test-key'))))});
  fitArtifacts();void document.documentElement.offsetHeight;
  var gate=presentationGate(suite),basePass=typeof suite.final_gate_pass==='boolean'?suite.final_gate_pass:(suite.failed===0&&suite.passed===suite.total&&suite.offline_gate_pass===true&&suite.runtime_gate_pass!==false&&suite.diagnostics_pass!==false&&suite.layout_pass!==false&&suite.delayed_stability_pass!==false);
  suite.catalog_presentation_gate=gate;suite.presentation_gate_pass=gate.pass===true;suite.final_gate_pass=basePass&&gate.pass===true;
  var badge=document.getElementById('m-offline');if(badge&&!gate.pass){badge.textContent='FAIL';badge.style.color='var(--bad)'}
  if(!gate.pass)document.title=document.title.replace(/^PASS\s+/,'FAIL ');
}
Object.defineProperty(window,'__ALL_TESTS_DONE__',{configurable:true,enumerable:true,get:function(){return completed},set:function(value){
  if(value===true&&!finishing){finishing=true;requestAnimationFrame(function(){requestAnimationFrame(function(){try{finish()}catch(error){var suite=window.__MEANINGFUL_SUITE__||{};suite.catalog_presentation_gate={pass:false,error:String(error&&error.stack||error)};suite.presentation_gate_pass=false;suite.final_gate_pass=false;window.__MEANINGFUL_SUITE__=suite;document.title=document.title.replace(/^PASS\s+/,'FAIL ')}completed=true})})}else if(value!==true){completed=!!value;finishing=false}
}});
})();
"""


def _catalog_registry():
    registry = json.loads((_CATALOG_DIR / "registry.json").read_text(encoding="utf-8"))
    if registry.get("schema") != "catalog-registry/v1":
        raise ValueError("catalog registry identity mismatch")
    volumes = registry.get("volumes")
    covers = registry.get("covers")
    if not isinstance(volumes, list) or not isinstance(covers, list):
        raise ValueError("catalog registry structure mismatch")
    if len(covers) != 172 or len(covers) != len(set(covers)):
        raise ValueError("catalog registry must cover 172 unique libraries")
    return registry


def r_catalog_demo(b, ctx):
    """Render one fixed all-library verification volume.

    This is deliberately not a generic escape hatch: the JSON supplies only a
    volume number.  Markup, JavaScript, data vectors, expected values, library
    order and assertions all come from the plugin-owned frozen registry.
    """
    volume = int(as_num(b.get("volume"), 1))
    if volume not in (1, 2, 3, 4):
        volume = 1
    fixture_path = _CATALOG_DIR / f"volume{volume:02d}.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture.get("schema") != "catalog-fixture/v1" or fixture.get("volume") != volume:
        raise ValueError("catalog fixture identity mismatch")
    registry = _catalog_registry()
    declared = next((item for item in registry["volumes"] if item.get("volume") == volume), None)
    covers = fixture.get("covers")
    if (
        not isinstance(covers, list)
        or not declared
        or declared.get("file") != fixture_path.name
        or declared.get("count") != fixture.get("count")
        or declared.get("covers") != covers
        or len(covers) != fixture.get("count")
        or len(covers) != len(set(covers))
    ):
        raise ValueError("catalog fixture coverage mismatch")
    ctx["catalog_assets"].extend(fixture.get("assets") or [])
    ctx["extra_styles"].extend(fixture.get("styles") or [])
    ctx["extra_styles"].append(_CATALOG_PRESENTATION_CSS)
    ctx["body_prefix_scripts"].extend(fixture.get("prelude_js") or [])
    ctx["body_prefix_scripts"].append(_CATALOG_PRESENTATION_JS)
    runner = fixture.get("runner_js")
    if runner:
        ctx["scripts"].append(runner)
    ctx["catalog_covers"].extend(fixture.get("covers") or [])
    return str(fixture.get("body_html") or "")


def r_internal_error(b, ctx):
    return _error_card(b.get("title") or "内容块无法处理", b.get("reason") or "",
                       b.get("suggestion") or "按 PageSpec 规范修正该块")


def r_internal_fallback(b, ctx):
    reason = f'<div class="ps-fallback-r">已降级：{esc(b.get("reason"))}</div>' if b.get("reason") else ""
    return f'<div class="ps-fallback"><p class="ps-p">{esc(b.get("text"))}</p>{reason}</div>'




RENDERERS = {
    "heading": r_heading, "text": r_text, "markdown": r_markdown, "callout": r_callout,
    "quote": r_quote, "kv": r_kv, "tags": r_tags, "code": r_code, "formula": r_formula,
    "divider": r_divider, "stat_row": r_stat_row, "progress": r_progress, "timeline": r_timeline,
    "table": r_table, "chart": r_chart, "wordcloud": r_wordcloud, "graph": r_graph,
    "mermaid": r_mermaid, "calendar": r_calendar, "image": r_image, "gallery": r_gallery,
    "qrcode": r_qrcode, "barcode": r_barcode, "section": r_section, "card": r_card,
    "columns": r_columns, "tabs": r_tabs, "collapse": r_collapse,
    "catalog_demo": r_catalog_demo,
    "__error__": r_internal_error, "__fallback__": r_internal_fallback,
}

_ALIAS_TYPE = {"h": "heading", "title": "heading", "p": "text", "paragraph": "text",
               "md": "markdown", "note": "callout", "alert": "callout", "img": "image",
               "graph_dagre": "graph", "list": "kv", "stats": "stat_row", "metric": "stat_row",
               "qr": "qrcode", "flow": "mermaid", "chart_bar": "chart"}


def _closest_type(t):
    import difflib
    m = difflib.get_close_matches(t, list(RENDERERS), n=1, cutoff=0.6)
    return m[0] if m else None


def render_block(b, ctx):
    if not isinstance(b, dict):
        ctx["rep"].add("SKIP", "block", f"块必须是对象，收到 {type(b).__name__}")
        return _error_card("块不是对象", f"收到 {type(b).__name__}", '每个块应为 {"type":"...",...}')
    t = b.get("type")
    if not isinstance(t, str):
        return _error_card("块缺少 type", "", '加上 "type"，如 "type":"text"')
    tl = t.strip().lower()
    tl = _ALIAS_TYPE.get(tl, tl)
    if tl not in RENDERERS:
        near = _closest_type(tl)
        sug = f'最接近 "{near}"' if near else "见规范块清单"
        ctx["rep"].add("SKIP", f"type={t}", "未知块类型", sug)
        return _error_card(f"未知块类型：{esc(t)}", "拼写错误或不受支持", sug)
    if tl != t.strip().lower() and _ALIAS_TYPE.get(t.strip().lower()):
        ctx["rep"].add("INFO", f"type={t}", f"已归一为 '{tl}'")
    try:
        return RENDERERS[tl](b, ctx)
    except Exception as e:
        ctx["rep"].add("SKIP", f"type={tl}", f"渲染失败：{str(e)[:100]}")
        return _error_card(f"{esc(tl)} 块无法渲染", esc(str(e)[:120]), "检查该块字段是否完整")


def _error_card(title, reason, suggestion):
    r = f'<div class="ps-err-r">原因：{esc(reason)}</div>' if reason else ""
    s = f'<div class="ps-err-s">建议：{esc(suggestion)}</div>' if suggestion else ""
    return f'<div class="ps-errcard"><div class="ps-err-t">⚠ {esc(title)}</div>{r}{s}</div>'


def render_blocks(blocks, ctx, depth: int = 0):
    if depth > MAX_DEPTH:
        ctx["rep"].add("SKIP", "nesting", f"嵌套超过 {MAX_DEPTH} 层，更深内容未渲染")
        return _error_card(f"嵌套超过 {MAX_DEPTH} 层", "容器套容器过深", "拍平结构或减少嵌套")
    out = []
    for b in blocks:
        ctx["nblocks"] = ctx.get("nblocks", 0) + 1
        if ctx["nblocks"] > MAX_BLOCKS:
            if not ctx.get("_blocks_capped"):
                ctx["_blocks_capped"] = True
                ctx["rep"].add("SKIP", "blocks", f"块总数超过 {MAX_BLOCKS}，其余未渲染")
                out.append(_error_card(f"块总数超过 {MAX_BLOCKS}", "输入过大", "拆分为多个页面"))
            break
        ctx["_depth"] = depth
        out.append(render_block(b, ctx))
    return "".join(out)


# ============================ document assembly ===============================

BASE_CSS = """
:root{--bg:#12151c;--panel:#1a1f2a;--panel2:#212836;--line:#2c3444;--ink:#e9ecf3;
--ink2:#aab3c4;--mut:#78829a;--accent:#5b8ff9;--good:#5ad8a6;--warn:#f6bd16;--danger:#e8684a}
:root[data-theme=light]{--bg:#f5f6f8;--panel:#fff;--panel2:#f0f2f5;--line:#e2e5ea;
--ink:#1a1f2a;--ink2:#414a5a;--mut:#6b7480;--accent:#2563eb}
*{box-sizing:border-box}
html,body{overflow-x:hidden;margin:0}
body{background:var(--bg);color:var(--ink);font-size:16px;line-height:1.8;overflow-wrap:anywhere;
font-family:-apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
img,canvas,svg,video{max-width:100%}
.ps-wrap{max-width:920px;margin:0 auto;padding:28px 22px 64px}
.ps-header{padding:16px 0 26px;border-bottom:1px solid var(--line);margin-bottom:28px}
.ps-header h1{font-size:clamp(24px,4vw,34px);font-weight:800;margin:0 0 8px;text-wrap:balance}
.ps-sub{color:var(--ink2);font-size:16px}
.ps-badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.ps-badge{background:var(--panel);border:1px solid var(--line);border-radius:99px;padding:3px 13px;font-size:12.5px;color:var(--accent)}
.ps-h{font-weight:700;line-height:1.4;text-wrap:balance;margin:26px 0 10px}
.ps-h1{font-size:26px}.ps-h2{font-size:22px}.ps-h3{font-size:19px}.ps-h4{font-size:16.5px}
.ps-p{margin:0 0 12px}
.ps-section{margin:34px 0}
.ps-sec-t{font-size:22px;font-weight:800;margin:0 0 16px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.ps-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}
.ps-card-t{font-weight:700;font-size:16.5px;margin-bottom:12px}
.ps-cols{display:flex;gap:18px;flex-wrap:wrap;margin:16px 0}
.ps-col{flex:1;min-width:260px}
.ps-callout{border-radius:10px;padding:14px 18px;margin:16px 0;border-left:3px solid var(--accent);background:var(--panel)}
.ps-callout-t{font-weight:700;margin-bottom:4px}
.ps-info{border-left-color:var(--accent)}.ps-success{border-left-color:var(--good)}
.ps-warning{border-left-color:var(--warn)}.ps-danger{border-left-color:var(--danger)}
.ps-quote{border-left:3px solid var(--mut);margin:16px 0;padding:6px 18px;color:var(--ink2);font-style:italic}
.ps-quote-src{margin-top:8px;color:var(--mut);font-size:13px;font-style:normal}
.ps-kv{display:grid;gap:10px;margin:16px 0}
.ps-kv-i{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 14px;display:flex;justify-content:space-between;gap:12px}
.ps-kv-k{color:var(--mut)}.ps-kv-v{font-weight:600}
.ps-tags{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
.ps-tag{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:3px 12px;font-size:13px}
.ps-code{background:#0d1016;border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow-x:auto;margin:16px 0}
.ps-code code{font-family:"SF Mono",Consolas,monospace;font-size:13px}
.ps-formula{margin:16px 0;overflow-x:auto}
.ps-divider{border:none;border-top:1px solid var(--line);margin:24px 0}
.ps-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin:16px 0}
.ps-stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.ps-stat-v{font-size:26px;font-weight:800;font-variant-numeric:tabular-nums}
.ps-stat-u{font-size:14px;color:var(--mut);margin-left:4px;font-weight:400}
.ps-stat-l{color:var(--ink2);font-size:13.5px;margin-top:2px}
.ps-stat-d{margin-left:8px;font-weight:700}.ps-up{color:var(--good)}.ps-down{color:var(--danger)}.ps-flat{color:var(--mut)}
.ps-prog{margin:16px 0}
.ps-prog-r{display:grid;grid-template-columns:130px 1fr 56px;gap:12px;align-items:center;padding:5px 0}
.ps-prog-l{color:var(--ink2);font-size:14px;text-align:right}
.ps-prog-bar{height:12px;background:var(--panel2);border-radius:99px;overflow:hidden}
.ps-prog-bar>span{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--good))}
.ps-prog-v{font-variant-numeric:tabular-nums;font-size:14px}
.ps-tl{margin:16px 0;padding-left:8px}
.ps-tl-i{display:grid;grid-template-columns:16px 90px 1fr;gap:12px;padding:8px 0;position:relative}
.ps-tl-dot{width:11px;height:11px;border-radius:99px;background:var(--accent);margin-top:6px}
.ps-tl-time{color:var(--mut);font-size:13px}
.ps-tl-t{font-weight:600}.ps-tl-d{color:var(--ink2);font-size:14px}
.ps-tbwrap{overflow-x:auto;margin:16px 0}
.ps-tb-search{margin-bottom:10px;padding:7px 12px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);width:min(280px,100%)}
.ps-tb{border-collapse:collapse;width:100%;font-size:14px}
.ps-tb th{background:var(--panel2);padding:9px 14px;text-align:left;border-bottom:1px solid var(--line);font-weight:700}
.ps-tb th[data-sort]{cursor:pointer;user-select:none}
.ps-tb th[data-sort]:after{content:" ⇅";color:var(--mut);font-size:11px}
.ps-tb td{padding:9px 14px;border-bottom:1px solid var(--line);color:var(--ink2)}
.ps-chart,.ps-graph,.ps-cal{width:100%;margin:16px 0}
.ps-graph{height:320px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.ps-wc{margin:16px 0;text-align:center}
.mermaid{margin:16px 0;text-align:center}
.ps-fig{margin:16px 0}
.ps-img{border-radius:10px;display:block}
.ps-zoom{cursor:zoom-in}
.ps-cap{color:var(--mut);font-size:13px;margin-top:6px;text-align:center}
.ps-gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin:16px 0}
.ps-gallery .ps-fig{margin:0}
.ps-qr,.ps-bc{margin:16px 0;text-align:center}
.ps-tabs{margin:16px 0}
.ps-tabhs{display:flex;gap:4px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.ps-tabh{background:none;border:none;color:var(--ink2);padding:9px 16px;cursor:pointer;font-size:14.5px;border-bottom:2px solid transparent;margin-bottom:-1px}
.ps-tabh.active{color:var(--accent);border-bottom-color:var(--accent)}
.ps-tabp{display:none;padding-top:14px}.ps-tabp.active{display:block}
.ps-collapse{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:10px 0;padding:0 16px}
.ps-collapse summary{cursor:pointer;padding:12px 0;font-weight:600}
.ps-collapse[open] summary{border-bottom:1px solid var(--line);margin-bottom:10px}
.ps-collapse>div{padding-bottom:12px}
.ps-errcard{background:rgba(232,104,74,.08);border:1px solid rgba(232,104,74,.4);border-radius:10px;padding:14px 18px;margin:14px 0}
.ps-err-t{color:var(--danger);font-weight:700}
.ps-err-r,.ps-err-s{color:var(--ink2);font-size:14px;margin-top:4px;font-family:monospace}
.ps-fallback{background:rgba(246,189,22,.08);border:1px solid rgba(246,189,22,.35);border-radius:10px;padding:12px 16px;margin:14px 0}
.ps-fallback-r{color:var(--mut);font-size:12px;margin-top:5px}
.ps-toc{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 20px;margin:0 0 24px}
.ps-toc a{color:var(--accent);text-decoration:none;display:block;padding:3px 0;font-size:14.5px}
#__ofx-report{margin:40px 0 0;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:0 18px}
#__ofx-report summary{cursor:pointer;padding:14px 0;color:var(--mut);font-size:13.5px}
#__ofx-report table{width:100%;border-collapse:collapse;font-size:12.5px;font-family:monospace;margin-bottom:14px}
#__ofx-report td{padding:4px 8px;border-top:1px solid var(--line);vertical-align:top;color:var(--ink2)}
.ps-footer{margin-top:40px;padding-top:20px;border-top:1px solid var(--line);color:var(--mut);font-size:13px;text-align:center}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

HELPER_JS = r"""
window.__ps_charts=[];
window.__ps_runtime_errors=[];
window.__ps_record_runtime=function(id,e){try{
 var msg=String((e&&e.message)||e||'未知错误').slice(0,160);
 var item={level:'RUNTIME',where:String(id||'runtime').slice(0,80),message:msg,suggestion:'请检查该组件的数据；其他组件不受影响'};
 window.__ps_runtime_errors.push(item);
 var data=document.getElementById('__ofx-runtime-data');if(data)data.textContent=JSON.stringify(window.__ps_runtime_errors);
 var body=document.getElementById('__ofx-runtime-body');if(body){
  var tr=document.createElement('tr');[item.level,item.where,item.message+'　→ '+item.suggestion].forEach(function(v){var td=document.createElement('td');td.textContent=v;tr.appendChild(td);});body.appendChild(tr);
 }
 var sum=document.getElementById('__ofx-report-summary');if(sum){sum.setAttribute('data-runtime-errors',String(window.__ps_runtime_errors.length));sum.textContent=sum.getAttribute('data-static-label')+' · 运行错误 '+window.__ps_runtime_errors.length+'（点击展开）';}
}catch(_){}};
window.__ps_fail=function(id,e){try{var el=document.getElementById(id);if(!el)return;
 var d=document.createElement('div');d.className='ps-errcard';
 var t=document.createElement('div');t.className='ps-err-t';t.textContent='⚠ 组件运行失败';
 var r=document.createElement('div');r.className='ps-err-r';r.textContent=String((e&&e.message)||e).slice(0,160);
 d.appendChild(t);d.appendChild(r);el.innerHTML='';el.appendChild(d);
 __ps_record_runtime(id,e);}catch(_){}};
window.__ps_harden=function(root){try{
 (root||document).querySelectorAll('*').forEach(function(el){
  ['src','poster','background'].forEach(function(a){if(el.hasAttribute(a)){var v=el.getAttribute(a)||'';if(!/^(?:data:|blob:)/i.test(v))el.removeAttribute(a);}});
  if(el.hasAttribute('srcset'))el.removeAttribute('srcset');
  ['href','xlink:href','formaction','action'].forEach(function(a){if(el.hasAttribute(a)){var v=el.getAttribute(a)||'';if(!/^#/.test(v))el.removeAttribute(a);}});
  var st=el.getAttribute('style')||'';if(/url\s*\(|@import|expression\s*\(/i.test(st))el.removeAttribute('style');
 });
}catch(e){__ps_record_runtime('resource-guard',e);}};
window.__ps_render_markdown=function(id,raw){try{
 var el=document.getElementById(id);if(!el)return;
 var plain=String(raw||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
 var out=marked.parse(plain,{mangle:false,headerIds:false});
 el.innerHTML=DOMPurify.sanitize(out,{ALLOWED_TAGS:['p','br','strong','em','del','code','pre','blockquote','ul','ol','li','h1','h2','h3','h4','h5','h6','hr','table','thead','tbody','tr','th','td'],ALLOWED_ATTR:[]});
 __ps_harden(el);
}catch(e){__ps_fail(id,e);}};
addEventListener('resize',function(){(window.__ps_charts||[]).forEach(function(c){try{c.resize();}catch(e){}});});
// tabs
document.addEventListener('click',function(e){
 var h=e.target.closest('.ps-tabh');if(h){var id=h.getAttribute('data-tab'),i=h.getAttribute('data-i');
  document.querySelectorAll('.ps-tabh[data-tab="'+id+'"]').forEach(function(x){x.classList.toggle('active',x===h);});
  document.querySelectorAll('.ps-tabp[data-tab="'+id+'"]').forEach(function(x){x.classList.toggle('active',x.getAttribute('data-i')===i);});
  (window.__ps_charts||[]).forEach(function(c){try{c.resize();}catch(e){}});}
});
// table sort + search (zero library)
document.addEventListener('click',function(e){
 var th=e.target.closest('.ps-tb th[data-sort]');if(!th)return;
 var tb=th.closest('table'),ci=+th.getAttribute('data-sort'),body=tb.tBodies[0];
 var dir=th.__d=(th.__d===1?-1:1);
 var rows=[].slice.call(body.rows);
 rows.sort(function(a,b){var ac=a.cells[ci],bc=b.cells[ci];if(!ac||!bc)return 0;var x=ac.textContent.trim(),y=bc.textContent.trim();
  var nx=parseFloat(x.replace(/[^0-9.\\-]/g,'')),ny=parseFloat(y.replace(/[^0-9.\\-]/g,''));
  if(!isNaN(nx)&&!isNaN(ny))return (nx-ny)*dir;return x.localeCompare(y,'zh')*dir;});
 rows.forEach(function(r){body.appendChild(r);});
});
document.addEventListener('input',function(e){
 var s=e.target.closest('.ps-tb-search');if(!s)return;
 var tb=document.getElementById(s.getAttribute('data-tb')),q=s.value.toLowerCase();
 [].slice.call(tb.tBodies[0].rows).forEach(function(r){
  r.style.display=r.textContent.toLowerCase().indexOf(q)>-1?'':'none';});
});
// lightbox (zero library)
document.addEventListener('click',function(e){
 var im=e.target.closest('.ps-zoom');if(!im)return;
 var o=document.createElement('div');o.setAttribute('style','position:fixed;inset:0;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;z-index:99999;cursor:zoom-out;padding:20px');
 var big=document.createElement('img');big.src=im.src;big.setAttribute('style','max-width:100%;max-height:100%;border-radius:8px');
 o.appendChild(big);o.onclick=function(){o.remove();};document.body.appendChild(o);
 o.tabIndex=-1;o.focus();addEventListener('keydown',function k(ev){if(ev.key==='Escape'){o.remove();removeEventListener('keydown',k);}});
});
addEventListener('DOMContentLoaded',function(){
 __ps_harden(document);
 try{performance.getEntriesByType('resource').forEach(function(x){if(/^https?:/i.test(x.name))__ps_record_runtime('network','检测到外部资源：'+x.name.slice(0,100));});}catch(e){}
});
"""

MERMAID_INIT = (
    'try{mermaid.initialize({startOnLoad:false,theme:'
    '(document.documentElement.getAttribute("data-theme")==="light"?"default":"dark"),'
    'securityLevel:"strict",maxTextSize:20000,maxEdges:2000});'
    'document.querySelectorAll(".mermaid").forEach(function(el){'
    'mermaid.run({nodes:[el]}).then(function(){__ps_harden(el);}).catch(function(e){__ps_fail(el.parentElement.id,e);});});'
    '}catch(e){document.querySelectorAll(".mermaid").forEach(function(el){__ps_fail(el.parentElement.id,e);});}'
)


def _report_html(rep: Report) -> str:
    c = rep.counts
    visible_items = rep.items[:rep.MAX_ITEMS]
    rows = "".join(
        f'<tr data-decision-id="{it["id"]}"><td>{esc(it["level"])}</td><td>{esc(it["where"])}</td>'
        f'<td>{esc(it["message"])}{("　→ "+esc(it["suggestion"])) if it["suggestion"] else ""}</td></tr>'
        for it in visible_items)
    hidden_count = len(rep.items) - len(visible_items)
    if hidden_count:
        rows += (
            '<tr><td>INFO</td><td>/report</td><td>'
            f'可见表格仅展开前 {rep.MAX_ITEMS} 条；其余 {hidden_count} 条没有丢失，'
            '均完整保存在本文件的 #__ofx-report-data JSON 中（每条含 id 与 JSON Pointer）。'
            '</td></tr>'
        )
    empty = ("" if rep.items else
             '<div style="padding:0 0 14px;color:var(--mut)">无静态归一、警告或降级项；运行期错误仍会在下表追加。</div>')
    data = js_json(rep.items)
    static_label = f'离线导出报告 · 归一 {c["INFO"]} · 警告 {c["WARN"]} · 降级 {c["SKIP"]}'
    return (
        '<details id="__ofx-report">'
        f'<summary id="__ofx-report-summary" data-static-label="{esc(static_label)}" '
        f'data-runtime-errors="0">{esc(static_label)} · 运行错误 0（点击展开）</summary>'
        f'{empty}<table><tbody id="__ofx-static-body">{rows}</tbody>'
        '<tbody id="__ofx-runtime-body"></tbody></table>'
        f'<script type="application/json" id="__ofx-report-data">{data}</script>'
        '<script type="application/json" id="__ofx-runtime-data">[]</script>'
        '</details>'
    )


def render_document(raw_spec, ctx_inputs) -> tuple[str, Report, dict]:
    """
    ctx_inputs: {
      'slots': {slotN: dataURI}, 'placeholder': fn(slotname)->uri,
      'load_libs': fn(set[str])->(head_css_html, body_js_html, missing_list),
      'nonce': CSP nonce generated by the orchestration layer,
      'pre_warnings': warnings detected before rendering (for example slots),
    }
    Returns (full_html, report, meta{needed_libs,missing,fatal}).
    """
    rep = Report()
    for item in ctx_inputs.get("pre_warnings", []):
        if isinstance(item, dict):
            rep.add(item.get("level", "WARN"), item.get("where", "input"),
                    item.get("message", ""), item.get("suggestion", ""))
        else:
            rep.add("WARN", "input", item)

    spec, hard, parse_events = parse_spec(raw_spec)
    for item in parse_events:
        rep.add(item.get("level", "INFO"), item.get("where", "/"),
                item.get("message", ""), item.get("suggestion", ""))
    if hard:
        return _hard_error_page(hard), rep, {"fatal": hard}
    spec, hard = pagespec_validate.normalize_spec(spec, rep)
    if hard:
        return _hard_error_page(hard), rep, {"fatal": hard}

    blocks = spec["blocks"]
    doc = spec.get("doc") if isinstance(spec.get("doc"), dict) else {}
    theme = doc.get("theme", "dark")
    nonce = _safe_text(ctx_inputs.get("nonce") or "")

    counter = {"n": 0}

    def uid(prefix):
        counter["n"] += 1
        return f"ps-{prefix}-{counter['n']}"

    ctx = {
        "need": set(), "scripts": [], "toc": [], "rep": rep, "uid": uid,
        "slots": ctx_inputs.get("slots", {}), "placeholder": ctx_inputs.get("placeholder", lambda s: ""),
        "need_table_js": False, "need_tabs_js": False, "need_lightbox": False,
        "need_mermaid_init": False,
        "catalog_assets": [], "catalog_covers": [], "extra_styles": [],
        "body_prefix_scripts": [],
    }
    body_html = render_blocks(blocks, ctx)

    # libs
    load_libs = ctx_inputs.get("load_libs")
    load_catalog = ctx_inputs.get("load_catalog_assets")
    if ctx["catalog_assets"]:
        if ctx["need"]:
            raise ValueError("catalog_demo cannot be mixed with normal library-backed blocks")
        head_css, body_js, missing = (
            load_catalog(ctx["catalog_assets"]) if load_catalog else ("", "", ["catalog loader missing"])
        )
    else:
        head_css, body_js, missing = (
            load_libs(ctx["need"]) if load_libs and ctx["need"] else ("", "", [])
        )
    for m in missing:
        rep.add("WARN", "vendor", f"内置库文件缺失：{m}")

    # Always install the runtime reporter and resource guard. Component code is
    # still conditional and only bundled when a block needs it.
    helper = HELPER_JS
    init = "\n".join(ctx["scripts"])
    if ctx["need_mermaid_init"]:
        init += "\n" + MERMAID_INIT

    # header
    hd = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    htitle = hd.get("title") or doc.get("title") or ""
    header = ""
    if htitle or hd.get("subtitle") or hd.get("badges"):
        badges = "".join(f'<span class="ps-badge">{esc(x)}</span>' for x in as_list(hd.get("badges")))
        sub = f'<div class="ps-sub">{esc(hd.get("subtitle"))}</div>' if hd.get("subtitle") else ""
        bd = f'<div class="ps-badges">{badges}</div>' if badges else ""
        header = f'<header class="ps-header"><h1>{esc(htitle)}</h1>{sub}{bd}</header>'

    # toc
    toc = ""
    if as_bool(doc.get("toc")) and ctx["toc"]:
        links = "".join(f'<a href="#{esc(sid)}">{esc(t)}</a>' for t, sid in ctx["toc"])
        toc = f'<nav class="ps-toc">{links}</nav>'

    footer = f'<footer class="ps-footer">{esc(doc.get("footer"))}</footer>' if doc.get("footer") else ""
    report = _report_html(rep)

    title = esc(doc.get("title") or htitle or "离线页面")
    document_language = esc(doc.get("lang") or "zh-CN")
    accent = doc.get("accent", "")
    accent_css = f':root{{--accent:{esc(accent)}}}' if accent else ""
    c = rep.counts
    audit_comment = (f'<!-- PageSpec offline export: static INFO={c["INFO"]}; '
                     f'WARN={c["WARN"]}; SKIP={c["SKIP"]}; runtime report id=__ofx-runtime-data -->')
    extra_styles = "\n".join(ctx["extra_styles"])
    body_prefix = "\n".join(
        f'<script nonce="{esc(nonce)}">{code.replace("</script", "<\\/script")}</script>'
        for code in ctx["body_prefix_scripts"]
    )
    if ctx["catalog_assets"]:
        # The frozen catalogue fixture already owns its semantic <main> shell.
        # Do not nest it in PageSpec's ordinary 920px document wrapper: doing
        # so changes the layout being verified and would turn a test page into
        # a test of the wrapper rather than of the bundled libraries.
        document_content = f"{body_html}\n{report}"
    else:
        document_content = f"""<main class="ps-wrap">
{header}
{toc}
{body_html}
{footer}
{report}
</main>"""

    html = f"""<!DOCTYPE html>
<html lang="{document_language}" data-theme="{theme}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{accent_css}{BASE_CSS}\n{extra_styles}</style>
{head_css}
</head>
<body>
{audit_comment}
{body_prefix}
{document_content}
{body_js}
<script nonce="{esc(nonce)}">{helper}
document.addEventListener('DOMContentLoaded',function(){{
{init}
}});</script>
</body>
</html>"""
    needed = list(dict.fromkeys(ctx["catalog_covers"])) if ctx["catalog_assets"] else sorted(ctx["need"])
    return html, rep, {"needed_libs": needed, "missing": missing,
                      "catalog_covers": list(ctx["catalog_covers"]),
                      "fatal": None, "doc_filename": doc.get("filename", "")}


def _hard_error_page(msg: str) -> str:
    tmpl = ('{"version":1,"blocks":[{"type":"heading","text":"标题"},'
            '{"type":"text","text":"正文"}]}')
    return f"""<!DOCTYPE html><html lang="zh-CN" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>输入无法解析</title>
<style>{BASE_CSS}</style></head><body><main class="ps-wrap">
<div class="ps-errcard"><div class="ps-err-t">⚠ 输入 JSON 无法处理</div>
<div class="ps-err-r">原因：{esc(msg)}</div>
<div class="ps-err-s">最小可用模板：</div>
<pre class="ps-code"><code>{esc(tmpl)}</code></pre></div>
</main></body></html>"""
