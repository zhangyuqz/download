#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply the PageSpec 0.4.0 compatibility-preserving refactor.

This patch is deliberately narrow:
- preserve the existing closed PageSpec language, transport recovery, 172-library
  registry, image slots, and Dify tool input/output contract;
- add a closed all-library showcase that may coexist with ordinary report blocks;
- isolate each catalog volume in one disposable iframe with an explicit
  ready/fail protocol and only one active volume at a time;
- stop hiding root overflow and make wide tables scroll inside their own region;
- keep old YML files valid while exposing an optional form switch for the two
  new full-library report examples.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_regex_once(text: str, pattern: str, repl: str, label: str) -> str:
    result, count = re.subn(pattern, repl, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return result


SHOWCASE_CODE = r'''

CATALOG_SHOWCASE_JS = r"""
window.__ps_catalog_install=function(rootId,dataId){
 var root=document.getElementById(rootId),data=document.getElementById(dataId);
 if(!root||!data)return;
 var entries=[];try{entries=JSON.parse(data.textContent||'[]')}catch(error){__ps_record_runtime(rootId,error);return}
 var active=null,objectUrl='',timeoutId=0,listener=null;
 function setStatus(volume,text,kind){var node=root.querySelector('[data-catalog-status="'+volume+'"]');if(node){node.textContent=text;node.setAttribute('data-kind',kind||'')}}
 function cleanup(){
  if(timeoutId){clearTimeout(timeoutId);timeoutId=0}
  if(listener){removeEventListener('message',listener);listener=null}
  if(active){active.remove();active=null}
  if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl=''}
  document.documentElement.classList.remove('ps-catalog-open');
 }
 async function decode(payload){
  if(typeof DecompressionStream!=='function')throw new Error('当前浏览器缺少离线解压能力，请使用 Chrome/Edge 109 或更高版本');
  var raw=atob(payload),bytes=new Uint8Array(raw.length);for(var i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
  var stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return await new Response(stream).text();
 }
 async function openVolume(volume){
  cleanup();var entry=entries.find(function(item){return +item.volume===+volume});if(!entry)return;
  setStatus(volume,'正在解压…','loading');
  try{
   var html=await decode(entry.gzip_b64);setStatus(volume,'正在初始化…','loading');
   objectUrl=URL.createObjectURL(new Blob([html],{type:'text/html'}));
   var overlay=document.createElement('div');overlay.className='ps-catalog-overlay';
   overlay.innerHTML='<div class="ps-catalog-shell"><div class="ps-catalog-head"><div><strong>全库能力卷 '+entry.volume+'</strong><span data-overlay-status>正在初始化…</span></div><div><button type="button" data-catalog-retry>重试</button><button type="button" data-catalog-close>关闭</button></div></div><iframe title="全库能力卷 '+entry.volume+'" referrerpolicy="no-referrer"></iframe></div>';
   active=overlay;document.body.appendChild(overlay);document.documentElement.classList.add('ps-catalog-open');
   var frame=overlay.querySelector('iframe'),overlayStatus=overlay.querySelector('[data-overlay-status]');
   overlay.querySelector('[data-catalog-close]').addEventListener('click',cleanup);
   overlay.querySelector('[data-catalog-retry]').addEventListener('click',function(){openVolume(volume)});
   listener=function(event){
    if(!frame||event.source!==frame.contentWindow||!event.data||event.data.token!==entry.token)return;
    if(event.data.type==='pagespec-catalog-close'){cleanup();return}
    if(event.data.type==='pagespec-catalog-ready'){
     var ok=event.data.final===true;overlayStatus.textContent=ok?('已就绪 · '+event.data.passed+'/'+event.data.total):('已完成但门禁未通过 · '+event.data.passed+'/'+event.data.total);
     setStatus(volume,ok?'已就绪':'门禁未通过',ok?'ready':'fail');if(timeoutId){clearTimeout(timeoutId);timeoutId=0}
    }
    if(event.data.type==='pagespec-catalog-fail'){
     var message=String(event.data.message||'初始化失败');overlayStatus.textContent=message;setStatus(volume,'初始化失败','fail');if(timeoutId){clearTimeout(timeoutId);timeoutId=0}
    }
   };addEventListener('message',listener);
   timeoutId=setTimeout(function(){overlayStatus.textContent='初始化超过 120 秒，可关闭或重试';setStatus(volume,'初始化超时','fail')},120000);
   frame.src=objectUrl;
  }catch(error){cleanup();setStatus(volume,'无法打开','fail');__ps_record_runtime(rootId,error)}
 }
 root.querySelectorAll('[data-catalog-volume]').forEach(function(button){button.addEventListener('click',function(){openVolume(button.getAttribute('data-catalog-volume'))})});
};
"""


def r_catalog_showcase(b, ctx):
    """Embed all four frozen catalog volumes in one ordinary business report.

    The user cannot choose arbitrary libraries, HTML, JavaScript, or URLs.  The
    plugin builds four trusted standalone pages, gzip-compresses them, and only
    starts the selected page in an isolated disposable iframe.  Closing a volume
    destroys its iframe and releases the Blob URL before another one can run.
    """
    builder = ctx.get("catalog_builder")
    if not callable(builder):
        raise ValueError("catalog_showcase builder is unavailable")
    registry = _catalog_registry()
    payloads = [builder(volume) for volume in (1, 2, 3, 4)]
    expected = {item["volume"]: item for item in registry["volumes"]}
    for item in payloads:
        volume = int(item.get("volume", 0))
        if volume not in expected or item.get("count") != expected[volume].get("count"):
            raise ValueError(f"catalog showcase volume {volume} identity mismatch")
    ctx["catalog_covers"].extend(registry["covers"])
    if not ctx.get("_catalog_showcase_runtime"):
        ctx["_catalog_showcase_runtime"] = True
        ctx["body_prefix_scripts"].append(CATALOG_SHOWCASE_JS)
    root_id, data_id = ctx["uid"]("showcase"), ctx["uid"]("showcase-data")
    ctx["scripts"].append(f'__ps_catalog_install("{root_id}","{data_id}");')
    profile = str(b.get("profile") or "general").strip().lower()
    title = b.get("title") or ("全库能力与业务展示" if profile == "general" else "全库能力展示")
    cards = []
    labels = {
        1: "数据趋势与基础可视化", 2: "知识办公与内容生产",
        3: "交互组件与创作工具", 4: "兼容工具与高级能力",
    }
    for item in payloads:
        volume = int(item["volume"])
        cards.append(
            f'<button type="button" class="ps-catalog-card" data-catalog-volume="{volume}">'
            f'<span class="ps-catalog-no">卷 {volume:02d}</span>'
            f'<strong>{esc(labels[volume])}</strong>'
            f'<span>{item["count"]} 个库 · 点击按需加载</span>'
            f'<em data-catalog-status="{volume}">未加载</em></button>'
        )
    index = "".join(f'<span>{esc(name)}</span>' for name in registry["covers"])
    payload_json = js_json(payloads)
    return (
        f'<section class="ps-showcase" id="{root_id}"><div class="ps-showcase-title"><h2>{esc(title)}</h2>'
        '<p>172 个库全部封装在本报告中；一次只运行一卷，关闭后立即销毁，避免全局样式和运行时互相污染。</p></div>'
        f'<div class="ps-catalog-grid">{"".join(cards)}</div>'
        f'<details class="ps-catalog-index"><summary>查看 172 库完整索引</summary><div>{index}</div></details>'
        f'<script type="application/json" id="{data_id}">{payload_json}</script></section>'
    )
'''


def patch_pagespec(root: Path) -> None:
    path = root / "tools/pagespec.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "import json\nimport re\n", "import json\nimport re\n", "pagespec imports")
    table = '''def r_table(b, ctx):
    # Wide tables remain readable by scrolling only inside their own container.
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
    for row_index, row in enumerate(rows):
        if isinstance(row, dict):
            vals = [row.get(k, "") for k in keys]
        elif isinstance(row, list):
            vals = list(row)
        else:
            vals = [row]
        if len(vals) != len(cols):
            original = len(vals)
            vals = (vals + [""] * len(cols))[:len(cols)]
            ctx["rep"].add("WARN", f"table.rows/{row_index}",
                           f"行字段数 {original} 与列数 {len(cols)} 不一致，已补空或截断")
        tds = "".join(
            f'<td style="text-align:{esc(aligns[j] if j < len(aligns) else "left")}">{esc(vals[j])}</td>'
            for j in range(len(cols))
        )
        body.append(f"<tr>{tds}</tr>")
    ths = "".join(
        f'<th style="text-align:{esc(aligns[j])}"'
        + (f' data-sort="{j}"' if "sort" in feats else "")
        + f'>{esc(labels[j])}</th>' for j in range(len(labels)))
    tid = ctx["uid"]("tb")
    search = f'<input class="ps-tb-search" data-tb="{tid}" placeholder="搜索…">' if "search" in feats else ""
    if "sort" in feats or "search" in feats:
        ctx["need_table_js"] = True
    minimum = max(640, min(7000, len(cols) * 140))
    return (f'<div class="ps-tbwrap">{search}'
            f'<table class="ps-tb" id="{tid}" style="min-width:{minimum}px"><thead><tr>{ths}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')
'''
    text = replace_regex_once(text, r"def r_table\(b, ctx\):\n.*?\n\ndef _echart_option", table + "\n\ndef _echart_option", "table renderer")
    text = replace_once(text, "\ndef r_internal_error(b, ctx):", SHOWCASE_CODE + "\n\ndef r_internal_error(b, ctx):", "catalog showcase insertion")
    text = replace_once(text, '    "catalog_demo": r_catalog_demo,\n', '    "catalog_demo": r_catalog_demo, "catalog_showcase": r_catalog_showcase,\n', "renderer registry")
    text = replace_once(text, '"qr": "qrcode", "flow": "mermaid", "chart_bar": "chart"}', '"qr": "qrcode", "flow": "mermaid", "chart_bar": "chart",\n               "showcase": "catalog_showcase", "全库展示": "catalog_showcase"}', "renderer aliases")
    text = replace_once(text, "html,body{overflow-x:hidden;margin:0}", "html,body{margin:0;max-width:100%}", "root overflow")
    text = replace_once(text, "line-height:1.8;overflow-wrap:anywhere;", "line-height:1.8;overflow-wrap:break-word;", "body wrapping")
    text = replace_once(text, ".ps-wrap{max-width:920px;margin:0 auto;padding:28px 22px 64px}", ".ps-wrap{width:100%;max-width:920px;margin:0 auto;padding:28px 22px 64px}", "document wrapper")
    text = replace_once(text, ".ps-tbwrap{overflow-x:auto;margin:16px 0}", ".ps-tbwrap{max-width:100%;overflow-x:auto;overscroll-behavior-inline:contain;margin:16px 0;border:1px solid var(--line);border-radius:10px}", "table wrapper")
    text = replace_once(text, ".ps-tb{border-collapse:collapse;width:100%;font-size:14px}", ".ps-tb{border-collapse:collapse;width:max-content;min-width:100%;font-size:14px}", "table width")
    text = replace_once(text, ".ps-tb th{background:var(--panel2);padding:9px 14px;", ".ps-tb th{background:var(--panel2);padding:9px 14px;white-space:nowrap;min-width:120px;", "table headers")
    text = replace_once(text, ".ps-tb td{padding:9px 14px;", ".ps-tb td{padding:9px 14px;white-space:nowrap;min-width:120px;", "table cells")
    showcase_css = r'''
.ps-showcase{margin:36px 0;padding:22px;background:linear-gradient(145deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:16px}
.ps-showcase-title h2{margin:0 0 6px;font-size:22px}.ps-showcase-title p{margin:0;color:var(--ink2)}
.ps-catalog-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:18px}
.ps-catalog-card{appearance:none;text-align:left;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:16px;cursor:pointer;display:grid;gap:5px}
.ps-catalog-card:hover{border-color:var(--accent);transform:translateY(-1px)}.ps-catalog-card strong{font-size:15px}.ps-catalog-card span{color:var(--ink2);font-size:13px}
.ps-catalog-card em{font-style:normal;color:var(--mut);font-size:12px}.ps-catalog-card em[data-kind=ready]{color:var(--good)}.ps-catalog-card em[data-kind=fail]{color:var(--danger)}
.ps-catalog-no{color:var(--accent)!important;font-weight:700}.ps-catalog-index{margin-top:16px}.ps-catalog-index summary{cursor:pointer;color:var(--accent)}
.ps-catalog-index>div{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;max-height:260px;overflow:auto}.ps-catalog-index span{font-size:11.5px;color:var(--ink2);padding:2px 7px;border:1px solid var(--line);border-radius:6px}
.ps-catalog-open{overflow:hidden}.ps-catalog-overlay{position:fixed;inset:0;z-index:2147483000;background:rgba(5,7,12,.94);padding:12px;display:flex}
.ps-catalog-shell{width:100%;height:100%;background:var(--bg);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:grid;grid-template-rows:auto 1fr}
.ps-catalog-head{min-height:54px;padding:9px 12px;display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);background:var(--panel)}
.ps-catalog-head strong{margin-right:10px}.ps-catalog-head span{color:var(--ink2);font-size:13px}.ps-catalog-head button{margin-left:8px;border:1px solid var(--line);border-radius:8px;padding:7px 12px;background:var(--panel2);color:var(--ink);cursor:pointer}
.ps-catalog-shell iframe{width:100%;height:100%;border:0;background:#12151c}
@media(max-width:640px){.ps-catalog-grid{grid-template-columns:1fr}.ps-catalog-overlay{padding:0}.ps-catalog-shell{border-radius:0;border:0}.ps-catalog-head{align-items:flex-start}.ps-catalog-head>div:first-child{display:grid}.ps-catalog-head button{padding:6px 9px}}
'''
    text = replace_once(text, ":focus-visible{outline:2px solid var(--accent);outline-offset:2px}", showcase_css + "\n:focus-visible{outline:2px solid var(--accent);outline-offset:2px}", "showcase css")
    text = replace_once(text, '    blocks = spec["blocks"]\n', '    blocks = list(spec["blocks"])\n    if ctx_inputs.get("include_all_libraries") and not any(isinstance(block, dict) and block.get("type") == "catalog_showcase" for block in blocks):\n        blocks.append({"type": "catalog_showcase", "profile": "general"})\n', "showcase flag")
    text = replace_once(text, '        "body_prefix_scripts": [],\n', '        "body_prefix_scripts": [], "catalog_builder": ctx_inputs.get("build_catalog_volume"),\n', "catalog builder context")
    text = replace_once(text, '    needed = list(dict.fromkeys(ctx["catalog_covers"])) if ctx["catalog_assets"] else sorted(ctx["need"])\n', '    needed = sorted(set(ctx["need"]) | set(ctx["catalog_covers"]))\n', "needed library summary")
    path.write_text(text, encoding="utf-8")


def patch_validator(root: Path) -> None:
    path = root / "tools/pagespec_validate.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, '    "库验证": "catalog_demo", "目录验证": "catalog_demo",\n', '    "库验证": "catalog_demo", "目录验证": "catalog_demo",\n    "showcase": "catalog_showcase", "全库展示": "catalog_showcase", "能力展示": "catalog_showcase",\n', "validator aliases")
    text = replace_once(text, '    "columns", "tabs", "collapse", "catalog_demo",\n', '    "columns", "tabs", "collapse", "catalog_demo", "catalog_showcase",\n', "validator known types")
    text = replace_once(text, '    "catalog_demo": {"卷": "volume", "卷号": "volume", "分卷": "volume"},\n', '    "catalog_demo": {"卷": "volume", "卷号": "volume", "分卷": "volume"},\n    "catalog_showcase": {"标题": "title", "用途": "profile"},\n', "validator field aliases")
    text = replace_once(text, '    "catalog_demo": {"type", "volume", "fallback"},\n', '    "catalog_demo": {"type", "volume", "fallback"},\n    "catalog_showcase": {"type", "title", "profile", "fallback"},\n', "validator allowed fields")
    path.write_text(text, encoding="utf-8")


def patch_schema(root: Path) -> None:
    path = root / "pagespec.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    definitions = schema["definitions"]
    definitions["catalog_showcase"] = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "type": {"const": "catalog_showcase"},
            "title": {"type": "string", "maxLength": 1000},
            "profile": {"enum": ["general", "library", "phone"]},
            "fallback": {"type": "string", "maxLength": 2000},
        },
        "required": ["type"],
    }
    refs = definitions["block"]["oneOf"]
    ref = {"$ref": "#/definitions/catalog_showcase"}
    if ref not in refs:
        refs.append(ref)
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_render_tool(root: Path) -> None:
    path = root / "tools/render_page.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "import re\nimport secrets\n", "import base64\nimport gzip\nimport json\nimport re\nimport secrets\n", "render imports")
    text = replace_once(text, "connect-src 'none'; worker-src 'none'; child-src 'none'; \n        \"frame-src 'none'; object-src 'none'; base-uri 'none'; \"", "connect-src 'none'; worker-src 'none'; child-src blob:; \n        \"frame-src blob:; object-src 'none'; base-uri 'none'; \"", "CSP frame policy")
    marker = "    # ---- main -------------------------------------------------------------\n"
    method = r'''    def _build_catalog_volume(self, volume: int) -> dict[str, Any]:
        """Compile one trusted catalog volume into a deterministic gzip payload."""
        volume = int(volume)
        if volume not in (1, 2, 3, 4):
            raise ValueError("catalog volume must be 1..4")
        child_nonce = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(24)
        child_spec = {
            "version": 1,
            "profile": "catalog-verification",
            "doc": {"title": f"全库能力展示·卷{volume:02d}", "lang": "zh-CN"},
            "blocks": [{"type": "catalog_demo", "volume": volume}],
        }
        child_ctx = {
            "slots": {}, "placeholder": resources.slot_placeholder,
            "load_libs": self._make_lib_loader(child_nonce),
            "load_catalog_assets": self._make_catalog_loader(child_nonce),
            "nonce": child_nonce, "pre_warnings": [],
            "include_all_libraries": False,
        }
        html, _report, meta = pagespec.render_document(
            json.dumps(child_spec, ensure_ascii=False, separators=(",", ":")), child_ctx
        )
        if meta.get("fatal"):
            raise ValueError(f"catalog volume {volume} failed: {meta['fatal']}")
        bridge = f'''<style>#ps-catalog-return{{position:fixed;right:14px;top:12px;z-index:2147483640;border:1px solid #475063;border-radius:8px;background:#1a1f2a;color:#e9ecf3;padding:8px 12px;cursor:pointer}}</style>
