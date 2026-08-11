# PHASE 0 — Findings

> מסמך ממצאים חי. כל מדידה נכנסת לכאן עם התאריך והגרסה שבה נמדדה.
> קריטריוני הקבלה ב־[`05-ROADMAP.md`](05-ROADMAP.md) §Phase 0.

| | |
|---|---|
| תאריך | 2026-08-10 |
| `il-supermarket-scraper` | 1.0.8 (הגרסה האחרונה ב־PyPI) |
| כלי | [`scripts/phase0_verify_scrapers.py`](../scripts/phase0_verify_scrapers.py) |

---

## מצב המשימות

| # | משימה | מצב |
|---|---|---|
| 0.1 | אימות מיפוי הסקרייפרים | ✅ הושלם |
| 0.2 | הורדת קבצי סניפים | ⛔ חסום — ראה §5 |
| 0.3 | הורדת קובץ מחירים ומבצעים | ⛔ חסום — ראה §5 |
| 0.4 | פתיחת קובץ בעיניים | ⛔ חסום — תלוי ב־0.3 |
| 0.5 | ארבע המדידות | ⛔ חסום — תלוי ב־0.3 |

**ארבע המדידות טרם בוצעו.** הן דורשות נתונים אמיתיים. כל ההחלטות שתלויות
בהן — `base + exceptions` (ADR-002), `CATALOG_MIN_CHAIN_COUNT`, היקף מנוע
המבצעים — **נשארות פתוחות**.

---

## 1. ✅ 0.1 — מיפוי הסקרייפרים (הושלם)

13/13 השמות ב־`.env.example` נפתרים מול הספרייה **אחרי התיקון** ב־§2.
הטבלה כפי שאומתה בפועל:

| שם ב-`.env` | משפחת פורטל | נקודת קצה | `gov_chain_id` |
|---|---|---|---|
| `SHUFERSAL` | shufersal | `https://prices.shufersal.co.il/` | 7290027600007 |
| `RAMI_LEVY` | cerberus (FTP) | `url.retail.publishedprices.co.il` · user `RamiLevi` | 7290058140886 |
| `YAYNO_BITAN_AND_CARREFOUR` | publishprice | `https://prices.carrefour.co.il/` | 7290055700007 |
| `VICTORY_NEW_SOURCE` | laibcatalog_api | `https://laibcatalog.co.il` | 7290696200003, 7290058103393 |
| `MAHSANI_ASHUK_NEW_SOURCE` | laibcatalog_api | `https://laibcatalog.co.il` | 7290661400001, 7290633800006 |
| `OSHER_AD` | cerberus (FTP) | `url.retail.publishedprices.co.il` · user `osherad` | 7290103152017 |
| `MAAYAN_2000` | bina | `http://maayan2000.binaprojects.com/` | 7290058159628 |
| `NETIV_HASED` | web | `http://141.226.203.152/` | 7290058160839 |
| `SHEFA_BARCART_ASHEM` | bina | `http://shefabirkathashem.binaprojects.com/` | 7290058134977 |
| `ZOL_VEBEGADOL` | bina | `http://zolvebegadol.binaprojects.com/` | 7290058173198 |
| `SHUK_AHIR` | bina | `http://shuk-hayir.binaprojects.com/` | 7290058148776 |
| `MESHMAT_YOSEF_1` | web | `https://list-files.w5871031-kt.workers.dev/` | 5144744100002 |
| `MESHMAT_YOSEF_2` | bina | `http://ktshivuk.binaprojects.com/` | 5144744100001, 7290058289400, 2222222 |

לשחזור: `python scripts/phase0_verify_scrapers.py` (יוצא 0 כשהכל נפתר).

---

## 2. 🔴 ממצאים חוסמים

### F-1 — `VICTORY` ו-`MAHSANI_ASHUK` לא קיימים יותר בספרייה

שני השמות הוסרו מ-`ScraperFactory` ב-1.0.8. ההערות בקוד המקור:

```python
# VICTORY = all_scrappers.Victory  # old Matrix source; gov.il now lists new format only
# MAHSANI_ASHUK = all_scrappers.MahsaniAShuk  # כ.נ מחסני השוק בע"מ (moved to new source)
```

