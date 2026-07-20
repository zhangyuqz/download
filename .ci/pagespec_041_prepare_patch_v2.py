#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path


def change(text: str, old: str, new: str, count: int, label: str) -> str:
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{label}: expected {count}, found {actual}")
    return text.replace(old, new, count)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    text = args.source.read_text(encoding="utf-8")
    text = change(text, "bootstrap = f'''", 'bootstrap = f"""', 1, "bootstrap")
    text = change(text, "final_bridge = f'''", 'final_bridge = f"""', 1, "bridge")
    text = change(text, "}})();</script>'''", '}})();</script>"""', 2, "script delimiters")
    text = change(
        text,
        "re.subn(pattern, replacement, text, count=1, flags=re.S)",
        "re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.S)",
        1,
        "function replacement",
    )
    compile(text, str(args.output), "exec")
    args.output.write_text(text, encoding="utf-8")
    print(f"prepared_patch={args.output}")


if __name__ == "__main__":
    main()
