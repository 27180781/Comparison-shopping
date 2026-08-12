#!/usr/bin/env python3
"""Diagnose the laibcatalog API, which covers Victory and Mahsani Ashuk.

Both chains scrape zero files with no error, and the library has three places
where that happens silently:

  1. `get_request_url` calls `getbranches` first and, if it comes back empty,
     `continue`s to the next chain id -- logging only at DEBUG. No branches
     means no file listing is ever requested.
  2. `get_api_data` catches every RequestException, logs it and returns `[]`,
     so an HTTP error looks identical to an empty catalogue.
  3. `apply_filter_by_type` keeps an entry only when its `fileType` matches a
     hardcoded vocabulary ("pricefull", "promofull", "store"...). A different
     spelling in the API drops every file while both calls look successful.

This walks the same calls in the same order and prints what each one returns,
which separates the three. Nothing here goes through the library, so a library
bug cannot hide itself from the diagnosis.

Usage:
    python scripts/phase0_check_laibcatalog.py
    python scripts/phase0_check_laibcatalog.py --chain-id 7290696200003
    python scripts/phase0_check_laibcatalog.py --download   # also try one file
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from collections import Counter

if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

try:
    import requests
except ImportError:  # pragma: no cover - environment guard
    sys.exit("requests is not installed. Run: pip install -r requirements.txt")

BASE = "https://laibcatalog.co.il"
TIMEOUT = 30

# Chain ids as the library registers them.
CHAINS = {
    "VICTORY_NEW_SOURCE": ["7290696200003", "7290058103393"],
    "MAHSANI_ASHUK_NEW_SOURCE": ["7290661400001", "7290633800006"],
}

# The vocabulary the library matches `fileType` against. Anything the API
# returns that is not in here is dropped, silently.
LIBRARY_FILE_TYPES = {
    "STORE_FILE": ["store", "stores", "storefull"],
    "PRICE_FILE": ["price"],
    "PROMO_FILE": ["promo"],
    "PRICE_FULL_FILE": ["pricefull"],
    "PROMO_FULL_FILE": ["promofull"],
}
KNOWN_TYPES = {value for values in LIBRARY_FILE_TYPES.values() for value in values}


def step(label: str) -> None:
    print(f"\n── {label} " + "─" * max(0, 62 - len(label)))


def call(session: requests.Session, endpoint: str, params: dict) -> tuple[object, str | None]:
    """Return (payload, error). Never raises -- the point is to report, not fail."""
    url = f"{BASE}{endpoint}"
    try:
        response = session.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__}: {exc}"

    if response.status_code != 200:
        body = response.text[:200].replace("\n", " ")
        return None, f"HTTP {response.status_code}: {body}"

    try:
        return response.json(), None
    except ValueError:
        return None, f"not JSON: {response.text[:200]!r}"


def check_chain(session: requests.Session, name: str, chain_id: str, try_download: bool) -> None:
    print(f"\n{'=' * 70}\n{name}  ·  edi={chain_id}\n{'=' * 70}")

    step("1. getbranches — an empty answer stops the scraper silently")
    branches, error = call(session, "/webapi/api/getbranches", {"edi": chain_id})
    if error:
        print(f"FAIL  {error}")
        print("      The library would log this and return [], which reads as")
        print("      'no branches' and skips the chain entirely.")
        return
    if not branches:
        print("EMPTY — this alone explains zero files.")
        print("      get_request_url does `continue` here, so getfiles is never called.")
        return
    count = len(branches) if isinstance(branches, list) else 1
    print(f"OK    {count} branch(es)")
    if isinstance(branches, list) and branches:
        print(f"      sample: {json.dumps(branches[0], ensure_ascii=False)[:160]}")

    step("2. getfiles — the actual catalogue")
    files, error = call(session, "/webapi/api/getfiles", {"edi": chain_id})
    if error:
        print(f"FAIL  {error}")
        return
    if not files:
        print("EMPTY — branches exist but the chain publishes no files under this edi.")
        return

    entries = files if isinstance(files, list) else [files]
    print(f"OK    {len(entries)} file(s)")
    print(f"      keys: {sorted(entries[0])if isinstance(entries[0], dict) else type(entries[0])}")

    step("3. fileType vocabulary — the silent filter")
    types = Counter(
        str(entry.get("fileType", "")).lower() for entry in entries if isinstance(entry, dict)
    )
    if not any(types):
        print("      no fileType field at all on these entries.")
    for value, occurrences in types.most_common():
        verdict = "matches the library" if value in KNOWN_TYPES else "→ DROPPED by the library"
        print(f"      {value or '(empty)':<24} {occurrences:>5}  {verdict}")

    unmatched = {value for value in types if value and value not in KNOWN_TYPES}
    if unmatched:
        print()
        print(f"⚠  {len(unmatched)} unrecognised type(s). The library keeps only:")
        print(f"      {sorted(KNOWN_TYPES)}")
        print("      Every other file is filtered out after a successful fetch,")
        print("      which is why the run reports zero without an error.")

    sample = next((e for e in entries if isinstance(e, dict) and e.get("fileName")), None)
    if sample:
        print(f"\n      sample entry: {json.dumps(sample, ensure_ascii=False)[:220]}")

    if try_download and sample:
        step("4. download URL — the library builds it from the FIRST chain id")
        # f"{url}/webapi/{primary_chain_id}/{file_name}" in the library, which
        # is wrong for a chain whose files live under its second edi.
        file_name = sample["fileName"]
        url = f"{BASE}/webapi/{chain_id}/{file_name}"
        print(f"      {url}")
        try:
            response = session.get(url, timeout=TIMEOUT, stream=True)
            head = next(response.iter_content(4), b"")
            kind = (
                "gzip" if head[:2] == b"\x1f\x8b"
                else "zip" if head[:2] == b"PK"
                else "xml/text" if head[:1] in (b"<", b"\xef") else f"unknown {head!r}"
            )
            print(f"      HTTP {response.status_code}, first bytes look like {kind}")
            response.close()
        except requests.RequestException as exc:
            print(f"FAIL  {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain-id", help="check a single edi instead of all four")
    parser.add_argument(
        "--download", action="store_true", help="also fetch one file to check the URL shape"
    )
    args = parser.parse_args()

    session = requests.Session()
    # The library uses a plain requests.Session with no headers; matching that
    # keeps the diagnosis honest about what it will experience.
    print(f"base: {BASE}")

    if args.chain_id:
        check_chain(session, "manual", args.chain_id, args.download)
    else:
        for name, chain_ids in CHAINS.items():
            for chain_id in chain_ids:
                check_chain(session, name, chain_id, args.download)

    print("\nHow to read this:")
    print("  step 1 empty      -> the chain moved or the edi is stale; fix chains.portal_url")
    print("  step 2 empty      -> branches without a catalogue; nothing to ingest")
    print("  step 3 unmatched  -> a library bug; the files exist and are being discarded")
    print("  step 4 non-200    -> the download path is built wrong for this chain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
