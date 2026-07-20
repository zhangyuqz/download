#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply PageSpec 0.4.1 fixes to the released 0.4.0 source tree.

The patch is intentionally limited to the defects proven by post-release audit:
- restore bounded URL/Base64/gzip/zlib transport recovery without changing the
  strict-JSON fast path or decoding ordinary text as business data;
- nonce every executable script in each frozen catalogue child;
- add boot/progress/idle/hard-timeout protocol and bundled pako fallback;
- bump the already-released version to 0.4.1 while preserving plugin identity,
  PageSpec v1, the frozen 172-library registry and all existing YML contracts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return result


ENCODED_HELPERS = r'''

MAX_ENCODED_LAYERS = 6
_BASE64_TOKEN = re.compile(r"^[A-Za-z0-9+/_-]+={0,2}$")
_DATA_JSON_BASE64 = re.compile(
    r"^data:application/(?:json|[A-Za-z0-9.+-]+\\+json)(?:;charset=[^;,]+)?;base64,(.+)$",
    re.I | re.S,
)


def _bounded_inflate(blob: bytes, wbits: int):
    """Inflate gzip/zlib bytes without permitting a decompression bomb."""
    try:
        decoder = zlib.decompressobj(wbits)
        output = decoder.decompress(blob, MAX_SPEC_BYTES + 1)
        if len(output) > MAX_SPEC_BYTES or decoder.unconsumed_tail:
            return None
        remaining = MAX_SPEC_BYTES + 1 - len(output)
        if remaining <= 0:
            return None
        output += decoder.flush(remaining)
        if len(output) > MAX_SPEC_BYTES or not decoder.eof:
            return None
        return output
    except Exception:
        return None


def _decoded_byte_texts(blob: bytes):
    """Return bounded text and compressed-text candidates from one byte string."""
    byte_candidates: list[tuple[str, bytes]] = [("raw", blob)]
    gzip_value = _bounded_inflate(blob, 16 + zlib.MAX_WBITS)
    if gzip_value is not None:
        byte_candidates.append(("gzip", gzip_value))
    zlib_value = _bounded_inflate(blob, zlib.MAX_WBITS)
    if zlib_value is not None and zlib_value != gzip_value:
        byte_candidates.append(("zlib", zlib_value))

    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for compression, candidate in byte_candidates:
        if len(candidate) > MAX_SPEC_BYTES:
            continue
        for encoding in ("utf-8-sig", "utf-16", "utf-32", "gb18030"):
            try:
                decoded = candidate.decode(encoding)
            except Exception:
                continue
            if decoded not in seen:
                seen.add(decoded)
                results.append((f"{compression}+{encoding}", decoded))
            break
    return results


def _encoded_layer_candidates(text: str):
    """Decode one explicit/bounded transport layer; never execute input."""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, decoded: str):
        if decoded != text and decoded not in seen and len(decoded.encode("utf-8", "surrogatepass")) <= MAX_SPEC_BYTES:
            seen.add(decoded)
            results.append((label, decoded))

    if re.search(r"%(?:7[bBdD]|5[bBdD]|2[27]|60)", text):
        try:
            raw = urllib.parse.unquote_to_bytes(text)
            for label, decoded in _decoded_byte_texts(raw):
                add("percent+" + label, decoded)
        except Exception:
            pass

    match = _DATA_JSON_BASE64.fullmatch(text.strip())
    payloads: list[tuple[str, str]] = []
    if match:
        payloads.append(("data-json-base64", match.group(1)))
    compact = re.sub(r"\\s+", "", text)
    if len(compact) >= 16 and len(compact) <= MAX_SPEC_BYTES * 2 and _BASE64_TOKEN.fullmatch(compact):
        payloads.append(("base64", compact))

    for label, payload in payloads:
        padding = "=" * ((4 - len(payload) % 4) % 4)
        padded = (payload + padding).encode("ascii", "strict")
        for mode, altchars in (("standard", None), ("urlsafe", b"-_")):
            try:
                blob = base64.b64decode(padded, altchars=altchars, validate=True)
            except Exception:
                continue
            for suffix, decoded in _decoded_byte_texts(blob):
                add(f"{label}-{mode}+{suffix}", decoded)
    return results


def _encoded_text_seeds(text: str):
    """Breadth-first encoded transport recovery with exact layer/output bounds."""
    queue: list[tuple[str, list[dict[str, str]], str]] = [(text, [], "encoded")]
    seen = {text}
    outputs: list[tuple[str, str, list[dict[str, str]], int]] = []
    for layer in range(1, MAX_ENCODED_LAYERS + 1):
        next_queue: list[tuple[str, list[dict[str, str]], str]] = []
        for current, events, source in queue:
            for mode, decoded in _encoded_layer_candidates(current):
                if decoded in seen:
                    continue
                seen.add(decoded)
                event = _event(
                    "INFO",
                    f"已解开第 {layer} 层编码传输：{mode}",
                    "/",
                    "直接传 PageSpec JSON 可减少传输包装",
                )
                decoded_events = events + [event]
                decoded_source = source + "+" + mode
                next_queue.append((decoded, decoded_events, decoded_source))
                stripped = decoded.strip().lstrip("\\ufeff")
                if _looks_like_json_intent(stripped) or stripped.startswith(("{", "[", '"', "'", "```json", "```pagespec")):
                    outputs.append((decoded, decoded_source, decoded_events, 945 - layer * 24))
        queue = next_queue
        if not queue:
            break
    return outputs
'''


