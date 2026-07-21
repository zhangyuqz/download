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

    definitions = []
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
            try:
                text = archive.read(info).decode('utf-8')
            except UnicodeDecodeError:
                continue
            for match in re.finditer(r'offlinePass\s*=', text):
                start = max(0, match.start() - 500)
                end = min(len(text), match.start() + 1000)
                definitions.append({
                    'file': name,
                    'offset': match.start(),
                    'excerpt': text[start:end],
                })
    args.output.write_text(json.dumps({'definitions': definitions}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'definitions={len(definitions)}')
    for item in definitions:
        print('\nFILE', item['file'], 'OFFSET', item['offset'])
        print(item['excerpt'])
    if not definitions:
        raise SystemExit('offlinePass definition was not found in package')


if __name__ == '__main__':
    main()
