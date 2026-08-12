# מערכת השוואת מחירים — Israeli Grocery Price Comparison

מערכת השוואת מחירים לצרכן פרטי, מבוססת על נתוני שקיפות המחירים
שרשתות המזון בישראל מחויבות לפרסם לפי חוק קידום התחרות בענף המזון.

**המטרה:** לענות למשתמש על שאלה אחת בדיוק —
> "הנה 30 המוצרים שאני קונה כל שבוע. איפה הכי משתלם לקנות אותם היום, בהתחשב במרחק?"

---

## Status

✅ **Phase 0 הושלם** (2026-08-11) — ארבע המדידות בוצעו על נתונים אמיתיים.
🏗 **Phases 1–4 מומשו** — פייפליין, קטלוג, מחירים, API וסל. 133 טסטים עוברים.

| מדידה | תוצאה | סף | |
|---|---|---|---|
| #1 ברקודים תקינים | **97.86%** | < 50% פוסל | ✅ |
| #2 סטיית מחיר בין סניפים | **6.59%** | > 15% פוסל | ✅ ADR-002 מאומת |
| #3 מבצעים משדות מובנים | **99.72%** | — | ⚠️ ראה סייג |
| #4 `CATALOG_MIN_CHAIN_COUNT` | **2** | הברך בגרף | ✅ היה 4 |

**פתוח:** Cerberus (רמי לוי, אושר עד) ו-laibcatalog (ויקטורי, מחסני השוק)
לא נקלטות — 4 מתוך 12 רשתות. ראה [`docs/PHASE0-FINDINGS.md`](docs/PHASE0-FINDINGS.md).

---

## איך זה עובד

```
il-supermarket-scraper  →  R2 (raw .xml.gz, ארכיון ביקורת)
                        →  normalizer (lxml.iterparse, זיהוי BOM ו-gzip)
                        →  staging_items
                        →  catalog (ברקוד → canonical_products)
                        →  price_base / price_exception  (SCD2)
                        →  price_current  (מטריאליזציה)
                        →  API  →  Lovable frontend
```

**התובנה שמחזיקה את הסכמה:** בתוך קבוצת מחיר (`SubChainId`) המחירים כמעט
זהים בין סניפים — נמדד 6.59% חריגות. לכן מחיר נשמר פעם אחת לקבוצה, ורק
המחלוקות נשמרות לפי סניף. מספר הסניפים כמעט לא משפיע על נפח ההיסטוריה.

**ההיסטוריה היא הנכס.** כל שינוי מחיר סוגר שורה ופותח חדשה, ולכן המערכת
עונה על השאלה שאף אפליקציה לא עונה עליה: *המבצע הזה באמת מבצע, או שהמחיר
היה ככה כל השנה?*

---

## Quick start

> 🔴 **Linux או macOS בלבד.** הספרייה מייבאת `fcntl` — מודול POSIX שלא קיים
> ב-Windows. שם: WSL2. ראה `PHASE0-FINDINGS.md` F-11.
>
> ⚠️ **Python 3.11–3.13, לא 3.14** — ל-lxml 5.x אין wheel ל-cp314 בשום פלטפורמה.

```bash
git clone https://github.com/27180781/Comparison-shopping.git
cd Comparison-shopping
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv venv --python 3.13 && source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env
```

### עם Docker (מומלץ)

```bash
docker compose up -d db redis
docker compose run --rm api alembic upgrade head
docker compose up api
```

ה-API על `http://localhost:8000`, תיעוד אינטראקטיבי על `/docs`.

### בלי Docker

```bash
alembic upgrade head
uvicorn api.main:app --reload
```

---

## הפעלת הקליטה

```bash
python -m ingestion cycle                       # מחזור מלא: הורדה → קטלוג → מחירים
python -m ingestion cycle --deltas              # דלתות שעתיות
python -m ingestion download --chains SHUFERSAL --limit 2
python -m ingestion status                      # מה קרה בריצות האחרונות
```

> ⚠️ **ה-scraping חייב לרוץ מ-IP ישראלי.** חלק מהאתרים חוסמים גישה מחו"ל.
> אפס קבצים מכל הרשתות ⇒ חסימה גיאוגרפית, לא באג.

---

## API