<button type="button" id="ps-catalog-return">返回主报告</button>
<script nonce="{html_escape(child_nonce, quote=True)}">(function(){{var token={pagespec.js_json(token)},volume={volume},sent=false;
function send(type,extra){{if(sent&&type==='pagespec-catalog-ready')return;var data=Object.assign({{type:type,token:token,volume:volume}},extra||{{}});parent.postMessage(data,'*');if(type==='pagespec-catalog-ready')sent=true}}
document.getElementById('ps-catalog-return').addEventListener('click',function(){{send('pagespec-catalog-close')}});
addEventListener('error',function(event){{send('pagespec-catalog-fail',{{message:String(event.message||'运行错误').slice(0,180)}})}});
var started=Date.now(),timer=setInterval(function(){{try{{if(window.__ALL_TESTS_DONE__===true){{clearInterval(timer);var suite=window.__MEANINGFUL_SUITE__||{{}};send('pagespec-catalog-ready',{{passed:+suite.passed||0,total:+suite.total||0,final:suite.final_gate_pass===true}})}}else if(Date.now()-started>120000){{clearInterval(timer);send('pagespec-catalog-fail',{{message:'卷内运行超过 120 秒'}})}}}}catch(error){{clearInterval(timer);send('pagespec-catalog-fail',{{message:String(error&&error.message||error).slice(0,180)}})}}}},250);}})();</script>'''
        html = html.replace("</body>", bridge + "\n</body>", 1)
        html = html.replace("<head>", "<head>\n" + _csp_meta(child_nonce), 1)
        audit_errors = _validate_final_html(html, child_nonce)
        if audit_errors:
            raise ValueError("catalog child audit failed: " + "；".join(audit_errors[:5]))
        registry = pagespec._catalog_registry()
        declared = next(item for item in registry["volumes"] if item["volume"] == volume)
        payload = gzip.compress(html.encode("utf-8"), compresslevel=9, mtime=0)
        return {
            "volume": volume, "count": int(declared["count"]), "token": token,
            "gzip_b64": base64.b64encode(payload).decode("ascii"),
        }

'''
    text = replace_once(text, marker, method + marker, "catalog builder method")
    text = replace_once(text, '        requested_filename = pagespec._safe_text(tool_parameters.get("filename") or "").strip()\n', '        requested_filename = pagespec._safe_text(tool_parameters.get("filename") or "").strip()\n        include_value = tool_parameters.get("include_all_libraries", False)\n        include_all_libraries = include_value is True or str(include_value).strip().lower() in {"1", "true", "yes", "on", "是"}\n', "include option")
    text = replace_once(text, '            "pre_warnings": [\n', '            "build_catalog_volume": self._build_catalog_volume,\n            "include_all_libraries": include_all_libraries,\n            "pre_warnings": [\n', "render context options")
    path.write_text(text, encoding="utf-8")


