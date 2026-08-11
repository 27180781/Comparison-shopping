#!/usr/bin/env python3
"""Phase 0.5 — the four measurements that decide the schema.

    #1  share of items carrying a valid public barcode   -> v1 catalog size
    #2  price variance between stores of one chain       -> base + exceptions (ADR-002)
    #3  share of promotions parseable from structured    -> promo engine scope
        fields rather than free text
    #4  how many chains each barcode appears in          -> CATALOG_MIN_CHAIN_COUNT

Decision thresholds come from docs/05-ROADMAP.md; this prints the measured
number next to the threshold rather than deciding anything on its own.

Field names differ per chain, so every read goes through a candidate list
(ItemNm on Bina chains, ItemName on Shufersal, and so on) derived from
scripts/phase0_schema.py rather than from the documentation, which is wrong on
several of them. See docs/PHASE0-FINDINGS.md.

The barcode logic below is a copy of docs/04-ALGORITHMS.md §1 so the
measurement runs standalone. Phase 2 moves it to catalog/ with unit tests --
this file is analysis, not production, per docs/05-ROADMAP.md.

Usage:
    python scripts/phase0_measure.py
    python scripts/phase0_measure.py --json > docs/phase0-measurements.json
"""

from __future__ import annotations

import argparse
import json
import signal
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase0_schema import DUMPS, REPO_ROOT, classify, open_xml  # noqa: E402

try:
    from lxml import etree
except ImportError:  # pragma: no cover - environment guard
    sys.exit("lxml is not installed. Run: pip install -r requirements.txt")


# ─── barcode normalisation (docs/04-ALGORITHMS.md §1) ────────────────────────

INTERNAL_PREFIXES = {"02"} | {str(n) for n in range(20, 30)}


def valid_check_digit(code: str) -> bool:
    """EAN-13 / EAN-8 check digit. Weights run 3,1,3,1... from the right."""
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * w for d, w in zip(reversed(body), [3, 1] * 7))
    return (10 - total % 10) % 10 == check


def normalize_barcode(raw: str | None) -> str | None:
    """Return a normalised EAN-13/8, or None when this is not a public barcode."""
    if not raw:
        return None
    code = "".join(ch for ch in raw if ch.isdigit())

    if len(code) == 12:
        code = "0" + code
    elif len(code) in (11, 13):
        code = code.zfill(13)
    elif len(code) not in (8, 13):
        return None

    if code[:2] in INTERNAL_PREFIXES:
        return None
    if not valid_check_digit(code):
        return None
    return code


# ─── per-chain field access ──────────────────────────────────────────────────
# Candidate tag names, most specific first. Reading through a candidate list
# rather than a per-chain table means an unseen chain degrades to "field not
# found" instead of being silently mis-parsed.

CANDIDATES = {
    "chain_id": ["ChainId", "ChainID"],
    "sub_chain_id": ["SubChainId", "SubChainID"],
    "store_id": ["StoreId", "StoreID"],
    "item_code": ["ItemCode"],
    "item_type": ["ItemType"],
    "item_name": ["ItemNm", "ItemName"],
    "item_price": ["ItemPrice"],
    "promo_id": ["PromotionId", "PromotionID"],
    "promo_description": ["PromotionDescription"],
    "reward_type": ["RewardType"],
    "discount_type": ["DiscountType"],
    "discount_rate": ["DiscountRate"],
    "discounted_price": ["DiscountedPrice", "DiscountedPricePerMida"],
    "min_qty": ["MinQty"],
    "club_id": ["ClubId", "ClubID"],
}


def field(element, key: str) -> str | None:
    """First non-empty value among the candidate tag names, searched depth-first."""
    for tag in CANDIDATES[key]:
        found = element.find(f".//{tag}")
        if found is not None and found.text and found.text.strip():
            return found.text.strip()
    return None


def header(root_element, key: str) -> str | None:
    """Read a file-level field off the root element."""
    for tag in CANDIDATES[key]:
        found = root_element.find(tag)
        if found is not None and found.text and found.text.strip():
            return found.text.strip()
    return None