CATALOG_SHOWCASE_JS = r'''CATALOG_SHOWCASE_JS = r"""
window.__ps_catalog_install=function(rootId,dataId){
 var root=document.getElementById(rootId),data=document.getElementById(dataId);if(!root||!data)return;
 var entries=[];try{entries=JSON.parse(data.textContent||'[]')}catch(error){__ps_record_runtime(rootId,error);return}
 var active=null,objectUrl='',listener=null,loadTimer=0,idleTimer=0,hardTimer=0;
 function clearTimers(){[loadTimer,idleTimer,hardTimer].forEach(function(timer){if(timer)clearTimeout(timer)});loadTimer=idleTimer=hardTimer=0}
 function setStatus(volume,text,kind){var node=root.querySelector('[data-catalog-status="'+volume+'"]');if(node){node.textContent=text;node.setAttribute('data-kind',kind||'')}}
 function cleanup(){clearTimers();if(listener){removeEventListener('message',listener);listener=null}if(active){active.remove();active=null}if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl=''}document.documentElement.classList.remove('ps-catalog-open')}
 async function decode(payload){
  var raw=atob(payload),bytes=new Uint8Array(raw.length);for(var i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
  if(typeof DecompressionStream==='function'){
   try{var stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));return await new Response(stream).text()}catch(error){}
  }
  if(window.pako&&typeof window.pako.ungzip==='function'){
   var out=window.pako.ungzip(bytes);return new TextDecoder('utf-8',{fatal:true}).decode(out)
  }
  throw new Error('当前浏览器无法离线解压全库能力卷');
 }
 async function openVolume(volume){
  cleanup();var entry=entries.find(function(item){return +item.volume===+volume});if(!entry)return;
  setStatus(volume,'正在解压…','loading');
  try{
   var html=await decode(entry.gzip_b64);setStatus(volume,'正在装载…','loading');
   objectUrl=URL.createObjectURL(new Blob([html],{type:'text/html'}));
   var overlay=document.createElement('div');overlay.className='ps-catalog-overlay';
   overlay.innerHTML='<div class="ps-catalog-shell"><div class="ps-catalog-head"><div><strong>全库能力卷 '+entry.volume+'</strong><span data-overlay-status>正在装载…</span></div><div><button type="button" data-catalog-retry>重试</button><button type="button" data-catalog-close>关闭</button></div></div><iframe title="全库能力卷 '+entry.volume+'" referrerpolicy="no-referrer"></iframe></div>';
   active=overlay;document.body.appendChild(overlay);document.documentElement.classList.add('ps-catalog-open');
   var frame=overlay.querySelector('iframe'),overlayStatus=overlay.querySelector('[data-overlay-status]');
   overlay.querySelector('[data-catalog-close]').addEventListener('click',cleanup);
   overlay.querySelector('[data-catalog-retry]').addEventListener('click',function(){openVolume(volume)});
   function fail(text){overlayStatus.textContent=text;setStatus(volume,text,'fail');clearTimers()}
   function armIdle(){if(idleTimer)clearTimeout(idleTimer);idleTimer=setTimeout(function(){fail('运行超过 60 秒没有进展，可关闭或重试')},60000)}
   listener=function(event){
    if(!frame||event.source!==frame.contentWindow||!event.data||event.data.token!==entry.token)return;
    var message=event.data;
    if(message.type==='pagespec-catalog-close'){cleanup();return}
    if(message.type==='pagespec-catalog-boot'){if(loadTimer){clearTimeout(loadTimer);loadTimer=0}overlayStatus.textContent='页面已启动，正在执行能力项…';setStatus(volume,'已启动','loading');armIdle();return}
    if(message.type==='pagespec-catalog-progress'){
     var done=+message.done||0,total=+message.total||entry.count,passed=+message.passed||0,failed=+message.failed||0;
     overlayStatus.textContent='运行中 · '+done+'/'+total+' · 通过 '+passed+(failed?' · 失败 '+failed:'');setStatus(volume,'运行中 '+done+'/'+total,'loading');armIdle();return;
    }
    if(message.type==='pagespec-catalog-ready'){
     var ok=message.final===true;overlayStatus.textContent=ok?('已就绪 · '+message.passed+'/'+message.total):('已完成但门禁未通过 · '+message.passed+'/'+message.total);setStatus(volume,ok?'已就绪':'门禁未通过',ok?'ready':'fail');clearTimers();return;
    }
    if(message.type==='pagespec-catalog-fail'){fail(String(message.message||'初始化失败'));return}
   };addEventListener('message',listener);
   loadTimer=setTimeout(function(){fail('页面 60 秒内未启动，可关闭或重试')},60000);
   hardTimer=setTimeout(function(){fail('本卷运行超过 300 秒，可关闭或重试')},300000);
   frame.src=objectUrl;
  }catch(error){cleanup();setStatus(volume,'无法打开','fail');__ps_record_runtime(rootId,error)}
 }
 root.querySelectorAll('[data-catalog-volume]').forEach(function(button){button.addEventListener('click',function(){openVolume(button.getAttribute('data-catalog-volume'))})});
};
"""'''


