#!/usr/bin/env python3
"""Phase 0.2 / 0.3 — download a small sample of real files to look at.

Wraps ScarpingTask so the roadmap's snippets run identically on Windows,
macOS and Linux (CMD has no heredoc).

Deliberately small: three chains covering three different portal families —
MAAYAN_2000 (Bina), RAMI_LEVY (Cerberus/FTP), SHUFERSAL (own portal). If all
three work, network access is fine. Running all 13 means 5-15 GB and hours,
which is not what Phase 0 is for.

Files land in dumps/<chain>/, run status in dumps/status/. Both gitignored.

Usage:
    python scripts/phase0_download.py stores            # 0.2 — small and fast
    python scripts/phase0_download.py prices            # 0.3 — full snapshots
    python scripts/phase0_download.py prices --limit 1  # even smaller
    python scripts/phase0_download.py stores --scrapers SHUFERSAL

Requires an Israeli IP: several chains block access from abroad. Zero files
across all three chains means geo-blocking, not a bug in this script.
"""

from __future__ import annotations

import argparse
import sys

try:
    from il_supermarket_scarper import ScarpingTask
except ImportError as exc:  # pragma: no cover - environment guard
    # Show the real error; a failure inside playwright/pymongo/lxml is not
    # "the library is missing".
    import traceback

    traceback.print_exc()
    print(file=sys.stderr)
    missing = (getattr(exc, "name", "") or "").split(".")[0]
    if missing == "il_supermarket_scarper":
        sys.exit("il-supermarket-scraper is not installed. Run: pip install -r requirements.txt")
    sys.exit(f"Could not import the scraper library. Failing module: {missing or 'unknown'}")

# One chain per portal family — the point is to prove each access pattern works.
DEFAULT_SCRAPERS = ["MAAYAN_2000", "RAMI_LEVY", "SHUFERSAL"]

# 0.3 pulls the *Full snapshots rather than PRICE_FILE/PROMO_FILE. The delta
# files can legitimately come back empty when nothing changed in the last hour,
# and an empty file teaches you nothing about the schema. See PHASE0-FINDINGS F-2.
MODES = {
    "stores": ["STORE_FILE"],
    "prices": ["PRICE_FULL_FILE", "PROMO_FULL_FILE"],
    "deltas": ["PRICE_FILE", "PROMO_FILE"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=sorted(MODES), help="which file types to fetch")
    parser.add_argument(
        "--scrapers",
        default=",".join(DEFAULT_SCRAPERS),
        help=f"comma-separated scraper names (default: {','.join(DEFAULT_SCRAPERS)})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="max files per chain (default: 3). Ignored for stores, which is one file.",
    )
    args = parser.parse_args()

    scrapers = [name.strip() for name in args.scrapers.split(",") if name.strip()]
    files_types = MODES[args.mode]

    print(f"mode      : {args.mode} -> {', '.join(files_types)}")
    print(f"scrapers  : {', '.join(scrapers)}")
    print(f"limit     : {'n/a' if args.mode == 'stores' else args.limit}")
    print(f"output    : dumps/")
    print()

    task = ScarpingTask(enabled_scrapers=scrapers, files_types=files_types)
    if args.mode == "stores":
        task.start()
    else:
        task.start(limit=args.limit)

    print()
    print("Done. Inspect what landed with:  python scripts/phase0_peek.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
