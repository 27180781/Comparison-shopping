# מערכת השוואת מחירים — Israeli Grocery Price Comparison

מערכת השוואת מחירים לצרכן פרטי, מבוססת על נתוני שקיפות המחירים
שרשתות המזון בישראל מחויבות לפרסם לפי חוק קידום התחרות בענף המזון.

**המטרה:** לענות למשתמש על שאלה אחת בדיוק —
> "הנה 30 המוצרים שאני קונה כל שבוע. איפה הכי משתלם לקנות אותם היום, בהתחשב במרחק?"

---

## Status

🚧 **Phase 0 — Data Reality Check.** לא נכתב עדיין קוד production.

| משימה | מצב |
|---|---|
| 0.1 — אימות מיפוי הסקרייפרים | ✅ `scripts/phase0_verify_scrapers.py` |
| 0.2–0.5 — הורדות וארבע המדידות | ⛔ דורש IP ישראלי וגישת רשת |

ממצאים: [`docs/PHASE0-FINDINGS.md`](docs/PHASE0-FINDINGS.md).
שלבים וקריטריוני קבלה: [`docs/05-ROADMAP.md`](docs/05-ROADMAP.md).

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

## Phase 0 quickstart

**Phase 0 לא פורס כלום.** אין Docker, אין מסד, אין Caprover — רק ספריית פייתון
אחת וסקריפטים חד־פעמיים. התשתית נכנסת ב־Phase 1.

> 🔴 **Linux או macOS בלבד. לא Windows מקומי.**
> `il-supermarket-scraper` מייבא `fcntl` — מודול POSIX שלא קיים ב-Windows.
> `import il_supermarket_scarper` נכשל שם מיידית. ראה `PHASE0-FINDINGS.md` F-11.
> **על Windows: WSL2** (למטה). production על Docker/Caprover לא מושפע.

> ⚠️ **דורש Python 3.11–3.13. לא 3.14.**
> הספרייה נועלת `lxml<6.0.0`, ול־lxml 5.x אין wheel ל־cp314 — pip ינסה לקמפל
> מקוד C. בדוק עם `python3 --version`.

<details open>
<summary><b>Windows — דרך WSL2</b></summary>

ב-PowerShell כמנהל, פעם אחת:

```powershell
wsl --install
```

אתחל את המחשב, ואז בטרמינל **Ubuntu** (לא CMD):

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git
git clone https://github.com/27180781/Comparison-shopping.git
cd Comparison-shopping
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

שכפל בתוך ה־home של Ubuntu, לא תחת `/mnt/c/` — venv מעבר לגבול מערכות
הקבצים איטי, ונתיבים עם עברית מסבכים.
</details>

<details>
<summary><b>macOS / Linux</b></summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```
</details>

ואז, זהה בכל מערכת הפעלה:

```bash
python scripts/phase0_verify_scrapers.py     # 0.1 — אימות מול הספרייה
python scripts/phase0_download.py stores     # 0.2 — קבצי סניפים
python scripts/phase0_download.py prices     # 0.3 — snapshot מחירים ומבצעים
python scripts/phase0_peek.py --list         # 0.4 — מה ירד
python scripts/phase0_peek.py                # 0.4 — לפתוח ולהסתכל
```

> ⚠️ **חלק מאתרי הרשתות חוסמים גישה מחוץ לישראל.**
> ה־scraping חייב לרוץ מ־IP ישראלי — פיתוח מקומי בישראל, production על Caprover בישראל.
> אפס קבצים מכל שלוש הרשתות ⇒ חסימה גיאוגרפית, לא באג.

---

## Local setup (Phase 1 ואילך — עדיין לא רלוונטי)

```bash
cp .env.example .env      # מלא את הערכים
docker compose up -d db redis
pip install -r requirements.txt
alembic upgrade head
```

---

## License

Private.
