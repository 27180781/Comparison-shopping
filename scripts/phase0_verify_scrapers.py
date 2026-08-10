#!/usr/bin/env python3
"""Phase 0.1 — verify the configured scrapers against il-supermarket-scraper.

Roadmap acceptance criterion: "13 scraper names verified against the library"
(docs/05-ROADMAP.md, Phase 0). This script is the check.

It reads ENABLED_SCRAPERS / ENABLED_FILE_TYPES from the environment (falling
back to .env, then .env.example), resolves every name against the installed
library, and reports the portal family, base URL and gov chain ids that each
name actually resolves to.

Why this has to be re-runnable, not a one-off: the library drops a scraper the
moment a chain changes portal — VICTORY and MAHSANI_ASHUK were both removed in
favour of *_NEW_SOURCE variants. A stale name in .env is not a crash at import
time, it is a chain that silently stops being ingested. See ADR-006.

Exit code 0 when every configured name resolves, 1 otherwise — safe to wire
into CI as a guard against upstream renames.

Usage:
    pip install il-supermarket-scraper
    python scripts/phase0_verify_scrapers.py
    python scripts/phase0_verify_scrapers.py --json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from il_supermarket_scarper.scraper_stability import ScraperStability
    from il_supermarket_scarper.scrappers_factory import ScraperFactory
    from il_supermarket_scarper.utils import DiskFileOutput, FileTypesFilters
except ImportError as exc:  # pragma: no cover - environment guard
    # Never swallow the real error: the library pulls in playwright, pymongo and
    # lxml, and a failure in any of them is not "the library is missing".
    import traceback

    traceback.print_exc()
    print(file=sys.stderr)
    missing = (getattr(exc, "name", "") or "").split(".")[0]
    if missing == "il_supermarket_scarper":
        sys.exit("il-supermarket-scraper is not installed. Run: pip install -r requirements.txt")
    sys.exit(
        f"Could not import the scraper library: {exc}\n"
        f"The failing module is {missing or 'unknown'}, not the library itself. "
        "The traceback above is the real error."
    )


def quiet_library_logging() -> None:
    """Keep the library's chatter off stdout so --json stays parseable.

    The library builds its logger at import time with a StreamHandler bound to
    the real sys.stdout, plus a FileHandler that drops a logging.log into the
    working directory. Point the stream at stderr and detach the file handler.
    """
    library_logger = logging.getLogger("Logger")
    for handler in list(library_logger.handlers):
        if isinstance(handler, logging.FileHandler):
            library_logger.removeHandler(handler)
            handler.close()
        elif isinstance(handler, logging.StreamHandler):
            handler.setStream(sys.stderr)


quiet_library_logging()


# The library builds its base URLs inside __init__, so a scraper has to be
# instantiated to learn where it points. engine.py resolves its own default
# DiskFileOutput incorrectly (DumpFolderNames[chain] with an enum member rather
# than a name), which raises KeyError, so pass an explicit sink. Nothing is
# written — we only read attributes off the instance.
PROBE_OUTPUT_PATH = os.environ.get("PHASE0_PROBE_PATH", "/tmp/phase0-probe")


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a dict. Ignores comments and blank lines."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_config() -> tuple[dict[str, str], str]:
    """Resolve config from the environment, then .env, then .env.example."""
    for candidate in (REPO_ROOT / ".env", REPO_ROOT / ".env.example"):
        file_values = read_env_file(candidate)
        if file_values:
            source = str(candidate.relative_to(REPO_ROOT))
            break
    else:
        file_values, source = {}, "environment only"

    merged = {**file_values}
    overridden = []
    for key in ("ENABLED_SCRAPERS", "ENABLED_FILE_TYPES"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
            overridden.append(key)
    if overridden:
        source = f"{source} (env override: {', '.join(overridden)})"
    return merged, source


def split_list(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def suggest_replacement(name: str, known: set[str]) -> str | None:
    """Best-effort suggestion for a name the library no longer exposes.

    The library's rename convention when a chain moves portal is to append
    _NEW_SOURCE and comment out the original, so try that first.
    """
    candidate = f"{name}_NEW_SOURCE"
    if candidate in known:
        return candidate
    prefixed = sorted(k for k in known if k.startswith(f"{name}_"))
    if prefixed:
        return prefixed[0]
    return None


def portal_family(instance, scraper_cls) -> str:
    """Map the engine base class onto the portal families in 02-DATA-SOURCES.md."""
    bases = {klass.__name__ for klass in scraper_cls.__mro__}
    if "Cerberus" in bases:
        return "cerberus"
    if "Bina" in bases:
        return "bina"
    if "_LaibcatalogApiScraper" in bases:
        return "laibcatalog_api"
    if "PublishPrice" in bases:
        return "publishprice"
    if "Matrix" in bases:
        return "matrix"
    if "MultiPageWeb" in bases:
        return "shufersal"
    if "WebBase" in bases:
        return "web"
    return "unknown"


def describe(name: str, probe_output) -> dict:
    """Resolve one scraper name into the facts Phase 0 needs to record."""
    scraper_cls = getattr(ScraperFactory, name).value
    instance = scraper_cls(file_output=probe_output)

    chain_id = instance.chain_id
    chain_ids = chain_id if isinstance(chain_id, list) else [chain_id]

    stability = None
    if name in ScraperStability.__members__:
        stability_cls = ScraperStability[name].value
        stability = {
            "policy": stability_cls.__name__,
            "reason": (stability_cls.__doc__ or "").strip().splitlines()[0],
            "expires": stability_cls.pass_expiration_date().strftime("%Y-%m-%d"),
        }

    return {
        "name": name,
        "class": scraper_cls.__name__,
        "portal_family": portal_family(instance, scraper_cls),
        "url": getattr(instance, "url", None),
        "ftp_host": getattr(instance, "ftp_host", None),
        "ftp_username": getattr(instance, "ftp_username", None) or None,
        "chain_ids": chain_ids,
        "utilize_date_param": getattr(scraper_cls, "utilize_date_param", None),
        "stability": stability,
    }


def verify_file_types(configured: list[str]) -> tuple[list[str], list[str]]:
    """Split configured file types into (valid, unknown)."""
    known = set(FileTypesFilters.all_types())
    valid = [name for name in configured if name in known]
    unknown = [name for name in configured if name not in known]
    return valid, unknown


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()

    config, config_source = load_config()
    configured = split_list(config.get("ENABLED_SCRAPERS"))
    configured_types = split_list(config.get("ENABLED_FILE_TYPES"))

    if not configured:
        print("ENABLED_SCRAPERS is empty — nothing to verify.", file=sys.stderr)
        return 1

    known = set(ScraperFactory.all_listed_scrappers())
    resolved = [name for name in configured if name in known]
    missing = [name for name in configured if name not in known]

    probe_output = DiskFileOutput(storage_path=PROBE_OUTPUT_PATH)
    details = [describe(name, probe_output) for name in resolved]

    valid_types, unknown_types = verify_file_types(configured_types)
    # *Full snapshots and hourly deltas are separate file types. Requesting only
    # the deltas means never receiving a full price snapshot — see
    # docs/02-DATA-SOURCES.md §2 for the intended ingestion strategy.
    missing_full_types = [
        name
        for name in (FileTypesFilters.PRICE_FULL_FILE.name, FileTypesFilters.PROMO_FULL_FILE.name)
        if name not in configured_types
    ]

    report = {
        "library_version": library_version(),
        "config_source": config_source,
        "configured_count": len(configured),
        "resolved_count": len(resolved),
        "missing": [
            {"name": name, "suggested_replacement": suggest_replacement(name, known)}
            for name in missing
        ],
        "scrapers": details,
        "file_types": {
            "configured": configured_types,
            "valid": valid_types,
            "unknown": unknown_types,
            "missing_full_snapshot_types": missing_full_types,
        },
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    return 1 if missing or unknown_types else 0


def library_version() -> str:
    try:
        from importlib.metadata import version

        return version("il-supermarket-scraper")
    except Exception:  # pragma: no cover - metadata is best effort
        return "unknown"


def print_report(report: dict) -> None:
    print(f"il-supermarket-scraper version : {report['library_version']}")
    print(f"config source                  : {report['config_source']}")
    print(
        f"scrapers                       : {report['resolved_count']}/{report['configured_count']} resolved"
    )
    print()

    header = f"{'NAME':<26} {'PORTAL':<16} {'ENDPOINT':<46} {'GOV CHAIN IDS'}"
    print(header)
    print("-" * len(header))
    for item in report["scrapers"]:
        endpoint = item["url"] or f"ftp://{item['ftp_host']}"
        if item["ftp_username"]:
            endpoint = f"{endpoint} (user={item['ftp_username']})"
        print(
            f"{item['name']:<26} {item['portal_family']:<16} {endpoint:<46} "
            f"{','.join(item['chain_ids'])}"
        )

    flagged = [item for item in report["scrapers"] if item["stability"]]
    if flagged:
        print()
        print("Stability flags — the library expects these to return no files:")
        for item in flagged:
            stability = item["stability"]
            print(
                f"  {item['name']:<24} {stability['policy']} "
                f"(until {stability['expires']}) — {stability['reason']}"
            )

    if report["missing"]:
        print()
        print("MISSING — configured but not present in the library:")
        for item in report["missing"]:
            suggestion = item["suggested_replacement"] or "no obvious replacement"
            print(f"  {item['name']:<24} -> {suggestion}")

    file_types = report["file_types"]
    if file_types["unknown"]:
        print()
        print(f"UNKNOWN file types: {', '.join(file_types['unknown'])}")
        print(f"  valid values: {', '.join(FileTypesFilters.all_types())}")
    if file_types["missing_full_snapshot_types"]:
        print()
        print(
            "WARNING: no full-snapshot file type configured "
            f"({', '.join(file_types['missing_full_snapshot_types'])}). "
            "PRICE_FILE/PROMO_FILE match deltas only."
        )


if __name__ == "__main__":
    sys.exit(main())
