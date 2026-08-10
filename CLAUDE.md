# CLAUDE.md — Working rules for this repo

מערכת השוואת מחירי מזון בישראל, מבוססת נתוני שקיפות מחירים ממשלתיים.
**קרא את `docs/02-DATA-SOURCES.md` לפני שאתה נוגע בקוד ingestion.** הנתונים
מבולגנים בדרכים שלא תנחש אותן, והמסמך הזה מתעד אותן.

---

## Language & locale

- **ממשק משתמש: עברית, RTL.** כל טקסט פונה למשתמש בעברית.
- **קוד, שמות משתנים, קומיטים, קומנטים: אנגלית.**
- שמות מוצרים ורשתות במסד: עברית, UTF-8, collation `he_IL`.
- Timezone: `Asia/Jerusalem`. כל timestamp נשמר `timestamptz`.
- מטבע: ILS. מחירים כ־`NUMERIC(10,2)` — **לא float.**

---

## Stack (אל תחליף בלי סיבה)

Python 3.11 · FastAPI · PostgreSQL 16 + PostGIS · Redis · Cloudflare R2 · Docker/Caprover

---

## 🚫 עשרת הלאווים

דברים שנראים סבירים ויהרסו את הפרויקט:

1. **אל תכתוב scrapers מאפס.** השתמש ב־`il-supermarket-scraper`. יש 13 סקרייפרים
   מתוחזקים עם בדיקות יומיות. כתיבה מחדש = חודשים של תחזוקה.
2. **אל תטען קובץ XML לזיכרון.** קבצים 5–15MB, מאות בכל ריצה. `lxml.iterparse`
   בלבד. לא `pandas.read_xml`, לא `ElementTree.parse`, לא `BeautifulSoup`.
3. **אל תניח UTF-8.** חלק מהרשתות מפרסמות ב־UTF-16. זהה BOM.
4. **אל תקודד כתובות פורטלים בקוד.** הן בטבלת `chains`. רשתות מחליפות פורטל —
   זה כבר קרה. שינוי כתובת = UPDATE, לא deploy.
5. **אל תבנה solver לאופטימיזציית סל.** brute force על זוגות מספיק ורץ
   במילישניות. ראה `docs/04-ALGORITHMS.md` §3.
6. **אל תפרש מחיר ריק כ־0.** `NULL`. מחיר 0 יזהם כל חישוב סל.
7. **אל תכניס מוצר שלא הותאם בביטחון לחישוב סל.** עדיף "לא נמצא" מהשוואה שגויה.
8. **אל תשמור היסטוריית מחירים ב־Supabase.** Supabase לשכבה האפליקטיבית בלבד.
9. **אל תדלג על Phase 0.** ארבע המדידות קובעות את הסכמה. ראה `docs/05-ROADMAP.md`.
10. **אל תבטל החלטה מ־`docs/06-DECISIONS.md` בלי לקרוא את הנימוק.**

---

## Conventions

### Migrations
Alembic. כל שינוי סכמה = migration. אין `CREATE TABLE` ידני.

### Config
כל ערך מתכוונן ב־`.env` (ראה `.env.example`). אין magic numbers בקוד —
במיוחד ספי התאמה, רדיוסי חיפוש וקנסות נסיעה.

### Errors
Ingestion הוא best-effort: כישלון ברשת אחת לא מפיל את השאר.
כל כישלון נרשם ל־`ingestion_runs` עם `chain_id`, `error`, `file_count`.

### Testing
- Parsers: fixtures אמיתיים מ־`tests/fixtures/` (XML אמיתי, מכווץ, כולל UTF-16)
- Basket optimizer: unit tests עם סלים ידניים ותוצאות ידועות
- אין mocking של המסד — `testcontainers` עם Postgres אמיתי

### Commits
`type(scope): subject` באנגלית. למשל `feat(ingestion): add UTF-16 BOM detection`.

---

## מבנה

```
scripts/     סקריפטים חד־פעמיים לניתוח (Phase 0). לא production.
ingestion/   הורדה (עטיפה על il-supermarket-scraper) + נירמול XML → DB
catalog/     נירמול ברקודים, פירוק גודל אריזה, התאמת מוצרים
api/         FastAPI — endpoints לחיפוש, סל, התראות
db/          Alembic migrations
web/         frontend (מנוהל ב־Lovable, עברית/RTL)
```

---

## הזרימה בקצרה

```
il-supermarket-scraper  →  R2 (raw .xml.gz)
                        →  normalizer (stream parse)
                        →  staging_items
                        →  catalog matching (barcode + pack size)
                        →  price_base / price_exception  (SCD2)
                        →  price_current  (materialized)
                        →  API  →  Lovable frontend
```

---

## מצב נוכחי

Phase 0. אין קוד production. המשימה הראשונה היא `scripts/` — ראה
`docs/05-ROADMAP.md` §Phase 0. **אל תתחיל לבנות pipeline לפני שארבע
המדידות הושלמו ותועדו.**