def to_price(raw: str | None) -> Decimal | None:
    """Prices are Decimal. An unparseable or empty price is NULL, never 0."""
    if raw is None:
        return None
    try:
        value = Decimal(raw.strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    return value if value > 0 else None


def iter_records(path: Path, record_tag: str):
    """Stream records of one tag, clearing each subtree. Yields (root_meta, element)."""
    stream = open_xml(path)
    root_meta: dict[str, str | None] = {}
    try:
        context = etree.iterparse(stream, events=("start", "end"), recover=True)
        _, root = next(context)
        for event, element in context:
            if event == "end" and element.tag == record_tag:
                # Header fields normally precede <Items>, but re-read until the
                # store id turns up rather than caching a None for the whole
                # file if some chain emits them afterwards.
                if root_meta.get("store_id") is None:
                    root_meta = {
                        "chain_id": header(root, "chain_id"),
                        "sub_chain_id": header(root, "sub_chain_id"),
                        "store_id": header(root, "store_id"),
                    }
                yield root_meta, element
                element.clear()
                while element.getprevious() is not None:
                    del element.getparent()[0]
    except etree.XMLSyntaxError:
        return
    finally:
        stream.close()


def price_files() -> list[Path]:
    return [
        p
        for p in sorted(DUMPS.rglob("*"))
        if p.is_file() and "status" not in p.parts and classify(p.name) in {"pricefull", "price"}
    ]


def promo_files() -> list[Path]:
    return [
        p
        for p in sorted(DUMPS.rglob("*"))
        if p.is_file() and "status" not in p.parts and classify(p.name) in {"promofull", "promo"}
    ]


def measure_items() -> dict:
    """Measurements #1, #2 and #4 all come from one pass over the price files."""
    total = 0
    flagged_global = 0          # ItemType == 1
    valid_barcode = 0
    flagged_but_invalid = 0     # ItemType == 1 yet not a real barcode
    per_chain_items: Counter[str] = Counter()
    per_chain_valid: Counter[str] = Counter()

    barcode_chains: defaultdict[str, set[str]] = defaultdict(set)
    # (chain, sub_chain, barcode) -> {store_id: price}
    group_prices: defaultdict[tuple[str, str, str], dict[str, Decimal]] = defaultdict(dict)

    for path in price_files():
        chain = path.relative_to(DUMPS).parts[0]
        for meta, item in iter_records(path, "Item"):
            total += 1
            per_chain_items[chain] += 1

            raw_code = field(item, "item_code")
            item_type = field(item, "item_type")
            barcode = normalize_barcode(raw_code)

            if item_type == "1":
                flagged_global += 1
                if barcode is None:
                    flagged_but_invalid += 1

            if barcode is None:
                continue

            valid_barcode += 1
            per_chain_valid[chain] += 1
            barcode_chains[barcode].add(chain)

            price = to_price(field(item, "item_price"))
            store = meta.get("store_id") or "?"
            if price is not None:
                key = (chain, meta.get("sub_chain_id") or "-", barcode)
                group_prices[key][store] = price

    # #2 — within a price group, how often does a store deviate from the norm?
    comparable = 0
    deviating = 0
    deviation_sizes: list[float] = []
    for prices in group_prices.values():
        if len(prices) < 2:
            continue
        values = list(prices.values())
        modal = Counter(values).most_common(1)[0][0]
        for value in values:
            comparable += 1
            if value != modal:
                deviating += 1
                if modal:
                    deviation_sizes.append(abs(float(value - modal)) / float(modal) * 100)

    # #4 — how many chains does each barcode appear in?
    spread = Counter(len(chains) for chains in barcode_chains.values())

    return {
        "measurement_1": {
            "total_items": total,
            "valid_barcode": valid_barcode,
            "valid_barcode_pct": pct(valid_barcode, total),
            "item_type_1": flagged_global,
            "item_type_1_pct": pct(flagged_global, total),
            "item_type_1_but_invalid_barcode": flagged_but_invalid,
            "item_type_1_but_invalid_pct": pct(flagged_but_invalid, flagged_global),
            "per_chain": {
                chain: {
                    "items": count,
                    "valid_barcode": per_chain_valid[chain],
                    "valid_barcode_pct": pct(per_chain_valid[chain], count),
                }
                for chain, count in per_chain_items.items()
            },
        },
        "measurement_2": {
            "comparable_store_prices": comparable,
            "deviating": deviating,
            "deviating_pct": pct(deviating, comparable),
            "median_deviation_pct": round(statistics.median(deviation_sizes), 2)
            if deviation_sizes
            else None,
            "note": "Deviation is measured against the modal price within "
            "(chain, sub_chain, barcode).",
        },
        "measurement_4": {
            "distinct_barcodes": len(barcode_chains),
            "chains_in_sample": len(per_chain_items),
            "distribution": {str(k): v for k, v in sorted(spread.items())},
        },
    }


def measure_promotions() -> dict:
    """Measurement #3 — how much of a promotion survives without reading prose."""
    total = 0
    structured = 0
    partial = 0
    text_only = 0
    missing_fields: Counter[str] = Counter()
    per_chain: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for path in promo_files():
        chain = path.relative_to(DUMPS).parts[0]
        for _meta, promo in iter_records(path, "Promotion"):
            total += 1

            # A promotion is machine-applicable only with an amount to apply,
            # a quantity to apply it at, and items to apply it to.
            amount = to_price(field(promo, "discounted_price"))
            rate = field(promo, "discount_rate")
            has_amount = amount is not None or (rate not in (None, "0.00", "0"))
            has_qty = field(promo, "min_qty") is not None
            has_items = promo.find(".//ItemCode") is not None

            if not has_amount:
                missing_fields["discount amount"] += 1
            if not has_qty:
                missing_fields["min quantity"] += 1
            if not has_items:
                missing_fields["item list"] += 1

            if has_amount and has_qty and has_items:
                structured += 1
                per_chain[chain]["structured"] += 1
            elif has_items and (has_amount or has_qty):
                partial += 1
                per_chain[chain]["partial"] += 1
            else:
                text_only += 1
                per_chain[chain]["text_only"] += 1

    return {
        "measurement_3": {
            "total_promotions": total,
            "structured": structured,
            "structured_pct": pct(structured, total),
            "partial": partial,
            "text_only": text_only,
            "missing_fields": dict(missing_fields.most_common()),
            "per_chain": {chain: dict(counts) for chain, counts in per_chain.items()},
        }
    }


def pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def render(report: dict) -> None:
    one = report["measurement_1"]
    two = report["measurement_2"]
    three = report["measurement_3"]
    four = report["measurement_4"]

    print("=" * 78)
    print("PHASE 0.5 — THE FOUR MEASUREMENTS")
    print("=" * 78)

    print("\n#1  Items with a valid public barcode        threshold: < 50% -> reconsider")
    print("-" * 78)
    print(f"  items scanned            {one['total_items']:>10,}")
    print(f"  valid barcode            {one['valid_barcode']:>10,}   {one['valid_barcode_pct']}%")
    print(f"  ItemType = 1             {one['item_type_1']:>10,}   {one['item_type_1_pct']}%")
    print(
        f"  ItemType 1, bad barcode  {one['item_type_1_but_invalid_barcode']:>10,}   "
        f"{one['item_type_1_but_invalid_pct']}% of flagged"
    )
    if one["item_type_1_but_invalid_barcode"]:
        print("  ^ ItemType is not a usable filter on its own. Validate the barcode.")
    print()
    for chain, data in one["per_chain"].items():
        print(f"    {chain:<20} {data['items']:>8,} items   {data['valid_barcode_pct']}% valid")

    print("\n#2  Price deviation between stores           threshold: > 15% -> drop ADR-002")
    print("-" * 78)
    if not two["comparable_store_prices"]:
        print("  Not enough overlap: need the same barcode priced in 2+ stores of a chain.")
    else:
        print(f"  comparable store prices  {two['comparable_store_prices']:>10,}")
        print(f"  deviating from modal     {two['deviating']:>10,}   {two['deviating_pct']}%")
        print(f"  median deviation size    {two['median_deviation_pct']}%")
        verdict = "holds" if (two["deviating_pct"] or 0) <= 15 else "DOES NOT HOLD"
        print(f"  -> base + exceptions {verdict}")

    print("\n#3  Promotions parseable from structured fields")
    print("-" * 78)
    if not three["total_promotions"]:
        print("  No promotion files. Run: python scripts/phase0_download.py promos")
    else:
        print(f"  promotions scanned       {three['total_promotions']:>10,}")
        print(f"  fully structured         {three['structured']:>10,}   {three['structured_pct']}%")
        print(f"  partial                  {three['partial']:>10,}")
        print(f"  text only                {three['text_only']:>10,}")
        if three["missing_fields"]:
            print("\n  most often missing:")
            for name, count in three["missing_fields"].items():
                print(f"    {name:<22} {count:>8,}")
        print()
        for chain, counts in three["per_chain"].items():
            print(f"    {chain:<20} {counts}")

    print("\n#4  Chains per barcode                       sets CATALOG_MIN_CHAIN_COUNT")
    print("-" * 78)
    print(f"  distinct barcodes        {four['distinct_barcodes']:>10,}")
    print(f"  chains in this sample    {four['chains_in_sample']:>10}")
    print()
    for chains, count in four["distribution"].items():
        share = pct(count, four["distinct_barcodes"])
        bar = "█" * max(1, int((share or 0) / 2))
        print(f"    in {chains} chain(s)   {count:>8,}  {share:>6}%  {bar}")
    if four["chains_in_sample"] < 3:
        print("\n  Only a few chains downloaded — this distribution cannot set the")
        print("  threshold yet. Re-run after downloading more chains.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if not DUMPS.is_dir() or not price_files():
        print(f"No price files under {DUMPS}.", file=sys.stderr)
        print("Run: python scripts/phase0_download.py prices", file=sys.stderr)
        return 1

    report = {**measure_items(), **measure_promotions()}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        render(report)
        print(f"\nSource: {len(price_files())} price file(s), {len(promo_files())} promo file(s)")
        print(f"under {DUMPS.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
