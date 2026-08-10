# מערכת השוואת מחירים — Israeli Grocery Price Comparison

מערכת השוואת מחירים לצרכן פרטי, מבוססת על נתוני שקיפות המחירים
שרשתות המזון בישראל מחויבות לפרסם לפי חוק קידום התחרות בענף המזון.

**המטרה:** לענות למשתמש על שאלה אחת בדיוק —
> "הנה 30 המוצרים שאני קונה כל שבוע. איפה הכי משתלם לקנות אותם היום, בהתחשב במרחק?"

---

## Status

🚧 **Phase 0 — Data Reality Check.** לא נכתב עדיין קוד production.

ראה [`docs/05-ROADMAP.md`](docs/05-ROADMAP.md) לשלבים ולקריטריוני קבלה.

---

## Quick orientation for a new contributor (human or agent)

קרא בסדר הזה:

| # | קובץ | מה בו |
|---|---|---|
| 1 | [`CLAUDE.md`](CLAUDE.md) | חוקי עבודה, מה לא לעשות, סטאק |
| 2 | [`docs/01-SPEC.md`](docs/01-SPEC.md) | מה המוצר עושה ולמי |
| 3 | [`docs/02-DATA-SOURCES.md`](docs/02-DATA-SOURCES.md) | **קריטי** — מאיפה מגיעים הנתונים ולמה הם מבולגנים |
| 4 | [`docs/03-DATA-MODEL.md`](docs/03-DATA-MODEL.md) | סכמת DB מלאה |
| 5 | [`docs/04-ALGORITHMS.md`](docs/04-ALGORITHMS.md) | ברקודים, גודל אריזה, אופטימיזציית סל, מבצעים |
| 6 | [`docs/05-ROADMAP.md`](docs/05-ROADMAP.md) | שלבים + קריטריוני קבלה |
| 7 | [`docs/06-DECISIONS.md`](docs/06-DECISIONS.md) | החלטות ארכיטקטורה ולמה — **אל תבטל בלי לקרוא** |

---

## Stack

| שכבה | טכנולוגיה |
|---|---|
| Scraping | `il-supermarket-scraper` (Python) — **לא לכתוב מאפס** |
| Orchestration | Docker על Caprover, cron |
| Raw storage | Cloudflare R2 (S3-compatible) |
| DB | PostgreSQL 16 + PostGIS |
| Cache | Redis |
| API | Python 3.11 + FastAPI |
| Frontend | Lovable (React + Tailwind), עברית/RTL |

---

## Repo layout

```
.
├── CLAUDE.md              ← קרא ראשון
├── docs/                  ← אפיון מלא
├── scripts/               ← סקריפטים לשלב 0 (ניתוח חד־פעמי)
├── ingestion/             ← הורדה + נירמול
├── catalog/               ← זיהוי והתאמת מוצרים
├── api/                   ← FastAPI
├── db/                    ← migrations
└── web/                   ← frontend (Lovable)
```

---

## Local setup

```bash
cp .env.example .env      # מלא את הערכים
docker compose up -d db redis
pip install -r requirements.txt
alembic upgrade head
```

> ⚠️ **חלק מאתרי הרשתות חוסמים גישה מחוץ לישראל.**
> ה־scraping חייב לרוץ מ־IP ישראלי — פיתוח מקומי בישראל, production על Caprover בישראל.

---

## License

Private.