`ScraperFactory.get('VICTORY')` זורק `ValueError: class_names VICTORY not found`.

**זו לא הפעלה של fallback — זה החלפה.** `02-DATA-SOURCES.md` §3 מתאר את
`VICTORY_NEW_SOURCE` / `MAHSANI_ASHUK_NEW_SOURCE` כ"fallbacks" למקרה שהמקור
הראשי יפסיק לעבוד. זה כבר קרה: המקור הראשי נמחק, וה-`_NEW_SOURCE` הוא היום
**המקור היחיד**.

**תוקן** ב-`.env.example`. **מאשש את ADR-006** — כתובות ושמות מקור בקונפיג, לא בקוד.

### F-2 — `ENABLED_FILE_TYPES` לא כלל אף snapshot מלא

הערך היה `STORE_FILE,PRICE_FILE,PROMO_FILE`. ב-`FileTypesFilters`:

```python
PRICE_FILE      = {'should_contain': 'price',     'should_not_contain': 'full'}
PRICE_FULL_FILE = {'should_contain': 'pricefull', 'should_not_contain': None}
```

`PRICE_FILE` **ממעט במפורש** את קבצי ה-Full. הקונפיג הקודם היה מוריד דלתות
בלבד ולעולם לא snapshot — בסתירה לאסטרטגיית הקליטה ב-`02-DATA-SOURCES.md` §2
("`*Full` פעם ביום, דלתות כל שעה"). בלי snapshot אין ממה לבנות מצב התחלתי.

**תוקן** — נוספו `PRICE_FULL_FILE,PROMO_FULL_FILE`.

### F-3 — קטעי הקוד ב-Roadmap §0.2/§0.3 לא רצים

שניהם משתמשים ב-`enabled_file_types=`. החתימה בפועל:

```python
ScarpingTask(enabled_scrapers=None, files_types=[...], multiprocessing=5,
             min_size=None, max_size=None, output_configuration=None,
             status_configuration=None, timeout_in_seconds=1800)
```

`enabled_file_types` → `TypeError`. הפרמטר הוא `files_types`.
**תוקן** ב-`05-ROADMAP.md`.

---

## 3. 🟡 ממצאים תפעוליים

### F-4 — `MESHMAT_YOSEF_1/2` הפוכים ביחס לתיעוד

`02-DATA-SOURCES.md` §3 ביקש לאמת ש-`MESHMAT_YOSEF_1/2` מצביעים ל-
`ktshivuk.binaprojects.com` ו-`chp-kt.pages.dev`, בסדר הזה. בפועל:

- `MESHMAT_YOSEF_2` → `ktshivuk.binaprojects.com` (Bina) — הסדר הפוך
- `MESHMAT_YOSEF_1` → `https://list-files.w5871031-kt.workers.dev/` — **Cloudflare Worker**, לא `chp-kt.pages.dev`. הכתובת בתיעוד מיושנת.

### F-5 — `NETIV_HASED` מושבת אוטומטית; האתר מת

הספרייה מסמנת אותו כלא־יציב עד 2027-01-01, עם הנימוק בקוד:

> Netiv Hased site is down (HTTP 500 on `http://141.226.203.152/`).
> Evidence: upstream returns HTTP 500 as of 2026-07-24.

`ScraperFactory.is_scraper_enabled` מחזיר `False` עבורו, ולכן `ScarpingTask`
**ידלג עליו בשקט**. מבחינת הפייפליין זו הצלחה עם אפס קבצים, לא כישלון.

⚠️ שים לב גם: זו כתובת IP חשופה מעל **HTTP לא מוצפן**, לא דומיין ולא Bina —
בניגוד ל"Bina / Cerberus" שבתיעוד.

**השלכה:** קריטריון הקבלה של Phase 1 "התראה כשנפח הקבצים של רשת יורד > 50%"
חייב להתייחס גם לרשת שנעלמת בשקט, לא רק לירידה הדרגתית. `ingestion_runs`
צריך להבחין בין `ok` עם 0 קבצים לבין `skipped_unstable`.

### F-6 — `MESHMAT_YOSEF_1` לא מפרסם מבצעים

