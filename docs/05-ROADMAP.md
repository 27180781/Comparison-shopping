# 05 — Roadmap

כל שלב מוגדר בקריטריוני קבלה. **אל תעבור שלב לפני שהם מסומנים.**

---

## Phase 0 — Data Reality Check ⏱ 2–3 ימים

**🚫 אל תכתוב קוד production בשלב הזה. אל תקים תשתית. אל תיצור סכמה.**

ארבע מדידות קובעות את `03-DATA-MODEL.md` ואת `04-ALGORITHMS.md`.
לשנות סכמה אחרי שיש בה נתונים יקר בהרבה מלחכות שלושה ימים.

### משימות

```bash
pip install il-supermarket-scraper
```

**0.1 — אמת את מיפוי הסקרייפרים** ✅ בוצע

```bash
python scripts/phase0_verify_scrapers.py     # יוצא 0 כשכל 13 נפתרים
```

התוצאות ב-[`PHASE0-FINDINGS.md`](PHASE0-FINDINGS.md) §1.
הרץ מחדש בכל שדרוג של הספרייה — שמות סקרייפרים נעלמים כשרשת מחליפה פורטל.

**0.2 — הורד קבצי סניפים בלבד** (קטן ומהיר)

```bash
python scripts/phase0_download.py stores
```

**0.3 — הורד קובץ מחירים ומבצעים אחד לכל רשת**

```bash
python scripts/phase0_download.py prices
```

<details>
<summary>מה הסקריפט עושה מאחורי הקלעים</summary>

```python
from il_supermarket_scarper import ScarpingTask
task = ScarpingTask(
    enabled_scrapers=['MAAYAN_2000', 'RAMI_LEVY', 'SHUFERSAL'],
    files_types=['STORE_FILE'],
)
task.start()
task.join()      # ← חובה. בלעדיו לא יורד כלום.
```

שלוש מלכודות ב-API, כולן תועדו ב-`PHASE0-FINDINGS.md`:
- `start()` מפעיל **daemon thread** וחוזר מיד. בלי `join()` התהליך מסתיים
  וההורדה נהרגת באמצע (F-12).
- הפרמטר הוא `files_types`, לא `enabled_file_types` (F-3).
- אל תיצור מחלקות סקרייפר ישירות — רק דרך `ScarpingTask` (F-8).
</details>

**0.4 — פתח קובץ אחד בעורך והסתכל בו בעיניים.**
לפני שכותבים parser. כמה שדות, איזה קידוד, איך נראה `ItemType`,
איך נראה שם מוצר, מה יש ב-`PromotionDescription`.

**0.5 — ארבע המדידות** (`scripts/phase0_*.py`)

| # | מדידה | קובע | סף החלטה |
|---|---|---|---|
| 1 | % פריטים עם `ItemType=1` וברקוד תקין | גודל קטלוג v1 | < 50% → לשקול מחדש |
| 2 | שונות מחיר בין סניפים באותה רשת | `base + exceptions` | > 15% חריגות → לנטוש המודל |
| 3 | % מבצעים שנפרסים משדות מובנים | היקף מנוע המבצעים | — |
| 4 | התפלגות "בכמה רשתות מופיע כל ברקוד" | `CATALOG_MIN_CHAIN_COUNT` | הברך בגרף |

### ✅ קריטריוני קבלה
- [x] 13 שמות הסקרייפרים אומתו מול הספרייה — `scripts/phase0_verify_scrapers.py`
- [x] `docs/PHASE0-FINDINGS.md` קיים, עם ארבעת המספרים — `scripts/phase0_measure.py`
- [x] `03-DATA-MODEL.md` עודכן לפי מדידה #2 — 6.59% חריגות, ADR-002 מאומת
- [x] `CATALOG_MIN_CHAIN_COUNT` נקבע לפי מדידה #4 — 2 (היה 4)
- [x] מיפוי שדות XML per-chain תועד — `scripts/phase0_schema.py`

**פתוח לפני Phase 1:**
- [ ] Cerberus (רמי לוי, אושר עד) — 2 רשתות לא נקלטות. F-13
- [ ] מדידה משלימה: כמה מהמבצעים נופלים ל-4 הסוגים של v1. §0 מדידה #3
- [ ] מדוד מחדש את #4 בכיסוי מלא של 12 רשתות

---

## Phase 1 — Pipeline ⏱ ~3 שבועות

Scraper כ־worker על Caprover · R2 · normalizer · Postgres.