CATALOG_METHOD = r'''    def _build_catalog_volume(self, volume: int) -> dict[str, Any]:
        """Compile one trusted catalog volume with CSP-safe scripts and progress."""
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
            "nonce": child_nonce, "pre_warnings": [], "include_all_libraries": False,
        }
        html, _report, meta = pagespec.render_document(
            json.dumps(child_spec, ensure_ascii=False, separators=(",", ":")), child_ctx
        )
        if meta.get("fatal"):
            raise ValueError(f"catalog volume {volume} failed: {meta['fatal']}")

        nonce_attr = html_escape(child_nonce, quote=True)
        bootstrap = f'''<script nonce="{nonce_attr}">(function(){{
var token={pagespec.js_json(token)},volume={volume},last='';
function send(type,extra){{parent.postMessage(Object.assign({{type:type,token:token,volume:volume}},extra||{{}}),'*')}}
window.__PAGESPEC_CATALOG_SEND__=send;send('pagespec-catalog-boot',{{stage:'document'}});
window.__PAGESPEC_CATALOG_PROGRESS_TIMER__=setInterval(function(){{
 var text=function(id){{var node=document.getElementById(id);return node?String(node.textContent||'').trim():''}},done=+text('m-done')||0,passed=+text('m-pass')||0,failed=+text('m-fail')||0,total=+text('m-total')||0,signature=[done,passed,failed,total,document.title].join('|');
 if(signature!==last){{last=signature;send('pagespec-catalog-progress',{{done:done,passed:passed,failed:failed,total:total,title:document.title}})}}
}},1000);
addEventListener('error',function(event){{send('pagespec-catalog-fail',{{message:String(event.message||'运行错误').slice(0,180)}})}});
addEventListener('unhandledrejection',function(event){{var reason=event.reason;send('pagespec-catalog-fail',{{message:String(reason&&reason.message||reason||'未处理异常').slice(0,180)}})}});
}})();</script>'''
        final_bridge = f'''<style>#ps-catalog-return{{position:fixed;right:14px;top:12px;z-index:2147483640;border:1px solid #475063;border-radius:8px;background:#1a1f2a;color:#e9ecf3;padding:8px 12px;cursor:pointer}}</style>
