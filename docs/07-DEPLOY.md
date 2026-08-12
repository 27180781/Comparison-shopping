# 07 — פריסה לאוויר

המסמך הזה עונה על שאלה אחת: **מה בדיוק צריך לרוץ איפה כדי שהמערכת תהיה
אונליין ותתעדכן לבד מכל הרשתות.**

---

## §1 למה Lovable + Supabase לבד לא מספיקים

זו האינטואיציה הטבעית — frontend ב־Lovable, נתונים ב־Supabase, גמרנו. היא
לא עובדת כאן, ולא בגלל העדפה ארכיטקטונית אלא בגלל שלוש מגבלות קונקרטיות:

| מה שצריך לקרות | למה Supabase Edge / Lovable לא יכולים |
|---|---|
| להתחבר ל־FTPS ול־FTP לפורטלים של הרשתות | Edge Functions רצות ב־Deno על סנדבוקס HTTP. אין שקע FTP, ואין את `il-supermarket-scraper` (Python). |
| לפרסר 5–15GB XML ביום ב־streaming | Edge Function מוגבלת לשניות בודדות ולזיכרון קטן. ריצה מלאה של רשת אחת היא עשרות דקות. |
| להיקרא מ־IP ישראלי | חלק מהפורטלים חוסמים גישה מחו"ל (`docs/02-DATA-SOURCES.md` §geo-blocking). התשתית של Supabase לא בישראל. |

ובנוסף, החלטה שכבר קיימת בפרויקט: **היסטוריית מחירים לא נשמרת ב־Supabase.**
Supabase — אם בכלל — לשכבה האפליקטיבית בלבד: משתמשים, סלים שמורים, התראות.
ראה `CLAUDE.md` §לאו 8.

הקטלוג עצמו הוא ~950 סניפים, מאות אלפי `price_current` ו־SCD2 שגדל כל יום.
זה Postgres שלכם, לא Postgres מנוהל של פלטפורמת frontend.

---

## §2 מה כן — הטופולוגיה

שלושה רכיבים. כולם על ה־Caprover שכבר יש לכם.

```
┌─ Caprover ───────────────────────────────────────────────┐
│                                                          │
│  postgres-16      ← המסד. קטלוג, היסטוריה, price_current │
│                                                          │
│  price-api        ← FastAPI. מגיש גם את הממשק ב-/app     │
│     Dockerfile, CMD ברירת המחדל                          │
│                                                          │
│  price-worker     ← אותו image, entrypoint אחר.          │
│     deploy/worker-entrypoint.sh → alembic + cron         │
│                                                          │
└──────────────────────────────────────────────────────────┘
              │
              ├──→ Cloudflare R2   (ארכיון הקבצים הגולמיים, אופציונלי)
              └──→ Google Maps API (גיאוקודינג סניפים, אופציונלי)
```

**הממשק כבר מוגש מה־API.** `api/main.py` מרים את `web/` תחת `/app`, ו־`/`
מפנה לשם. אין build step, אין origin שני, אין CORS. פריסה של `price-api`
מעלה גם את האתר.

---

## §3 הרצה — Caprover, שלב אחר שלב

### 3.1 מסד הנתונים

ב־Caprover: **One-Click Apps → PostgreSQL**, גרסה 16.

חשוב — collation עברי. אם ה־one-click לא נותן לבחור, צרו את המסד ידנית:

```sql
CREATE DATABASE pricecompare
  ENCODING 'UTF8'
  LOCALE_PROVIDER icu
  ICU_LOCALE 'he-IL'
  TEMPLATE template0;
```

ואז, פעם אחת:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

בלי `pg_trgm` החיפוש בעברית לא עובד — הוא נשען על `word_similarity`.

### 3.2 האפליקציה

```bash
# מהמחשב שלכם
caprover deploy -a price-api
```

Caprover קורא את `captain-definition` ובונה את ה־`Dockerfile`.

**App Configs → Environmental Variables:**

```
DATABASE_URL=postgresql://price:<סיסמה>@srv-captain--postgres:5432/pricecompare
CORS_ORIGINS=https://<הדומיין שלכם>
LOG_LEVEL=INFO

# אופציונלי אבל מומלץ — ארכיון ביקורת של הקבצים כפי שפורסמו
R2_ACCOUNT_ID=…
R2_ACCESS_KEY_ID=…
R2_SECRET_ACCESS_KEY=…
R2_BUCKET=price-raw
R2_ENDPOINT=https://<account>.r2.cloudflarestorage.com

# אופציונלי — בלעדיו חיפוש לפי מרחק מחזיר ריק
GOOGLE_MAPS_API_KEY=…
```

`postgresql://` מנורמל אוטומטית ל־psycopg 3, אז אין צורך לזכור את הסיומת.

`REDIS_URL` מופיע ב־`.env.example` אבל **שום קוד לא קורא אותו היום** — הוא
שמור לשכבת קאשינג עתידית. אל תפרסו Redis בשבילו.

**Container HTTP Port:** 8000. **Enable HTTPS** + **Force HTTPS**.

### 3.3 העובד

אפליקציה שנייה, `price-worker`, מאותו repo ומאותו image. ההבדל היחיד הוא
ה־entrypoint:

**App Configs → Service Update Override:**

