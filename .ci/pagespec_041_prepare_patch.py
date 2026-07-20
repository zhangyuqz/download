#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare one executable PageSpec 0.4.1 patch from the checked-in template."""
from __future__ import annotations

import argparse
from pathlib import Path


def replace_exact(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{label}: expected {expected} matches, found {count}")
    return text.replace(old, new, expected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    text = args.source.read_text(encoding="utf-8")
    text = replace_exact(text, "bootstrap = f'''", 'bootstrap = f"""', 1, "bootstrap delimiter")
    text = replace_exact(text, "final_bridge = f'''", 'final_bridge = f"""', 1, "bridge delimiter")
    text = replace_exact(text, "}})();</script>'''", '}})();</script>"""', 2, "script delimiters")
    text = replace_exact(
        text,
        "re.subn(pattern, replacement, text, count=1, flags=re.S)",
        "re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.S)",
        1,
        "function replacement",
    )
    text = replace_exact(
        text,
        r"r'<script\\b(?![^>]*\\bnonce\\s*=)([^>]*)>'",
        r"r'<script\b(?![^>]*\bnonce\s*=)([^>]*)>'",
        1,
        "script pattern",
    )
    text = replace_exact(text, r"r'<script\\b[^>]*>'", r"r'<script\b[^>]*>'", 2, "script tag patterns")
    text = replace_exact(
        text,
        r"missing_nonce = [tag[:200] for tag in script_tags if not re.search(r'\\bnonce\\s*=\\s*[\"\\\']', tag, flags=re.I)]",
        'missing_nonce = [tag[:200] for tag in script_tags if not re.search(r"nonce\\s*=", tag, flags=re.I)]',
        1,
        "nonce list",
    )
    text = replace_exact(
        text,
        r"re.search(r'\\bnonce\\s*=',tag,re.I)",
        r"re.search(r'\bnonce\s*=',tag,re.I)",
        1,
        "regression nonce pattern",
    )
    compile(text, str(args.output), "exec")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"prepared_patch={args.output}")


if __name__ == "__main__":
    main()
