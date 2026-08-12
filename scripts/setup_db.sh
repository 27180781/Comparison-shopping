#!/usr/bin/env bash
# Install and start a local Postgres, for when Docker is not available.
#
#   ./scripts/setup_db.sh
#
# Docker Desktop's WSL integration is off by default, and enabling it is a GUI
# step in someone else's application. Ubuntu ships Postgres 16, which is the
# version this project targets, so apt gets there in one command.
#
# Idempotent: re-running only starts the service and reports the URL.

set -euo pipefail

DB_NAME="${DB_NAME:-pricecompare}"
DB_TEST_NAME="${DB_TEST_NAME:-pricetest}"
DB_USER="${DB_USER:-price}"
DB_PASSWORD="${DB_PASSWORD:-price}"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1m── %s\033[0m\n' "$*"; }

step "Checking for Postgres"

if ! command -v psql >/dev/null 2>&1; then
  yellow "Not installed. Installing postgresql (needs sudo)."
  sudo apt-get update -qq
  sudo apt-get install -y -qq postgresql postgresql-contrib
fi
green "OK  $(psql --version)"

step "Starting the service"

# WSL images often run without systemd, so `service` is used rather than
# systemctl -- it works either way.
if ! pg_isready -q 2>/dev/null; then
  sudo service postgresql start
  for _ in $(seq 1 15); do
    pg_isready -q 2>/dev/null && break
    sleep 1
  done
fi

if ! pg_isready -q 2>/dev/null; then
  red "Postgres did not come up. Check: sudo service postgresql status"
  exit 1
fi
green "OK  accepting connections"

step "Creating the role and databases"

run_sql() { sudo -u postgres psql -tAc "$1"; }

if [[ -z "$(run_sql "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'")" ]]; then
  run_sql "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD' CREATEDB" >/dev/null
  green "OK  created role $DB_USER"
else
  green "OK  role $DB_USER already exists"
fi

for database in "$DB_NAME" "$DB_TEST_NAME"; do
  if [[ -z "$(run_sql "SELECT 1 FROM pg_database WHERE datname = '$database'")" ]]; then
    sudo -u postgres createdb -O "$DB_USER" "$database"
    green "OK  created database $database"
  else
    green "OK  database $database already exists"
  fi
  # pg_trgm powers the Hebrew fuzzy search and needs superuser to install.
  sudo -u postgres psql -qd "$database" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" >/dev/null
done

URL="postgresql+psycopg://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME"
TEST_URL="postgresql+psycopg://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_TEST_NAME"

step "Wiring it into .env"

if [[ -f .env ]] && grep -q '^DATABASE_URL=' .env; then
  # Escape the URL for sed: it contains / and :
  escaped="$(printf '%s' "$URL" | sed 's/[&/\]/\\&/g')"
  sed -i "s|^DATABASE_URL=.*|DATABASE_URL=$escaped|" .env
  green "OK  DATABASE_URL updated in .env"
else
  yellow "No .env with a DATABASE_URL line; set it yourself:"
  echo "    DATABASE_URL=$URL"
fi

cat <<NEXT

────────────────────────────────────────────────────────────────────
Postgres is ready.

  apply the schema
    alembic upgrade head

  run the database tests
    export TEST_DATABASE_URL=$TEST_URL
    pytest tests/

  ingest a small sample end to end
    python -m ingestion cycle --chains SHUFERSAL MAAYAN_2000 --limit 3
    python -m ingestion status

Postgres does not survive a WSL restart. Bring it back with:
    sudo service postgresql start
────────────────────────────────────────────────────────────────────
NEXT
