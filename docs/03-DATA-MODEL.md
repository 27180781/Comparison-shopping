# 03 — Data Model

PostgreSQL 16 + PostGIS. Alembic migrations.

---

## 1. עקרונות

1. **מחירים כ־`NUMERIC(10,2)`** — לעולם לא float.
2. **כל timestamp הוא `timestamptz`**, מאוחסן UTC, מוצג `Asia/Jerusalem`.
3. **היסטוריה = SCD Type 2.** כל שינוי מחיר פותח שורה חדשה וסוגר קודמת.
4. **מקורות בקונפיג/DB, לא בקוד.**
5. **מחיר חסר = `NULL`, לא 0.**

---

## 2. 🔑 base + exceptions — התובנה המרכזית

בתוך אותה קבוצת מחיר, המחירים כמעט זהים בין סניפים.
לכן **אין שורת מחיר לכל (מוצר × סניף)**:

```
price_base       (price_group_id, variant_id, price, ...)   ← הרוב המוחלט
price_exception  (store_id,       variant_id, price, ...)   ← חריגים בלבד
```

מחיר אפקטיבי = `COALESCE(exception, base)`.

**תוצאה:** מספר הסניפים כמעט לא משפיע על נפח האחסון. לכן קליטה ארצית,
וגיאוגרפיה כסינון בשאילתה בלבד.

✅ **אומת ב-Phase 0 מדידה #2: 6.59% חריגות**, גודל סטייה חציוני 6.25%,
על 26,780 מחירי סניף בני־השוואה מ-5 רשתות. הסף היה 15%. **המודל מוחזק.**
ראה [`PHASE0-FINDINGS.md`](PHASE0-FINDINGS.md) §0.

✅ **קבוצת מחיר = `SubChain`.** נמצא אמפירית: שופרסל מפרסמת
`SubChainId` ✚ `SubChainName` עם ערכים כמו `שופרסל שלי` — 10 תת־רשתות על
פני 420 סניפים. זהו בדיוק ה-`price_group_id`.
⚠️ אצל מעיין 2000 לעומת זאת `SubChainName` הוא `1` — חסר משמעות.
**אל תסתמך על `SubChainName` כתווית; השתמש ב-`SubChainId` כמפתח.**

---

## 3. DDL

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ═══ מקורות ═══════════════════════════════════════════
CREATE TABLE chains (
  id                SERIAL PRIMARY KEY,
  name_he           TEXT NOT NULL,
  gov_chain_id      TEXT,
  scraper_name      TEXT NOT NULL,        -- 'MAAYAN_2000'
  scraper_fallback  TEXT,                 -- 'VICTORY_NEW_SOURCE'
  portal_type       TEXT NOT NULL,        -- cerberus|bina|shufersal|api
  portal_url        TEXT,
  credentials_ref   TEXT,                 -- מפתח ב-secret store, לא הסוד עצמו
  is_active         BOOLEAN DEFAULT TRUE
);

CREATE TABLE price_groups (
  id        SERIAL PRIMARY KEY,
  chain_id  INT NOT NULL REFERENCES chains(id),
  label     TEXT NOT NULL                  -- 'שופרסל שלי'
);

CREATE TABLE stores (
  id                 SERIAL PRIMARY KEY,
  chain_id           INT NOT NULL REFERENCES chains(id),
  price_group_id     INT REFERENCES price_groups(id),
  store_code         TEXT NOT NULL,
  name_he            TEXT,
  address            TEXT,
  city               TEXT,
  lat                DOUBLE PRECISION,
  lng                DOUBLE PRECISION,
  geom               geography(Point,4326),
  geocode_confidence REAL,
  is_active          BOOLEAN DEFAULT TRUE,
  UNIQUE (chain_id, store_code)
);
CREATE INDEX idx_stores_geom ON stores USING GIST (geom);

-- ═══ קטלוג ════════════════════════════════════════════
CREATE TABLE categories (
  id         SERIAL PRIMARY KEY,
  name_he    TEXT NOT NULL,
  parent_id  INT REFERENCES categories(id)
);

