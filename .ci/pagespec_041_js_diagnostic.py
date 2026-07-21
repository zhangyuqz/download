#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import subprocess
import sys
import tempfile
import types
import zipfile
from pathlib import Path


def extract_package(package: Path, target: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        bad=archive.testzip()
        if bad: raise RuntimeError(f'CRC failure: {bad}')
        archive.extractall(target)


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    report={'scripts':[],'failed':[]}
    with tempfile.TemporaryDirectory(prefix='pagespec041-js-') as temporary:
        root=Path(temporary);extract_package(args.package.resolve(),root)
        class StubTool:
            def __init__(self,*args,**kwargs): pass
            def create_text_message(self,text): return {'kind':'text','text':text}
            def create_blob_message(self,blob,meta): return {'kind':'blob','blob':blob,'meta':meta}
        dify=types.ModuleType('dify_plugin');dify.Tool=StubTool
        entities=types.ModuleType('dify_plugin.entities');entity_tool=types.ModuleType('dify_plugin.entities.tool');entity_tool.ToolInvokeMessage=dict
        sys.modules['dify_plugin']=dify;sys.modules['dify_plugin.entities']=entities;sys.modules['dify_plugin.entities.tool']=entity_tool
        sys.path.insert(0,str(root/'tools'))
        import render_page
        payload=render_page.RenderPageTool()._build_catalog_volume(1,'diagnostic-shared-nonce')
        html=gzip.decompress(base64.b64decode(payload['gzip_b64'])).decode('utf-8')
        scripts=re.findall(r'<script\b[^>]*>(.*?)</script\s*>',html,flags=re.I|re.S)
        report['script_count']=len(scripts)
        for index,source in enumerate(scripts,1):
            path=Path(temporary)/f'script_{index:03d}.js';path.write_text(source,encoding='utf-8')
            completed=subprocess.run(['node','--check',str(path)],capture_output=True,text=True)
            item={'index':index,'bytes':len(source.encode('utf-8')),'returncode':completed.returncode}
            if completed.returncode:
                stderr=completed.stderr.strip();item['stderr']=stderr
                match=re.search(r':(\d+)\n',stderr)
                if match:
                    line=int(match.group(1));lines=source.splitlines();start=max(0,line-4);end=min(len(lines),line+3)
                    item['line']=line;item['context']=[{'line':n+1,'text':lines[n][:600]} for n in range(start,end)]
                report['failed'].append(item)
            report['scripts'].append(item)
        report['passed']=not report['failed'] and len(scripts)>0
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(args.output.read_text(encoding='utf-8'));raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__': main()