<button type="button" id="ps-catalog-return">返回主报告</button>
<script nonce="{nonce_attr}">(function(){{var send=window.__PAGESPEC_CATALOG_SEND__,started=Date.now(),sent=false;
document.getElementById('ps-catalog-return').addEventListener('click',function(){{send('pagespec-catalog-close')}});
var timer=setInterval(function(){{try{{if(window.__ALL_TESTS_DONE__===true){{clearInterval(timer);clearInterval(window.__PAGESPEC_CATALOG_PROGRESS_TIMER__);var suite=window.__MEANINGFUL_SUITE__||{{}};if(!sent){{sent=true;send('pagespec-catalog-ready',{{passed:+suite.passed||0,total:+suite.total||0,failed:+suite.failed||0,final:suite.final_gate_pass===true}})}}}}else if(Date.now()-started>300000){{clearInterval(timer);send('pagespec-catalog-fail',{{message:'卷内运行超过 300 秒'}})}}}}catch(error){{clearInterval(timer);send('pagespec-catalog-fail',{{message:String(error&&error.message||error).slice(0,180)}})}}}},250);
}})();</script>'''
        html = html.replace("<body>", "<body>\n" + bootstrap, 1)
        html = html.replace("</body>", final_bridge + "\n</body>", 1)
        html = html.replace("<head>", "<head>\n" + _csp_meta(child_nonce), 1)
        # Frozen fixture bodies contain executable script tags that predate CSP.
        # Add the same unique nonce to every opening script tag, retaining any
        # existing nonce and leaving script text byte-for-byte unchanged.
        html = re.sub(
            r'<script\\b(?![^>]*\\bnonce\\s*=)([^>]*)>',
            lambda match: f'<script nonce="{nonce_attr}"' + match.group(1) + '>',
            html,
            flags=re.I,
        )

        lower_html = html.lstrip().lower()
        if not lower_html.startswith("<!doctype html>") or not html.rstrip().lower().endswith("</html>"):
            raise ValueError("catalog child document is incomplete")
        if html.count("Content-Security-Policy") != 1:
            raise ValueError("catalog child CSP identity mismatch")
        script_tags = re.findall(r'<script\\b[^>]*>', html, flags=re.I)
        missing_nonce = [tag[:200] for tag in script_tags if not re.search(r'\\bnonce\\s*=\\s*["\\\']', tag, flags=re.I)]
        if missing_nonce:
            raise ValueError(f"catalog child has {len(missing_nonce)} unnonced script tags")
        for marker in ("window.__MEANINGFUL_SUITE__", "window.__ALL_TESTS_DONE__", "pagespec-catalog-boot", "pagespec-catalog-progress", "pagespec-catalog-ready"):
            if marker not in html:
                raise ValueError(f"catalog child protocol is missing {marker}")
        child_bytes = len(html.encode("utf-8"))
        if child_bytes >= resources.OUTPUT_REJECT_BYTES:
            raise ValueError(f"catalog child {child_bytes} bytes exceeds HTML limit")
        registry = pagespec._catalog_registry()
        declared = next(item for item in registry["volumes"] if item["volume"] == volume)
        if meta.get("catalog_covers") != declared.get("covers"):
            raise ValueError("catalog child frozen coverage mismatch")
        payload = gzip.compress(html.encode("utf-8"), compresslevel=9, mtime=0)
        return {"volume": volume, "count": int(declared["count"]), "token": token,
                "gzip_b64": base64.b64encode(payload).decode("ascii")}

'''


