#!/usr/bin/env python3
"""Phase 0 — map the XML field names each chain actually publishes.

Roadmap acceptance criterion: "per-chain XML field mapping documented".

The published schemas differ from the documentation and from each other:
Maayan2000 sends ItemNm where the docs say ItemName, MinNoOfItemOfered where
they say MinQty, and nests ClubId under AdditionalRestrictions/Clubs instead
of carrying it flat. Guessing field names from one sample is how a normalizer
silently drops a chain, so derive the map from the files instead.

For every downloaded file this walks the tree with lxml.iterparse — never
loading a file whole, per the second law in CLAUDE.md — and reports each
element path with how often it occurs and a few real values.

Usage:
    python scripts/phase0_schema.py                     # every chain, every type
    python scripts/phase0_schema.py --type PriceFull
    python scripts/phase0_schema.py --chain Shufersal --type PromoFull
    python scripts/phase0_schema.py --json > schema.json
"""

from __future__ import annotations

import argparse
import codecs
import gzip
import io
import json
import signal
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

try:
    from lxml import etree
except ImportError:  # pragma: no cover - environment guard
    sys.exit("lxml is not installed. Run: pip install -r requirements.txt")

REPO_ROOT = Path(__file__).resolve().parent.parent
DUMPS = REPO_ROOT / "dumps"

# Enough records to see every optional field without reading 15MB of repetition.
DEFAULT_MAX_ELEMENTS = 40_000
SAMPLES_PER_PATH = 3

BOMS = [
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
]


def open_raw(path: Path):
    """Open the file, transparently decompressing gzip."""
    with path.open("rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return path.open("rb")


def detect_encoding(head: bytes) -> str | None:
    """Return a codec name when the bytes are not UTF-8, else None."""
    for bom, codec in BOMS:
        if head.startswith(bom):
            return None if codec == "utf-8-sig" else codec
    sample = head[:512]
    if sample.count(b"\x00") > len(sample) // 4:
        return "utf-16-le" if sample[1:2] == b"\x00" else "utf-16-be"
    return None


class Utf8Stream(io.RawIOBase):
    """Re-encode a UTF-16/32 byte stream to UTF-8 incrementally.

    lxml parses bytes and honours the XML declaration, but several chains
    publish UTF-16 with a declaration that disagrees with the actual bytes.
    Transcoding on the fly keeps iterparse streaming rather than materialising
    the whole document to fix the encoding.
    """

    def __init__(self, source, codec: str):
        self._source = source
        self._decoder = codecs.getincrementaldecoder(codec)()
        self._buffer = b""
        self._declared = False

    def readable(self) -> bool:
        return True

    def readinto(self, target) -> int:
        while not self._buffer:
            chunk = self._source.read(65536)
            text = self._decoder.decode(chunk, not chunk)
            if not chunk and not text:
                return 0
            if not self._declared:
                # The original declaration names the old encoding; drop it so
                # lxml trusts the UTF-8 bytes it is now being handed.
                stripped = text.lstrip("﻿")
                if stripped.startswith("<?xml"):
                    end = stripped.find("?>")
                    if end != -1:
                        stripped = stripped[end + 2 :]
                text = stripped
                self._declared = True
            self._buffer = text.encode("utf-8")

        size = min(len(target), len(self._buffer))
        target[:size] = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return size


def open_xml(path: Path):
    """Return a byte stream of UTF-8 XML, whatever the file was encoded as."""
    stream = open_raw(path)
    head = stream.read(1024)
    stream.close()

    codec = detect_encoding(head)
    stream = open_raw(path)
    if codec is None:
        return stream
    return io.BufferedReader(Utf8Stream(stream, codec))


def scan(path: Path, max_elements: int) -> dict:
    """Walk one file and return {element_path: {count, samples}}."""
    paths: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    attributes: dict[str, set[str]] = defaultdict(set)
    stack: list[str] = []
    seen = 0

    stream = open_xml(path)
    try:
        # recover=True: several chains ship stray control characters mid-file
        # and a hard failure there would lose the whole sample.
        context = etree.iterparse(stream, events=("start", "end"), recover=True)
        for event, element in context:
            if event == "start":
                stack.append(element.tag if isinstance(element.tag, str) else "?")
                continue

            here = "/".join(stack)
            stack.pop()
            paths[here] += 1
            for name in element.keys():
                attributes[here].add(name)

            text = (element.text or "").strip()
            if text and len(samples[here]) < SAMPLES_PER_PATH:
                samples[here].append(text[:60])

            # Free the subtree; without this iterparse still builds the document.
            element.clear()
            while element.getprevious() is not None:
                del element.getparent()[0]

            seen += 1
            if seen >= max_elements:
                break
    except etree.XMLSyntaxError as exc:
        return {"error": f"XML syntax error: {exc}"}
    finally:
        stream.close()

    return {
        "elements_scanned": seen,
        "paths": {
            key: {
                "count": count,
                "samples": samples.get(key, []),
                "attributes": sorted(attributes.get(key, ())),
            }
            for key, count in paths.most_common()
        },
    }


def classify(name: str) -> str:
    """Bucket a filename into a published file type."""
    lowered = name.lower()
    for prefix in ("pricefull", "promofull", "stores", "store", "price", "promo"):
        if lowered.startswith(prefix):
            return prefix
    return "other"


def pick_files(chain_filter: str | None, type_filter: str | None) -> list[Path]:
    """One representative file per (chain, type) — schemas repeat per store."""
    if not DUMPS.is_dir():
        return []

    chosen: dict[tuple[str, str], Path] = {}
    for path in sorted(DUMPS.rglob("*")):
        if not path.is_file() or "status" in path.parts:
            continue
        chain = path.relative_to(DUMPS).parts[0]
        kind = classify(path.name)
        if chain_filter and chain_filter.lower() not in chain.lower():
            continue
        if type_filter and type_filter.lower() not in kind:
            continue
        chosen.setdefault((chain, kind), path)
    return [chosen[key] for key in sorted(chosen)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain", help="restrict to one chain folder, e.g. Shufersal")
    parser.add_argument("--type", help="restrict to one file type, e.g. PromoFull")
    parser.add_argument("--max-elements", type=int, default=DEFAULT_MAX_ELEMENTS)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    files = pick_files(args.chain, args.type)
    if not files:
        print(f"No matching files under {DUMPS}.", file=sys.stderr)
        print("Run: python scripts/phase0_download.py prices", file=sys.stderr)
        return 1

    report = {}
    for path in files:
        chain = path.relative_to(DUMPS).parts[0]
        report[f"{chain}/{classify(path.name)}"] = {
            "file": str(path.relative_to(REPO_ROOT)),
            **scan(path, args.max_elements),
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    for key, data in report.items():
        print(f"\n{'=' * 78}\n{key}\n  {data['file']}")
        if "error" in data:
            print(f"  ERROR: {data['error']}")
            continue
        print(f"  elements scanned: {data['elements_scanned']:,}\n{'=' * 78}")
        labels = {
            key: key + (f" @{','.join(info['attributes'])}" if info["attributes"] else "")
            for key, info in data["paths"].items()
        }
        width = max((len(label) for label in labels.values()), default=4)
        print(f"{'PATH':<{width}} {'COUNT':>8}  SAMPLE")
        print("-" * (width + 26))
        for path_key, info in data["paths"].items():
            sample = info["samples"][0] if info["samples"] else ""
            print(f"{labels[path_key]:<{width}} {info['count']:>8}  {sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
