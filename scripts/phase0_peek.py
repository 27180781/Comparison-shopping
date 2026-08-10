#!/usr/bin/env python3
"""Phase 0.4 — open a downloaded file and actually look at it.

Replaces `gunzip -c ... | iconv -f UTF-16` so this works on Windows too, and
handles the three traps from docs/02-DATA-SOURCES.md §5 in one place:
compression sniffed by magic bytes (gzip or zip, not by extension), encoding
detected from the BOM (several chains publish UTF-16), and file sizes that
must never be read whole.

This is a viewer, not a parser. It reads a bounded prefix and stops. The real
normalizer streams with lxml.iterparse — see the second law in CLAUDE.md.

Usage:
    python scripts/phase0_peek.py                     # first file found in dumps/
    python scripts/phase0_peek.py --list              # what got downloaded
    python scripts/phase0_peek.py --pattern PriceFull # first match
    python scripts/phase0_peek.py path/to/file.gz --lines 80
"""

from __future__ import annotations

import argparse
import gzip
import io
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DUMPS = REPO_ROOT / "dumps"

# Enough to see the root element and a couple of <Item> records without ever
# pulling a 15 MB file into memory.
READ_BYTES = 64 * 1024

BOMS = [
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
]


def find_files(pattern: str | None) -> list[Path]:
    if not DUMPS.is_dir():
        return []
    files = [p for p in sorted(DUMPS.rglob("*")) if p.is_file() and p.name != "status"]
    files = [p for p in files if "status" not in p.parts]
    if pattern:
        needle = pattern.lower()
        files = [p for p in files if needle in p.name.lower()]
    return files


def decompress(path: Path) -> tuple[bytes, str]:
    """Return a bounded prefix of the file's real content, sniffed by magic bytes."""
    with path.open("rb") as handle:
        magic = handle.read(4)

    if magic[:2] == b"\x1f\x8b":
        with gzip.open(path, "rb") as handle:
            return handle.read(READ_BYTES), "gzip"

    if magic[:2] == b"PK":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names:
                return b"", "zip (empty)"
            with archive.open(names[0]) as member:
                return member.read(READ_BYTES), f"zip -> {names[0]}"

    with path.open("rb") as handle:
        return handle.read(READ_BYTES), "none"


def detect_encoding(raw: bytes) -> tuple[str, str]:
    """Return (codec, how_it_was_detected). BOM first — never assume UTF-8."""
    for bom, codec in BOMS:
        if raw.startswith(bom):
            return codec, f"BOM {bom.hex(' ')}"

    # UTF-16 without a BOM still shows the tell: ASCII text interleaved with NULs.
    head = raw[:512]
    if head.count(b"\x00") > len(head) // 4:
        codec = "utf-16-le" if head[1:2] == b"\x00" else "utf-16-be"
        return codec, "no BOM, NUL-interleaved bytes"

    return "utf-8", "no BOM, assuming utf-8"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="file to inspect (default: first in dumps/)")
    parser.add_argument("--pattern", help="substring of the filename to match")
    parser.add_argument("--lines", type=int, default=40, help="lines to print (default: 40)")
    parser.add_argument("--list", action="store_true", help="list downloaded files and exit")
    args = parser.parse_args()

    if args.list:
        files = find_files(None)
        if not files:
            print(f"Nothing in {DUMPS}. Run: python scripts/phase0_download.py stores")
            return 1
        for path in files:
            print(f"{path.stat().st_size:>12,}  {path.relative_to(REPO_ROOT)}")
        print(f"\n{len(files)} file(s)")
        return 0

    if args.path:
        target = Path(args.path)
        if not target.is_file():
            print(f"No such file: {target}", file=sys.stderr)
            return 1
    else:
        candidates = find_files(args.pattern)
        if not candidates:
            where = f" matching {args.pattern!r}" if args.pattern else ""
            print(f"No files{where} in {DUMPS}.", file=sys.stderr)
            print("Run: python scripts/phase0_download.py stores", file=sys.stderr)
            return 1
        target = candidates[0]

    raw, compression = decompress(target)
    codec, how = detect_encoding(raw)

    print(f"file        : {target}")
    print(f"size on disk: {target.stat().st_size:,} bytes")
    print(f"compression : {compression}")
    print(f"encoding    : {codec}  ({how})")
    print("-" * 78)

    if not raw:
        print("(empty)")
        return 0

    # The prefix almost certainly cuts mid-character; never let that mask the content.
    text = raw.decode(codec, errors="replace")
    for line in io.StringIO(text).read().splitlines()[: args.lines]:
        print(line)

    print("-" * 78)
    print(f"(first {min(len(raw), READ_BYTES):,} bytes only — this is a viewer, not a parser)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