```json
{
  "TaskTemplate": {
    "ContainerSpec": {
      "Command": ["/app/deploy/worker-entrypoint.sh"],
      "HealthCheck": { "Test": ["NONE"] }
    }
  }
}
```

**ה־`HealthCheck` הוא לא קישוט.** ה־`Dockerfile` מגדיר בדיקת בריאות שמושכת
`http://localhost:8000/health` — נכון ל־API, קטלני לעובד: העובד לא מרים שרת
HTTP, הבדיקה נכשלת, ו־Swarm מפעיל אותו מחדש בלולאה באמצע קליטה. `["NONE"]`
מבטל אותה עבור העובד בלבד.

אותם משתני סביבה כמו ה־API. בנוסף:

```
RUN_ON_START=true
FILES_PER_CHAIN_LIMIT=0     # 0 = בלי הגבלה
KEEP_LOCAL_FILES=false
```

**Persistent Directory:** `/app/dumps`. בלעדיו ריסטארט באמצע ריצה מוריד
מחדש גיגה־בייטים.

ה־entrypoint מריץ `alembic upgrade head`, מבצע מחזור אחד כדי שלפריסה טרייה
יהיו נתונים מיד, ואז מוסר לקרון. **את המיגרציות מריץ רק העובד** — שתי
אפליקציות שמריצות `alembic upgrade` בו־זמנית מתנגשות.

---

## §4 העדכון האוטומטי

`deploy/crontab`, שעון ישראל:

| מתי | פקודה | מה זה מביא |
|---|---|---|
| 04:30 יומי | `python -m ingestion cycle` | סניפים, מחירים מלאים, מבצעים מלאים, גיאוקודינג |
| כל שעה 06:00–03:00 | `python -m ingestion cycle --deltas` | קבצי שינויים בלבד |

הריצה היומית אחרי 04:00 כי שם נוחתים הסנאפשוטים המלאים. שעתי ולא צפוף
יותר כי החוק נותן לקמעונאי שעה מהקופה ועד הקובץ — כל דבר צפוף מזה לא קונה
דבר.

**מה שהופך את זה לזול:** טבלת `ingested_files`. הרשתות מפרסמות את אותו
סנאפשוט כמה פעמים ביום תחת אותו שם, ולסקרייפר אין זיכרון בין ריצות. הטבלה
רושמת (רשת, שם קובץ, גודל) אחרי שהקובץ נפרסר, והריצה הבאה פשוט מדלגת עליו.
ריצה שלא מצאה כלום חדש מסתיימת בשניות ומקבלת סטטוס `unchanged` — לא
`no_files`, כי מערכת בריאה בלי עבודה היא לא פורטל שהפסיק לשרת, ורק על
השני צריך להתריע.

**דרישה שאי אפשר לעקוף:** השרת חייב IP ישראלי. חלק מהפורטלים חוסמים גישה
מחו"ל. VPS בישראל, או exit ישראלי.

---

## §5 בדיקה שהכול חי

```bash
curl https://<domain>/health
```

```json
{"status":"ok","database":true,"chains_active":12,
 "products":48213,"last_ingestion":"2026-08-12T04:41:02Z","stale_chains":[]}
```

`stale_chains` הוא הדבר שכדאי לנטר: רשת שלא נקלטה 36 שעות היא פורטל שבור,
לא שבוע שקט. `GET /coverage` נותן את אותה תמונה לפי רשת, והטאב "כיסוי"
בממשק מציג אותה למי שלא מסתכל ב־curl.

מצב הריצות מהשורה:

```bash
python -m ingestion status --hours 48
```

---

## §6 ואם בכל זאת Lovable

אפשר, אבל בתפקיד אחד בלבד: **frontend מעוצב שקורא ל־API שלכם.**

```
Lovable (React/TS)  ──HTTPS──→  price-api על Caprover  ──→  Postgres
```

מה שצריך: להוסיף את הדומיין של Lovable ל־`CORS_ORIGINS`, ולתת לפרויקט שם
את ה־OpenAPI מ־`https://<domain>/openapi.json`.

`web/` שבריפו נשאר כמו שהוא — ממשק עובד, בלי build, שמגיע יחד עם ה־API.
הוא לא תחליף ל־Lovable ולא מתחרה בו; הוא מה שמבטיח שהנתונים נראים גם ביום
שבו אין frontend אחר.

**Supabase**, אם תרצו: משתמשים, סלים שמורים, התראות מחיר. לא קטלוג, ולא
היסטוריית מחירים. הפרדה שהוחלטה מראש ולא כדאי לבטל בלי לקרוא את הנימוק
(`docs/06-DECISIONS.md`).

---

## §7 מה עוד פתוח לפני production

| פריט | מצב |
|---|---|
| `GOOGLE_MAPS_API_KEY` | חסר. בלעדיו חיפוש לפי מרחק מחזיר ריק, והסל עובד בלי מיקום |
| נתיב החסד | הפורטל מחזיר HTTP 500 מיולי. 11 מתוך 12 רשתות נקלטות |
| מעיין 2000 | הפורטל מגיש היסטוריה ארוכה של קבצי סניפים; 0 שורות מחיר |
| מבצעי N+1 | `RewardType` שונה בין רשתות, לא ממופה. נספרים ומוצגים כ"לא נכללו" |