CREATE TABLE canonical_products (
  id               SERIAL PRIMARY KEY,
  barcode          TEXT UNIQUE NOT NULL,   -- EAN-13 מנורמל
  name_he          TEXT NOT NULL,
  brand            TEXT,
  category_id      INT REFERENCES categories(id),
  pack_count       INT     DEFAULT 1,      -- מארז 4 → 4
  unit_size        NUMERIC(10,3),          -- 250
  unit_of_measure  TEXT,                   -- 'g'|'ml'|'unit'
  base_size        NUMERIC(12,3)
      GENERATED ALWAYS AS (pack_count * unit_size) STORED,
  chain_count      INT,                    -- בכמה רשתות נמצא
  image_url        TEXT,
  created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_cp_name_trgm ON canonical_products USING GIN (name_he gin_trgm_ops);

CREATE TABLE product_variants (
  id                SERIAL PRIMARY KEY,
  canonical_id      INT REFERENCES canonical_products(id),
  chain_id          INT NOT NULL REFERENCES chains(id),
  item_code         TEXT NOT NULL,
  item_type         SMALLINT,              -- 1=ברקוד גלובלי, 0=פנימי
  raw_name_he       TEXT NOT NULL,
  is_weighted       BOOLEAN DEFAULT FALSE,
  is_private_label  BOOLEAN DEFAULT FALSE,
  match_method      TEXT,                  -- barcode|embedding|manual
  match_confidence  REAL,
  UNIQUE (chain_id, item_code)
);

-- ═══ מחירים (SCD2) ════════════════════════════════════
CREATE TABLE price_base (
  id              BIGSERIAL PRIMARY KEY,
  price_group_id  INT NOT NULL REFERENCES price_groups(id),
  variant_id      INT NOT NULL REFERENCES product_variants(id),
  price           NUMERIC(10,2),
  unit_price      NUMERIC(10,4),
  valid_from      TIMESTAMPTZ NOT NULL,
  valid_to        TIMESTAMPTZ
);
CREATE INDEX idx_pb_open ON price_base (price_group_id, variant_id)
  WHERE valid_to IS NULL;

CREATE TABLE price_exception (
  id          BIGSERIAL PRIMARY KEY,
  store_id    INT NOT NULL REFERENCES stores(id),
  variant_id  INT NOT NULL REFERENCES product_variants(id),
  price       NUMERIC(10,2),
  unit_price  NUMERIC(10,4),
  valid_from  TIMESTAMPTZ NOT NULL,
  valid_to    TIMESTAMPTZ
);
CREATE INDEX idx_pe_open ON price_exception (store_id, variant_id)
  WHERE valid_to IS NULL;

-- טבלת קריאה מהירה. מתוחזקת ע"י job אחרי כל ingestion.
CREATE TABLE price_current (
  store_id               INT NOT NULL REFERENCES stores(id),
  variant_id             INT NOT NULL REFERENCES product_variants(id),
  canonical_id           INT REFERENCES canonical_products(id),
  price                  NUMERIC(10,2),
  normalized_unit_price  NUMERIC(10,4),   -- ₪ ל-100g / 100ml / יחידה
  updated_at             TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (store_id, variant_id)
);
CREATE INDEX idx_pc_canonical ON price_current (canonical_id);

-- ═══ מבצעים ═══════════════════════════════════════════
CREATE TABLE promotions (
  id                 BIGSERIAL PRIMARY KEY,
  chain_id           INT NOT NULL REFERENCES chains(id),
  store_id           INT REFERENCES stores(id),
  promo_code         TEXT,
  description_he     TEXT,
  discount_type      TEXT,
  reward_type        TEXT,
  min_qty            NUMERIC(10,2),
  discount_rate      NUMERIC(6,3),
  discounted_price   NUMERIC(10,2),
  club_id            TEXT,
  allow_stacking     BOOLEAN,
  starts_at          TIMESTAMPTZ,
  ends_at            TIMESTAMPTZ,
  parse_status       TEXT   -- structured|partial|text_only
);
CREATE INDEX idx_promo_active ON promotions (store_id, starts_at, ends_at);

CREATE TABLE promotion_items (
  promotion_id  BIGINT NOT NULL REFERENCES promotions(id) ON DELETE CASCADE,
  variant_id    INT NOT NULL REFERENCES product_variants(id),
  PRIMARY KEY (promotion_id, variant_id)
);

-- ═══ משתמש ════════════════════════════════════════════
CREATE TABLE users (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_locations (
  id       SERIAL PRIMARY KEY,
  user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label    TEXT,
  geom     geography(Point,4326) NOT NULL
);

CREATE TABLE baskets (
  id          SERIAL PRIMARY KEY,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE basket_items (
  basket_id     INT NOT NULL REFERENCES baskets(id) ON DELETE CASCADE,
  canonical_id  INT NOT NULL REFERENCES canonical_products(id),
  qty           NUMERIC(10,2) NOT NULL DEFAULT 1,
  PRIMARY KEY (basket_id, canonical_id)
);

CREATE TABLE price_alerts (
  id              SERIAL PRIMARY KEY,
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  canonical_id    INT NOT NULL REFERENCES canonical_products(id),
  threshold_type  TEXT,     -- 'below'|'pct_drop'
  threshold_value NUMERIC(10,2)
);

-- ═══ תפעול ════════════════════════════════════════════
CREATE TABLE ingestion_runs (
  id           BIGSERIAL PRIMARY KEY,
  chain_id     INT REFERENCES chains(id),
  started_at   TIMESTAMPTZ NOT NULL,
  finished_at  TIMESTAMPTZ,
  file_count   INT,
  row_count    BIGINT,
  status       TEXT,       -- ok|partial|failed
  error        TEXT
);

CREATE TABLE staging_items (
  chain_id     INT,
  store_code   TEXT,
  item_code    TEXT,
  item_type    SMALLINT,
  raw_name_he  TEXT,
  price        NUMERIC(10,2),
  unit_price   NUMERIC(10,4),
  is_weighted  BOOLEAN,
  file_date    DATE
);
```

---

## 4. אינדקסים קריטיים

| אינדקס | למה |
|---|---|
| `idx_stores_geom` (GIST) | `ST_DWithin` בכל שאילתת רדיוס |
| `idx_pc_canonical` | חיפוש מוצר בודד |
| `idx_cp_name_trgm` (GIN) | חיפוש טקסט עברי מטושטש |
| `idx_pb_open` / `idx_pe_open` (partial) | רק שורות פתוחות = הרוב המכריע של השאילתות |

---

## 5. אם הנפח גדל

מדוד אחרי חודשיים. אם `price_base`/`price_exception` הופכים כבדים:
העבר היסטוריה ל־**ClickHouse** על Caprover, השאר ב־Postgres רק
`price_current` ✚ קטלוג ✚ גיאו ✚ משתמשים.

**ההיסטוריה היא הנכס האמיתי** — היא מאפשרת את השאלה שאף אחד לא עונה עליה:
*"המבצע הזה באמת מבצע, או שהמחיר היה ככה כל השנה?"*