def patch_tool_yaml(root: Path) -> None:
    path = root / "tools/render_page.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    parameters = data["parameters"]
    if not any(item.get("name") == "include_all_libraries" for item in parameters):
        insert_at = next(i for i, item in enumerate(parameters) if item.get("name") == "slot1")
        parameters.insert(insert_at, {
            "name": "include_all_libraries", "type": "boolean", "required": False,
            "default": False,
            "label": {"en_US": "Include all 172 library capabilities", "zh_Hans": "包含 172 库全能力展示", "zh_Hant": "包含 172 庫全能力展示"},
            "human_description": {
                "en_US": "Embed four lazily loaded, isolated capability volumes in the same offline HTML report.",
                "zh_Hans": "在同一离线 HTML 报告中封装四个按需加载、相互隔离的全库能力卷。",
                "zh_Hant": "在同一離線 HTML 報告中封裝四個按需載入、彼此隔離的全庫能力卷。",
            },
            "llm_description": "Optional closed showcase switch. It never accepts library names, URLs, HTML, CSS or JavaScript.",
            "form": "form",
        })
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")


def patch_workflow_generator(root: Path) -> None:
    path = root / "dev_sources/build_workflows.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, '    expected_form = {f"slot{index}" for index in range(1, 21)}\n', '    expected_form = {f"slot{index}" for index in range(1, 21)} | {"include_all_libraries"}\n', "workflow form contract")
    text = replace_once(text, '    return {\n        f"slot{index}": {"type": "constant", "value": None}\n        for index in range(1, 21)\n    }\n', '    result = {f"slot{index}": {"type": "constant", "value": None} for index in range(1, 21)}\n    result["include_all_libraries"] = {"type": "constant", "value": False}\n    return result\n', "workflow configurations")
    path.write_text(text, encoding="utf-8")