מסומן `DoNotPublishPromo`. אין לצפות לקבצי מבצעים ממנו — אל תתריע על זה.

### F-7 — `gov_chain_id` הוא לא יחיד

שלושה סקרייפרים מחזירים **רשימת** מזהי רשת:

| סקרייפר | מזהים |
|---|---|
| `VICTORY_NEW_SOURCE` | 7290696200003, 7290058103393 |
| `MAHSANI_ASHUK_NEW_SOURCE` | 7290661400001, 7290633800006 |
| `MESHMAT_YOSEF_2` | 5144744100001, 7290058289400, 2222222 |

הסכמה ב-`03-DATA-MODEL.md` מגדירה `chains.gov_chain_id TEXT` — יחיד.
**דורש החלטה:** `TEXT[]`, או טבלת `chain_gov_ids` נפרדת.
לא שיניתי את הסכמה — היא ממילא תלויה במדידה #2 שטרם בוצעה.

> `2222222` אצל `MESHMAT_YOSEF_2` נראה כמו מזהה מציין־מקום. שווה בדיקה
> כשיהיו נתונים.

### F-8 — באג בספרייה: אי אפשר ליצור סקרייפר עם ארגומנטים ברירת מחדל

`engines/engine.py:117`:

```python
file_output = DiskFileOutput(storage_path=DumpFolderNames[chain].value)
```

`chain` הוא כבר איבר `DumpFolderNames`, ו-`DumpFolderNames[...]` מצפה למחרוזת
שם. התוצאה: `KeyError` בכל instantiation ישיר. (השימוש התקין נמצא ב-
`utils/databases/__init__.py:19`, שם מועברת מחרוזת.)

**עקיפה:** תמיד לעבוד דרך `ScarpingTask` — הוא מזריק `output_configuration`
משלו — או להעביר `file_output` מפורש. **אל תיצור מחלקות סקרייפר ישירות.**

### F-9 — משפחת פורטלים חמישית

`02-DATA-SOURCES.md` §4 מונה ארבע משפחות. בפועל יש חמש, ו-`laibcatalog`
מכסה שתי רשתות:

| משפחה | רשתות | מנוע |
|---|---|---|
| cerberus (FTP) | רמי לוי, אושר עד | `Cerberus` |
| shufersal | שופרסל | `MultiPageWeb` |
| bina | מעיין 2000, שפע ברכת השם, זול ובגדול, שוק העיר, משנת יוסף 2 | `Bina(Aspx)` |
| **laibcatalog_api** | **ויקטורי, מחסני השוק** | `_LaibcatalogApiScraper(ApiWebEngine)` |
| publishprice | קרפור / יינות ביתן / מגה בעיר | `PublishPrice` |
| web | נתיב החסד, משנת יוסף 1 | `WebBase` |

שתי הערות דיוק לתיעוד:
- **Cerberus הוא FTP, לא HTTP.** `ftp_host=url.retail.publishedprices.co.il`,
  שם משתמש מקודד בספרייה (`RamiLevi`, `osherad`), סיסמה ריקה.
  ⇒ `chains.credentials_ref` כנראה מיותר לרשתות האלה. אל תשכפל סוד שאין.
- **Bina רץ מעל `http://` לא מוצפן**, והדף הוא `MainIO_Hok.aspx`
  (התיעוד אמר `Main.aspx`).

### F-12 — 🔴 `start()` לא חוסם — בלי `join()` לא יורד כלום

`ScarpingTask.start()` יוצר **daemon thread** וחוזר מיד:

```python
self._thread = threading.Thread(target=_run, daemon=True)
self._thread.start()
return self._thread
```

בסקריפט שמסתיים אחרי `start()`, התהליך יוצא, וה-thread — שהוא daemon — נהרג
באמצע. **התוצאה: אפס קבצים, בלי שום שגיאה.** הסימן המזהה הוא סדר הפלט:
הודעת הסיום של הסקריפט מודפסת *לפני* `Start scraping` של הספרייה.

```python
task = ScarpingTask(...)
task.start()
task.join()      # ← חובה
```

