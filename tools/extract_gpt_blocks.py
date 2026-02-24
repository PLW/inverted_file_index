#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


FENCE_PREFIX = "```"  # in your big file

# first non-empty line inside the block must be a comment holding the relative path
# supports: // path, # path, -- path, /* path */
PATH_LINE_RE = re.compile(r"^\s*(?:(?://)|#|--|/\*)\s*(?P<path>[^*\r\n]+?)(?:\s*\*/)?\s*$")


def is_fence_line(line: str) -> bool:
    return line.strip().startswith(FENCE_PREFIX)


def normalize_rel_path(p: str) -> str:
    p = p.strip().replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    return p


def is_safe_relative_path(rel: str) -> bool:
    if not rel:
        return False
    # forbid absolute paths
    if rel.startswith("/") or re.match(r"^[A-Za-z]:/", rel):
        return False
    # forbid traversal and empty segments
    parts = rel.split("/")
    if any(part in ("", "..") for part in parts):
        return False
    return True


@dataclass
class Block:
    lang: str
    rel_path: str
    content: str
    open_line: int   # 1-based
    close_line: int  # 1-based


def parse_blocks(text: str) -> List[Block]:
    lines = text.splitlines()
    i = 0
    blocks: List[Block] = []

    while i < len(lines):
        if not is_fence_line(lines[i]):
            i += 1
            continue

        # Opening fence line: ```lang
        open_line = i + 1
        opener = lines[i].strip()
        lang = opener[len(FENCE_PREFIX):].strip()  # may be empty
        i += 1

        # Collect block body until next fence line
        body: List[str] = []
        while i < len(lines) and not is_fence_line(lines[i]):
            body.append(lines[i])
            i += 1

        if i >= len(lines):
            raise ValueError(f"Unclosed fence opened at line {open_line} (no closing fence found).")

        close_line = i + 1  # line number of closing fence
        i += 1  # skip closing fence

        # Find first non-empty line in body => should contain the target path comment
        first_nonempty: Optional[int] = None
        for j, bl in enumerate(body):
            if bl.strip():
                first_nonempty = j
                break

        if first_nonempty is None:
            # empty block; ignore
            continue

        first_line = body[first_nonempty]
        m = PATH_LINE_RE.match(first_line)
        if not m:
            raise ValueError(
                f"Block opened at line {open_line} has no valid path comment on its first non-empty line.\n"
                f"Found: {first_line!r}"
            )

        rel_path = normalize_rel_path(m.group("path"))
        if not is_safe_relative_path(rel_path):
            raise ValueError(f"Unsafe/invalid path {rel_path!r} in block opened at line {open_line}.")

        # Content is everything after that path-comment line
        content_lines = body[first_nonempty + 1 :]
        content = "\n".join(content_lines).rstrip("\n") + "\n"

        blocks.append(Block(lang=lang, rel_path=rel_path, content=content,
                            open_line=open_line, close_line=close_line))

    return blocks


def write_blocks(
    blocks: List[Block],
    out_root: Path,
    overwrite: bool,
    dry_run: bool,
) -> Tuple[int, int]:
    out_root = out_root.resolve()
    written = 0
    skipped = 0

    for b in blocks:
        target = (out_root / b.rel_path).resolve()

        # refuse to write outside out_root
        try:
            target.relative_to(out_root)
        except ValueError:
            raise ValueError(f"Refusing to write outside --out-root: {b.rel_path} -> {target}")

        if target.exists() and not overwrite:
            skipped += 1
            continue

        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(b.content, encoding="utf-8")

        written += 1

    return written, skipped


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract fenced GPT code blocks to files; file path is given in the first comment line."
    )
    ap.add_argument("input", type=Path, help="Concatenated GPT output file")
    ap.add_argument("--out-root", type=Path, default=Path("."), help="Root directory to write under")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    ap.add_argument("--dry-run", action="store_true", help="Do not write files; just report")
    ap.add_argument("--list", action="store_true", help="List extracted paths and exit")
    args = ap.parse_args()

    text = args.input.read_text(encoding="utf-8")
    blocks = parse_blocks(text)

    if args.list:
        for b in blocks:
            print(f"{b.rel_path}  (lang={b.lang or 'n/a'})  [lines {b.open_line}-{b.close_line}]")
        return 0

    written, skipped = write_blocks(blocks, args.out_root, args.overwrite, args.dry_run)
    suffix = " (dry-run)" if args.dry_run else ""
    print(f"Blocks: {len(blocks)}  Written: {written}  Skipped: {skipped}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