def patch_manifest(root: Path) -> None:
    path = root / "manifest.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["version"] = "0.4.0"
    data["created_at"] = "2026-07-20T23:50:00+08:00"
    data["description"]["en_US"] = "Compile closed PageSpec JSON into one self-contained offline HTML report, with deterministic Dify transport recovery and an optional isolated 172-library capability showcase."
    data["description"]["zh_Hans"] = "把封闭 PageSpec JSON 编译为单个自包含断网 HTML；兼容 Dify 传输形态，并可在同一报告中按需展示相互隔离的 172 库能力。"
    data["description"]["zh_Hant"] = "把封閉 PageSpec JSON 編譯為單一自包含離線 HTML；相容 Dify 傳輸形態，並可在同一報告按需展示彼此隔離的 172 庫能力。"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")


def patch_schema_builder(root: Path) -> None:
    path = root / "build_pagespec_schema.py"
    text = path.read_text(encoding="utf-8")
    if "catalog_showcase" not in text:
        text += '''\n\n# PageSpec 0.4.0 closed showcase definition is post-processed into the generated schema.\ndef _add_catalog_showcase(schema):\n    definitions = schema["definitions"]\n    definitions["catalog_showcase"] = {"type":"object","additionalProperties":False,"properties":{"type":{"const":"catalog_showcase"},"title":{"type":"string","maxLength":1000},"profile":{"enum":["general","library","phone"]},"fallback":{"type":"string","maxLength":2000}},"required":["type"]}\n    ref={"$ref":"#/definitions/catalog_showcase"}\n    if ref not in definitions["block"]["oneOf"]: definitions["block"]["oneOf"].append(ref)\n    return schema\n'''
    path.write_text(text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: pagespec_040_patch.py PLUGIN_ROOT")
    root = Path(sys.argv[1]).resolve()
    patch_pagespec(root)
    patch_validator(root)
    patch_schema(root)
    patch_render_tool(root)
    patch_tool_yaml(root)
    patch_workflow_generator(root)
    patch_manifest(root)
    patch_schema_builder(root)
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").strip()
    if requirements != "dify_plugin>=0.9.0":
        raise RuntimeError(f"unexpected requirements.txt: {requirements!r}")
    print("PageSpec 0.4.0 patch applied")


if __name__ == "__main__":
    main()