זה מסוכן במיוחד כי מצב הכישלון זהה לחלוטין למצב של חסימה גיאוגרפית — שניהם
"רץ בלי שגיאות, אין קבצים". מי שלא יודע יסיק שהוא חסום ויעבור לשרת.

**תוקן** ב-`phase0_download.py` וב-`05-ROADMAP.md`.

### F-11 — 🔴 הספרייה לא רצה על Windows

`utils/file_cache.py` שורה 1:

```python
import fcntl
```

בלי `try`, בלי `platform` check. `fcntl` הוא מודול POSIX בלבד בספריית התקן —
**לא קיים ב-Windows ולא ניתן להתקנה מ-pip.** הוא משמש לנעילת `flock` סביב
קובץ cache של JSON.

זה הייבוא היחיד מסוגו בכל החבילה, אבל הוא יושב בשרשרת הייבוא הראשית:

```
il_supermarket_scarper → main → scrapper_runner → scrappers_factory
  → scrappers → bareket → engines → cerberus → utils → status
  → connection → file_cache → fcntl   💥
```

⇒ `import il_supermarket_scarper` נכשל ב-`ModuleNotFoundError` על Windows,
עוד לפני שנגעת בסקרייפר כלשהו.

**השלכה:** כל פיתוח של שכבת ה-ingestion חייב לרוץ על Linux או macOS.
על Windows — WSL2 או קונטיינר. **production לא מושפע** (Docker על Caprover).
**ADR-001 עומד בעינו** — זו מגבלת סביבת פיתוח, לא סיבה לכתוב סקרייפרים מאפס.

הוסף `wsl` להוראות ההתקנה ב-`README.md` לפני שמצרפים מפתח נוסף.

### F-10 — קונסולידציה של רשתות בשוק

בקוד הספרייה, מתוארך 04.08.2026 — שישה ימים לפני המדידה:

```python
# COFIX = all_scrappers.Cofix  # gov.il 04.08.2026 folded into Rami Levy
# QUIK  = all_scrappers.Quik   # gov.il 04.08.2026 dropped dedicated link (under Rami Levy)
```

קופיקס וקוויק אינם ברשימת 12 שלנו, אז אין השלכה מיידית. הנקודה היא הקצב:
**רשתות משתנות ברמת ה־gov.il תוך שבועות.** אימות השמות חייב להיות בדיקה
חוזרת, לא אירוע חד־פעמי. הרץ את `phase0_verify_scrapers.py` בכל שדרוג ספרייה.

עוד שתי הערות מיזוג מהקוד: `SHUFERSAL` כולל את **רשת BE**,
ו-`NETIV_HASED` כולל את **ברכל** — שתיהן רלוונטיות ל-`price_groups`.

---

## 4. השלכות פתוחות

| # | נושא | ממצא | סטטוס |
|---|---|---|---|
| 1 | `chains.gov_chain_id` יחיד מול רשימה | F-7 | ⏳ דורש החלטת סכמה |
| 2 | `ingestion_runs` צריך `skipped_unstable` | F-5 | ⏳ לפני Phase 1 |
| 3 | `chains.credentials_ref` אולי מיותר | F-9 | ⏳ לאמת מול Cerberus חי |
| 4 | ארבע המדידות | — | ⛔ חסום |

---

## 5. למה 0.2–0.5 חסומים

הסביבה שבה רצה הבדיקה חוסמת יציאה לכל מארח שאינו ברשימת ההיתר
(`403` על `CONNECT` מה-proxy). נבדק ונדחה: `prices.shufersal.co.il`,
`url.retail.publishedprices.co.il`, `maayan2000.binaprojects.com`.
PyPI פתוח — ולכן 0.1 בוצע במלואו מתוך קוד הספרייה עצמו.

זה **בנוסף** לחסימה הגיאוגרפית המתוארת ב-`README.md`: חלק מהאתרים חוסמים
גישה מחוץ לישראל.

**כדי להשלים את Phase 0 צריך להריץ את 0.2–0.5 ממכונה עם IP ישראלי
וגישת רשת חופשית.** שם ייקבעו ארבעת המספרים, ואיתם ADR-002 ו-
`CATALOG_MIN_CHAIN_COUNT`.