REGRESSION_TEST = r'''# -*- coding: utf-8 -*-
from __future__ import annotations
import base64,gzip,json,re,sys,types,unittest,urllib.parse,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];TOOLS=ROOT/'tools';sys.path.insert(0,str(TOOLS))
class StubTool:
    def __init__(self,*args,**kwargs): pass
    def create_text_message(self,text): return {'kind':'text','text':text}
    def create_blob_message(self,blob,meta): return {'kind':'blob','blob':blob,'meta':meta}
dify=types.ModuleType('dify_plugin');dify.Tool=StubTool;sys.modules.setdefault('dify_plugin',dify)
entities=types.ModuleType('dify_plugin.entities');toolmod=types.ModuleType('dify_plugin.entities.tool');toolmod.ToolInvokeMessage=dict
sys.modules.setdefault('dify_plugin.entities',entities);sys.modules.setdefault('dify_plugin.entities.tool',toolmod)
import pagespec_transport,render_page

class PageSpec041RegressionTests(unittest.TestCase):
    def setUp(self):
        self.value={'version':1,'blocks':[{'type':'text','text':'编码容错'}]}
        self.text=json.dumps(self.value,ensure_ascii=False,separators=(',',':'))
    def assert_recovers(self,raw):
        outcome=pagespec_transport.parse_spec(raw)
        self.assertIsNone(outcome.error,outcome.error);self.assertEqual(self.value,outcome.value)
    def test_encoded_transport_matrix(self):
        cases=[
            urllib.parse.quote(self.text,safe=''),
            base64.b64encode(self.text.encode()).decode(),
            base64.urlsafe_b64encode(self.text.encode()).decode().rstrip('='),
            base64.b64encode(gzip.compress(self.text.encode(),mtime=0)).decode(),
            base64.b64encode(zlib.compress(self.text.encode())).decode(),
            'data:application/json;base64,'+base64.b64encode(self.text.encode()).decode(),
            base64.b64encode(base64.b64encode(self.text.encode())).decode(),
        ]
        for raw in cases:
            with self.subTest(prefix=raw[:24]): self.assert_recovers(raw)
    def test_plain_base64_like_text_is_not_reinterpreted(self):
        raw='SGVsbG8gV29ybGQ='
        outcome=pagespec_transport.parse_spec(raw)
        self.assertIsNone(outcome.error);self.assertEqual(raw,outcome.value['blocks'][0]['text'])
    def test_catalog_child_scripts_are_all_nonced(self):
        payload=render_page.RenderPageTool()._build_catalog_volume(1)
        html=gzip.decompress(base64.b64decode(payload['gzip_b64'])).decode('utf-8')
        tags=re.findall(r'<script\\b[^>]*>',html,flags=re.I)
        self.assertGreater(len(tags),10)
        self.assertTrue(all(re.search(r'\\bnonce\\s*=',tag,re.I) for tag in tags))
        self.assertIn('pagespec-catalog-boot',html);self.assertIn('pagespec-catalog-progress',html)
    def test_parent_has_native_and_pako_decompression_paths(self):
        source=(TOOLS/'pagespec.py').read_text(encoding='utf-8')
        self.assertIn("DecompressionStream",source);self.assertIn("pako.ungzip",source)
        self.assertIn("运行超过 60 秒没有进展",source);self.assertIn("运行超过 300 秒",source)

if __name__=='__main__': unittest.main()
'''


