"""Read published XML without trusting anything the file claims about itself.

Three traps, from docs/02-DATA-SOURCES.md §5 and confirmed in Phase 0:

  * Compression is gzip for most chains and zip for some, and the extension
    lies. Sniff magic bytes.
  * Encoding is UTF-8 for most and UTF-16 for some, and the XML declaration
    can disagree with the actual bytes. Trust the BOM, then the byte pattern.
  * Files run 5-15MB and there are hundreds per run, so nothing is ever read
    whole. Everything here streams (CLAUDE.md, second law).

The UTF-16 path transcodes incrementally rather than decoding the document to
fix its encoding, so a 15MB UTF-16 file costs one 64KB buffer.
"""

from __future__ import annotations

import codecs
import gzip
import io
import zipfile
from pathlib import Path
from typing import Iterator

from lxml import etree

CHUNK = 64 * 1024

BOMS = [
    (codecs.BOM_UTF8, None),  # utf-8 with a BOM is still utf-8 to lxml
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
]


def open_decompressed(path: Path):
    """Open a file, transparently decompressing by magic bytes rather than name."""
    with path.open("rb") as probe:
        magic = probe.read(4)

    if magic[:2] == b"\x1f\x8b":
        return gzip.open(path, "rb")

    if magic[:2] == b"PK":
        archive = zipfile.ZipFile(path)
        names = archive.namelist()
        if not names:
            archive.close()
            return io.BytesIO(b"")
        member = archive.open(names[0])
        # Keep the archive alive for as long as the member stream is read.
        member._archive = archive  # type: ignore[attr-defined]
        return member

    return path.open("rb")


def detect_encoding(head: bytes) -> str | None:
    """Return a codec when the bytes are not UTF-8, else None."""
    for bom, codec in BOMS:
        if head.startswith(bom):
            return codec
    sample = head[:512]
    if sample.count(b"\x00") > len(sample) // 4:
        return "utf-16-le" if sample[1:2] == b"\x00" else "utf-16-be"
    return None


class _Utf8Stream(io.RawIOBase):
    """Incrementally re-encode a UTF-16/32 byte stream as UTF-8."""

    def __init__(self, source, codec: str):
        self._source = source
        self._decoder = codecs.getincrementaldecoder(codec)()
        self._buffer = b""
        self._declaration_handled = False

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        try:
            self._source.close()
        finally:
            super().close()

    def readinto(self, target) -> int:
        while not self._buffer:
            chunk = self._source.read(CHUNK)
            text = self._decoder.decode(chunk, not chunk)
            if not chunk and not text:
                return 0
            if not self._declaration_handled:
                # The declaration names the original encoding. Strip it so lxml
                # believes the UTF-8 bytes it is now being handed.
                text = text.lstrip("﻿")
                if text.startswith("<?xml"):
                    end = text.find("?>")
                    if end != -1:
                        text = text[end + 2 :]
                self._declaration_handled = True
            self._buffer = text.encode("utf-8")

        size = min(len(target), len(self._buffer))
        target[:size] = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return size


def open_xml(path: Path):
    """Return a byte stream of UTF-8 XML regardless of the file's encoding."""
    probe = open_decompressed(path)
    try:
        head = probe.read(1024)
    finally:
        probe.close()

    codec = detect_encoding(head)
    stream = open_decompressed(path)
    if codec is None:
        return stream
    return io.BufferedReader(_Utf8Stream(stream, codec))


def iter_elements(path: Path, tag: str) -> Iterator[tuple[etree._Element, etree._Element]]:
    """Yield (root, element) for every `tag`, clearing each subtree as it goes.

    The root is yielded alongside so callers can read file-level fields such as
    ChainId and StoreId, which sit outside the repeating record.

    recover=True because several chains emit stray control characters; losing
    an entire store's prices to one bad byte is worse than skipping the byte.
    """
    stream = open_xml(path)
    try:
        context = etree.iterparse(stream, events=("end",), tag=tag, recover=True, huge_tree=True)
        root = None
        for _event, element in context:
            if root is None:
                root = element.getroottree().getroot()
            yield root, element
            element.clear()
            # Drop already-processed siblings so the document does not grow.
            parent = element.getparent()
            if parent is not None:
                while element.getprevious() is not None:
                    del parent[0]
    finally:
        stream.close()
