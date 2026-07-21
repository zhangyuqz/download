#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import types
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

COUNTS=[35,63,41,33]


def extract(package: Path,target: Path):
    with zipfile.ZipFile(package) as archive:
        bad=archive.testzip()
        if bad: raise RuntimeError(f'package CRC failure: {bad}')
        archive.extractall(target)


def generate(package_root: Path,output: Path):
    class StubTool:
        def __init__(self,*args,**kwargs): pass
        def create_text_message(self,text): return {'kind':'text','text':text}
        def create_blob_message(self,blob,meta): return {'kind':'blob','blob':blob,'meta':meta}
    dify=types.ModuleType('dify_plugin');dify.Tool=StubTool
    entities=types.ModuleType('dify_plugin.entities');entity_tool=types.ModuleType('dify_plugin.entities.tool');entity_tool.ToolInvokeMessage=dict
    sys.modules['dify_plugin']=dify;sys.modules['dify_plugin.entities']=entities;sys.modules['dify_plugin.entities.tool']=entity_tool
    sys.path.insert(0,str(package_root/'tools'))
    import render_page
    spec={'version':1,'doc':{'title':'PageSpec 0.4.1 浏览器门禁','theme':'dark','header':{'title':'PageSpec 0.4.1 浏览器门禁','subtitle':'正文、宽表和四卷 172 库共存。'}},'blocks':[{'type':'text','text':'普通报告正文必须保留。'},{'type':'table','columns':['A','B','C','D','E','F','G','H'],'rows':[[1,2,3,4,5,6,7,8],[9,10,11,12,13,14,15,16]],'features':['search','sort']}]}
    messages=list(render_page.RenderPageTool()._invoke({'spec':json.dumps(spec,ensure_ascii=False,separators=(',',':')),'filename':'pagespec_041_browser.html','include_all_libraries':True}))
    if [item.get('kind') for item in messages]!=['text','blob']: raise RuntimeError(messages)
    output.write_bytes(messages[1]['blob'])
    return {'bytes':len(messages[1]['blob']),'message':messages[0]['text']}


def overflow(frame):
    return frame.evaluate("Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth,document.body?document.body.scrollWidth-document.documentElement.clientWidth:0)")


def run_viewport(browser,html:Path,name:str,viewport:dict,force_pako:bool):
    context=browser.new_context(viewport=viewport)
    if force_pako:
        context.add_init_script("try{Object.defineProperty(globalThis,'DecompressionStream',{value:undefined,writable:false,configurable:true})}catch(e){globalThis.DecompressionStream=undefined}")
    page=context.new_page();network=[];console_errors=[];page_errors=[]
    page.on('request',lambda request: network.append(request.url) if re.match(r'^https?://',request.url,re.I) else None)
    page.on('console',lambda message: console_errors.append(message.text) if message.type=='error' else None)
    page.on('pageerror',lambda error: page_errors.append(str(error)))
    page.goto(html.as_uri(),wait_until='load',timeout=180000);page.wait_for_timeout(1000)
    result={'name':name,'force_pako':force_pako,'native_decompression':page.evaluate("typeof DecompressionStream==='function'"),'cards':page.locator('.ps-catalog-card').count(),'index':page.locator('.ps-catalog-index span').count(),'parent_overflow':overflow(page),'volumes':[]}
    if '普通报告正文必须保留' not in page.locator('body').inner_text(): raise AssertionError(f'{name}: ordinary body missing')
    if result['cards']!=4 or result['index']!=172 or result['parent_overflow']>1: raise AssertionError(f'{name}: parent structure {result}')
    for index,count in enumerate(COUNTS):
        card=page.locator('.ps-catalog-card').nth(index);status=card.locator('em');card.click()
        page.wait_for_selector('.ps-catalog-shell iframe',state='attached',timeout=180000)
        handle=status.element_handle();page.wait_for_function("el=>['ready','fail'].includes(el.getAttribute('data-kind'))",arg=handle,timeout=330000)
        kind=status.get_attribute('data-kind');status_text=status.inner_text()
        element=page.locator('.ps-catalog-shell iframe').element_handle();frame=element.content_frame() if element else None
        if frame is None: raise AssertionError(f'{name} volume {index+1}: frame missing')
        suite=frame.evaluate('window.__MEANINGFUL_SUITE__||null')
        volume={'volume':index+1,'kind':kind,'status':status_text,'suite':suite,'overflow':overflow(frame)};result['volumes'].append(volume)
        if kind!='ready': raise AssertionError(f'{name} volume {index+1}: {kind} {status_text}; suite={suite}')
        if not isinstance(suite,dict) or suite.get('total')!=count or suite.get('passed')!=count or suite.get('failed')!=0 or suite.get('final_gate_pass') is not True: raise AssertionError(f'{name} volume {index+1}: bad suite {suite}')
        if volume['overflow']>1: raise AssertionError(f'{name} volume {index+1}: overflow {volume["overflow"]}')
        page.wait_for_timeout(11000)
        late=frame.evaluate('window.__MEANINGFUL_SUITE__||null')
        if late.get('final_gate_pass') is not True: raise AssertionError(f'{name} volume {index+1}: late failure')
        frame.locator('#ps-catalog-return').click();page.wait_for_selector('.ps-catalog-shell iframe',state='detached',timeout=30000)
        if page.locator('.ps-catalog-overlay').count()!=0 or overflow(page)>1: raise AssertionError(f'{name} volume {index+1}: cleanup failure')
    result.update({'network':network,'console_errors':console_errors,'page_errors':page_errors})
    context.close()
    if network or console_errors or page_errors: raise AssertionError(f'{name}: network={network[:5]} console={console_errors[:5]} page={page_errors[:5]}')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True);parser.add_argument('--html',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    report={'passed':False,'error':None,'viewports':[]}
    try:
        with tempfile.TemporaryDirectory(prefix='pagespec041-browser-') as temporary:
            root=Path(temporary);extract(args.package.resolve(),root);report['generated']=generate(root,args.html.resolve())
        if report['generated']['bytes']>=30000000: raise AssertionError(report['generated'])
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True);report['browser_version']=browser.version
            report['viewports'].append(run_viewport(browser,args.html.resolve(),'desktop-native',{'width':1366,'height':900},False))
            report['viewports'].append(run_viewport(browser,args.html.resolve(),'mobile-pako',{'width':390,'height':844},True))
            browser.close()
        report['passed']=True
    except Exception as exc: report['error']=f'{type(exc).__name__}: {exc}'
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(args.output.read_text(encoding='utf-8'));raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__': main()