### ✅ קריטריוני קבלה
- [x] 13 הסקרייפרים רצים ב-Docker על Caprover, cron יומי ✚ שעתי — `Dockerfile`, `deploy/crontab`
- [x] קבצי גלם נשמרים ל-R2 בנתיב `raw/{chain}/{date}/{type}/{store}.xml.gz` — `ingestion/storage.py`
- [x] Normalizer עובד ב-stream (`lxml.iterparse`), UTF-16 ו-ZIP מטופלים — `ingestion/xmlstream.py`
- [~] `staging_items` מתמלא — **8 מתוך 12 רשתות**. Cerberus ו-laibcatalog פתוחות
- [x] `ingestion_runs` רושם הצלחה/כישלון לכל רשת, כולל `skipped_unstable`
- [x] כישלון ברשת אחת אינו מפיל את השאר — `ingestion/pipeline.py`
- [x] בדיקת בריאות: התראה כשנפח הקבצים יורד > 50% מהממוצע **של אותה רשת**

---

## Phase 2 — Catalog & Geo ⏱ ~3 שבועות

### ✅ קריטריוני קבלה
- [x] `normalize_barcode` עם unit tests (כולל דחיית `02`/`20`-`29`) — 14 טסטים
- [~] `parse_pack` — 30 טסטים מול שמות אמיתיים; **דורש הרחבה ל-50+ לכל רשת**
- [x] `canonical_products` נבנה מהחיתוך (`chain_count >= threshold`)
- [x] `normalized_unit_price` מחושב לכל `price_current`
- [ ] כל הסניפים גיאוקודדו, `geocode_confidence` נשמר — **עמודות קיימות, הריצה לא**
- [ ] סניפים מתחת לסף עברו אימות ידני
- [x] `price_base` / `price_exception` מתמלאים כ-SCD2 — 9 טסטים מול Postgres

---

## Phase 3 — 🚀 חיפוש מוצר בודד (השקה ציבורית) ⏱ 2–3 שבועות

**פיצ'ר ההשקה.** הכי פשוט, הכי קל להסביר, ה-wedge שמכניס משתמשים.

### ✅ קריטריוני קבלה
- [x] `GET /search?q=&lat=&lng=&radius_km=` מחזיר סניף/מרחק/זמן/מחיר/₪ליחידה/מבצע/עודכן
- [x] חיפוש טקסט עברי עם שגיאות כתיב — `word_similarity`, לא `similarity`
- [x] גרף מחיר 90 יום — `GET /products/{id}/history` ✚ "נמוך/גבוה מהרגיל"
- [x] מיון לפי ₪/יחידה כברירת מחדל
- [x] חותמת "עודכן לאחרונה" על כל מחיר — נאכף בסכמה
- [x] דיסקליימר "המחיר בקופה גובר" — נאכף בסכמה
- [ ] כפתור "הוסף לסל" — צד frontend
- [ ] Frontend עברית/RTL ב-Lovable

---

## Phase 4 — סל ✚ פיצול ✚ מבצעים בסיסיים ⏱ 4–5 שבועות

**הפיצ'ר המרכזי.** תלוי בכל מה שלפניו.

### ✅ קריטריוני קבלה
- [x] `POST /basket/optimize` מחזיר k=1 ו-k=2
- [x] brute force ✚ pruning — **אין solver בקוד**
- [x] שיפור מקומי לשיוך בתוך זוג — טסט מוודא שמבצע כמותי לא נשבר
- [x] מנוע מבצעים: מחיר קבוע, מין' כמות, אחוז, מועדון (toggle)
- [x] `skipped_count` בכל תוצאה — נאכף בסכמה
- [x] פריטים שלא נמצאו מוצגים מפורשות
- [x] בורר עלות נסיעה (זול ביותר / מאוזן / סניף אחד)
- [x] תגובה < 500ms לסל של 40 פריטים ו-25 סניפים — נמדד בטסט
- [x] מסגור: "מוצרים ארוזים של מותגים מובילים" — נאכף בסכמה
- [ ] 1+1 / 2+1 — `RewardType` לא ממופה; ראה PHASE0-FINDINGS §0 מדידה #3

---

## Phase 5 — התראות מחיר ⏱ 1–2 שבועות

- [ ] `price_alerts` ✚ job יומי
- [ ] התראה על ירידה מוחלטת ועל ירידה באחוזים
- [ ] "המחיר נמוך/גבוה מהממוצע ב-90 יום"

---

## Phase 6+ — v2

| | |
|---|---|
| מותג פרטי | מחלקות שקילות ✚ embeddings ✚ תור סקירה |
| פריטים שקילים | טקסונומיה ידנית ~400 קטגוריות ✚ ₪/ק"ג |
| מבצעים מורכבים | סף, חוצה־קטגוריות, LLM על טקסט חופשי |
| תמונות מוצר | Open Food Facts → GS1 Israel → UGC. 3,000 SKU מובילים בלבד |
| רגולציה | בירור מול הרשות להגנת הצרכן לקראת השקה |
