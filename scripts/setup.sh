#!/usr/bin/env bash
# One-command setup on a fresh machine.
#
#   ./scripts/setup.sh
#
# Idempotent: safe to re-run. It checks rather than assumes, because every
# assumption in this project's setup has been wrong at least once --
# see docs/PHASE0-FINDINGS.md F-11 (Windows cannot run the scraper at all) and
# the Python 3.14 lxml wall.
#
# What it does NOT do: download price data or start Postgres. Those are
# separate on purpose - the first needs an Israeli IP and takes minutes, the
# second is only needed for the database tests.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 3.13 is the newest version with an lxml 5.x wheel, and the library pins
# lxml<6. On 3.14 pip falls back to compiling from C source.
PYTHON_VERSION="3.13"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1m── %s\033[0m\n' "$*"; }

# ─── 1. platform ─────────────────────────────────────────────────────────────

step "Checking the platform"

case "$(uname -s)" in
  Linux|Darwin) green "OK  $(uname -s)" ;;
  MINGW*|MSYS*|CYGWIN*)
    red "This is Git Bash on Windows, not Linux."
    red "il-supermarket-scraper imports fcntl, which does not exist on Windows."
    red "Run this inside WSL:  wsl  →  cd ~  →  git clone …  →  ./scripts/setup.sh"
    exit 1
    ;;
  *) yellow "Unrecognised platform $(uname -s); continuing" ;;
esac

if grep -qi microsoft /proc/version 2>/dev/null; then
  green "OK  running under WSL"
  case "$REPO_ROOT" in
    /mnt/*)
      yellow "WARNING: this clone lives under $REPO_ROOT, on the Windows filesystem."
      yellow "It will be slow, and a path containing non-ASCII characters causes trouble."
      yellow "Prefer:  cd ~ && git clone <url> && cd Comparison-shopping"
      ;;
  esac
fi

# ─── 2. python ───────────────────────────────────────────────────────────────

step "Finding Python $PYTHON_VERSION"

find_python() {
  for candidate in "python$PYTHON_VERSION" python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if (3,11) <= sys.version_info < (3,14) else 1)' 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"

if [[ -z "$PYTHON_BIN" ]]; then
  yellow "No suitable Python found (need 3.11-3.13; 3.14 has no lxml wheel)."
  step "Installing a standalone Python via uv"

  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi

  uv python install "$PYTHON_VERSION"
  uv venv --python "$PYTHON_VERSION" .venv
  USED_UV=1
else
  green "OK  $PYTHON_BIN ($("$PYTHON_BIN" --version))"
  if [[ ! -d .venv ]]; then
    "$PYTHON_BIN" -m venv .venv
  fi
  USED_UV=0
fi

# ─── 3. dependencies ─────────────────────────────────────────────────────────

step "Installing dependencies"

# requirements-dev pulls in requirements, so this covers both. The production
# image installs requirements.txt alone -- a test client does not belong there.
if [[ "${USED_UV:-0}" == "1" ]] && command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -r requirements-dev.txt
else
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install -r requirements-dev.txt
fi

# The one failure that is worth catching loudly, because the symptom otherwise
# appears much later as a compiler error.
if ! ./.venv/bin/python -c 'import lxml' 2>/dev/null; then
  red "lxml failed to import. Almost always the Python version -- 3.14 has no wheel."
  exit 1
fi
green "OK  $(./.venv/bin/python --version), lxml and the scraper import cleanly"

# ─── 4. config ───────────────────────────────────────────────────────────────

step "Configuration"

if [[ -f .env ]]; then
  green "OK  .env already exists (left untouched)"
else
  cp .env.example .env
  green "OK  created .env from .env.example"
  yellow "    Fill in DATABASE_URL and GOOGLE_MAPS_API_KEY before running the pipeline."
fi

# ─── 5. verify ───────────────────────────────────────────────────────────────

step "Verifying the scraper mapping"

if ./.venv/bin/python scripts/phase0_verify_scrapers.py >/dev/null 2>&1; then
  green "OK  all 13 scrapers resolve against the installed library"
else
  yellow "Some scraper names did not resolve. Run for details:"
  yellow "    ./.venv/bin/python scripts/phase0_verify_scrapers.py"
fi

step "Running the tests that need no database"

# Database tests skip without TEST_DATABASE_URL; a failure here is a real
# problem with the checkout, not a missing service.
if ./.venv/bin/python -m pytest tests/ -q 2>&1 | tail -1; then
  :
fi

# ─── done ────────────────────────────────────────────────────────────────────

cat <<'NEXT'

────────────────────────────────────────────────────────────────────
Ready. Activate the environment with:

    source .venv/bin/activate

Then, depending on what you are doing:

  diagnose the two chains that fetch nothing
    python scripts/phase0_check_laibcatalog.py
    python scripts/phase0_check_cerberus.py

  download sample data (needs an Israeli IP, a few minutes)
    python scripts/phase0_download.py stores
    python scripts/phase0_download.py prices
    python scripts/phase0_peek.py

  run the database tests
    docker compose up -d db
    export TEST_DATABASE_URL=postgresql+psycopg://price:price@localhost:5432/pricecompare
    pytest tests/

  run the whole thing
    docker compose up -d db redis
    alembic upgrade head
    python -m ingestion cycle
    uvicorn api.main:app --reload
────────────────────────────────────────────────────────────────────
NEXT
