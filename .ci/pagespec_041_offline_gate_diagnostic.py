#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    findings = []
    with zipfile.ZipFile(args.package) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f'package CRC failure: {bad}')
        for info in archive.infolist():
            if info.is_dir() or info.file_size > 40_000_000:
                continue
            name = info.filename
            if not name.lower().endswith(('.py', '.json', '.js', '.html', '.txt')):
                continue
            raw = archive.read(info)
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                continue
            for match in re.finditer(r'offline_gate_pass', text):
                start = max(0, match.start() - 1200)
                end = min(len(text), match.end() + 1800)
                findings.append({
                    'file': name,
                    'offset': match.start(),
                    'excerpt': text[start:end],
                })
    args.output.write_text(json.dumps({'matches': findings}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'matches={len(findings)}')
    for item in findings[:20]:
        print('\nFILE', item['file'], 'OFFSET', item['offset'])
        print(item['excerpt'])
    if not findings:
        raise SystemExit('offline_gate_pass was not found in package')


if __name__ == '__main__':
    main()