| endpoint | מה הוא עושה |
|---|---|
| `GET /products?sort=spread&chain_ids=&max_price=&promo_only=` | דפדוף בקטלוג, ממוין לפי הפער בין הזול ליקר |
| `GET /filters` | אוצר המילים לסינון — רשתות, מותגים, קטגוריות — נקרא מהנתונים |
| `GET /search?q=&lat=&lng=&radius_km=` | חיפוש מוצר, ממוין לפי ₪ ליחידה |
| `GET /products/{id}/prices` | טבלת הסניפים למוצר, בלי לחפש |
| `GET /products/{id}/history?days=90` | גרף מחיר + "נמוך/גבוה מהרגיל" |
| `POST /basket/optimize` | סניף אחד מול פיצול, עם חיסכון ותוספת זמן |
| `GET /coverage` | מה הקטלוג מכסה, לפי רשת |
| `GET /health` | טריות נתונים ורשתות שהפסיקו לדווח |

הממשק בעברית מוגש מאותו תהליך תחת `/app` — אין build step ואין origin שני.

כל תשובה כוללת חותמת עדכון לכל מחיר, את הדיסקליימר *"המחיר בקופה גובר"*,
ואת מספר המבצעים שלא נכללו בחישוב. אלה לא קישוטים — ראה ADR-010.

---

## פריסה ל-Caprover

```bash
caprover deploy   # captain-definition כבר ברפו
```

שתי אפליקציות מאותו image:

| אפליקציה | פקודה | הערות |
|---|---|---|
| `api` | ברירת המחדל של ה-Dockerfile | חושף 8000, health check מובנה |
| `worker` | `deploy/worker-entrypoint.sh` | cron יומי 04:30 ✚ שעתי, volume ל-`/app/dumps` |

הגדר `DATABASE_URL`, `REDIS_URL` ומפתחות R2 כ-env vars ב-Caprover.
**אל תקודד כתובות פורטלים** — הן בטבלת `chains` (ADR-006).

השרת חייב IP ישראלי — חלק מהפורטלים חוסמים גישה מחו"ל.
המדריך המלא, כולל למה Supabase לא מחליף את המסד:
[`docs/07-DEPLOY.md`](docs/07-DEPLOY.md).

---

## מבנה

```
ingestion/   הורדה, פריסת XML, נירמול, פייפליין
catalog/     ברקודים, פירוק אריזה, canonical, מחירים SCD2, מבצעים
api/         FastAPI — חיפוש, היסטוריה, סל
db/          Alembic migrations
scripts/     כלי Phase 0 (חד־פעמיים, לא production)
tests/       133 טסטים; טסטי DB מול Postgres אמיתי
deploy/      cron ו-entrypoint
```

---

## טסטים

```bash
pytest tests/                                   # ללא DB — פרסרים, ברקוד, סל
createdb pricetest
TEST_DATABASE_URL=postgresql+psycopg://user@localhost/pricetest pytest tests/
```

טסטי המסד רצים מול Postgres אמיתי, לא mock — שכבת המחירים היא כמעט כולה SQL,
ו-mock היה בודק את ה-mock.

---

## תיעוד

| # | קובץ | מה בו |
|---|---|---|
| 1 | [`CLAUDE.md`](CLAUDE.md) | חוקי עבודה, מה לא לעשות |
| 2 | [`docs/01-SPEC.md`](docs/01-SPEC.md) | מה המוצר עושה ולמי |
| 3 | [`docs/02-DATA-SOURCES.md`](docs/02-DATA-SOURCES.md) | **קריטי** — מאיפה הנתונים ולמה הם מבולגנים |
| 4 | [`docs/03-DATA-MODEL.md`](docs/03-DATA-MODEL.md) | סכמה |
| 5 | [`docs/04-ALGORITHMS.md`](docs/04-ALGORITHMS.md) | ברקודים, אריזה, סל, מבצעים |
| 6 | [`docs/05-ROADMAP.md`](docs/05-ROADMAP.md) | שלבים וקריטריוני קבלה |
| 7 | [`docs/06-DECISIONS.md`](docs/06-DECISIONS.md) | החלטות ולמה — **אל תבטל בלי לקרוא** |
| 8 | [`docs/07-DEPLOY.md`](docs/07-DEPLOY.md) | איך זה עולה לאוויר ומתעדכן לבד |
| 8 | [`docs/PHASE0-FINDINGS.md`](docs/PHASE0-FINDINGS.md) | המדידות ו-13 הממצאים |

---

## License

Private.