def patch_transport(root: Path) -> None:
    path=root/'tools/pagespec_transport.py';text=path.read_text(encoding='utf-8')
    text=replace_once(text,'import ast\nimport html\nimport json\nimport math\nimport re\n',
                      'import ast\nimport base64\nimport html\nimport json\nimport math\nimport re\nimport urllib.parse\nimport zlib\n','transport imports')
    text=replace_once(text,'\ndef _text_candidates(text: str, *, nested: bool = False) -> list[_Candidate]:\n',
                      ENCODED_HELPERS+'\n\ndef _text_candidates(text: str, *, nested: bool = False) -> list[_Candidate]:\n','encoded helpers')
    marker='''        seeds.append((entity, f"html-entity-{layer}", 930 - (layer - 1) * 16,
                      list(entity_events)))

    transforms = ('''
    replacement='''        seeds.append((entity, f"html-entity-{layer}", 930 - (layer - 1) * 16,
                      list(entity_events)))

    for decoded, source, encoded_events, score in _encoded_text_seeds(text):
        seeds.append((decoded, source, score, encoded_events))

    transforms = ('''
    text=replace_once(text,marker,replacement,'encoded seed insertion')
    path.write_text(text,encoding='utf-8')


def patch_catalog(root: Path) -> None:
    path=root/'tools/pagespec.py';text=path.read_text(encoding='utf-8')
    text=replace_regex_once(text,r'CATALOG_SHOWCASE_JS = r""".*?\n"""',CATALOG_SHOWCASE_JS,'parent catalog runtime')
    text=replace_once(text,'    registry = _catalog_registry()\n    payloads = [builder(volume) for volume in (1, 2, 3, 4)]\n',
                      '    registry = _catalog_registry()\n    ctx["need"].add("pako")\n    payloads = [builder(volume) for volume in (1, 2, 3, 4)]\n','pako parent dependency')
    path.write_text(text,encoding='utf-8')

    tool=root/'tools/render_page.py';source=tool.read_text(encoding='utf-8')
    source=replace_regex_once(source,r'    def _build_catalog_volume\(self, volume: int\) -> dict\[str, Any\]:.*?\n    # ---- main',CATALOG_METHOD+'    # ---- main','catalog child builder')
    tool.write_text(source,encoding='utf-8')


def patch_identity(root: Path) -> None:
    path=root/'manifest.yaml';data=yaml.safe_load(path.read_text(encoding='utf-8'))
    if str(data.get('version'))!='0.4.0': raise RuntimeError(f"unexpected baseline version {data.get('version')!r}")
    data['version']='0.4.1';data['created_at']='2026-07-21T02:10:00+08:00'
    data['description']['en_US']='Compile closed PageSpec JSON into offline HTML with bounded encoded transport recovery and CSP-safe, progress-aware 172-library capability volumes.'
    data['description']['zh_Hans']='把封闭 PageSpec JSON 编译为断网 HTML；恢复有界编码传输，并以 CSP 正确、可显示进度的隔离卷展示 172 库能力。'
    data['description']['zh_Hant']='把封閉 PageSpec JSON 編譯為離線 HTML；恢復有界編碼傳輸，並以 CSP 正確、可顯示進度的隔離卷展示 172 庫能力。'
    path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False,width=1000),encoding='utf-8')


def main() -> None:
    if len(sys.argv)!=2: raise SystemExit('usage: pagespec_041_patch.py PLUGIN_ROOT')
    root=Path(sys.argv[1]).resolve()
    patch_transport(root);patch_catalog(root);patch_identity(root)
    (root/'tests/test_pagespec_041_regressions.py').write_text(REGRESSION_TEST,encoding='utf-8')
    if (root/'requirements.txt').read_text(encoding='utf-8').strip()!='dify_plugin>=0.9.0':
        raise RuntimeError('requirements.txt identity changed')
    print('PageSpec 0.4.1 patch applied')

if __name__=='__main__': main()
