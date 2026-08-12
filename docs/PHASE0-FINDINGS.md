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
| 0.2 | הורדת קבצי סניפים | ✅ הושלם |
| 0.3 | הורדת קובץ מחירים ומבצעים | ✅ הושלם |
| 0.4 | פתיחת קובץ בעיניים | ✅ הושלם — §6 |
| 0.5 | ארבע המדידות | ✅ הושלם — §0 |

**בסיס הנתונים למדידות:** 12 קובצי מחירים ו-12 קובצי מבצעים מ-5 רשתות
(מעיין 2000, שופרסל, זול ובגדול, שוק העיר, שפע ברכת השם), 71MB, 2026-08-11.
שתי רשתות Cerberus — רמי לוי ואושר עד — לא נכללו, ראה F-13.

---

## 0. ארבע המדידות

### מדידה #1 — ברקודים ציבוריים תקינים ✅ 97.86%

סף ההחלטה: **< 50% → לשקול מחדש**. עברנו אותו בגדול.

| | |
|---|---|
| פריטים שנסרקו | 41,883 |
| ברקוד תקין | 40,987 — **97.86%** |
| `ItemType = 1` | 41,880 — 99.99% |
| מסומן `1` אך ברקוד פסול | 893 — 2.13% מהמסומנים |

| רשת | פריטים | תקינים |
|---|---|---|
| שופרסל | 19,044 | 99.97% |
| זול ובגדול | 7,765 | 97.0% |
| מעיין 2000 | 6,106 | 96.54% |
| שפע ברכת השם | 6,235 | 95.78% |
| שוק העיר | 2,733 | 93.3% |

⚠️ **`ItemType` חסר ערך כמסנן.** הוא `1` על 99.99% מהפריטים — כולל קודים
פנימיים חד־ספרתיים כמו `ItemCode=5`. הוא לא מבדיל בין כלום.
**המסנן הוא `normalize_barcode`**, לא הדגל. ADR-004 שריר אבל הניסוח שלו
("רק `ItemType=1`") צריך לומר "רק ברקוד שעובר אימות ספרת ביקורת".

**מסקנה:** הקטלוג נקי בהרבה ממה שהאפיון חשש. v1 על ברקודים ציבוריים בר־ביצוע.

### מדידה #2 — סטיית מחיר בין סניפים ✅ 6.59%

סף ההחלטה: **> 15% חריגות → ADR-002 נופל**. לא נפל.

| | |
|---|---|
| מחירי סניף בני־השוואה | 26,780 |
| סוטים מהמחיר השכיח | 1,766 — **6.59%** |
| גודל סטייה חציוני | 6.25% |

הסטייה נמדדת מול המחיר השכיח בתוך `(רשת, תת־רשת, ברקוד)`.

**מסקנה: ADR-002 מאומת.** `base + exceptions` משתלם — פחות מ-7% מהשורות
צריכות רשומת חריג. אפשר לבנות את הסכמה כמתוכנן.

### מדידה #3 — מבצעים משדות מובנים ⚠️ 99.72%, עם סייג

| | |
|---|---|
| מבצעים שנסרקו | 12,149 |
| כל השדות המובנים קיימים | 12,115 — **99.72%** |
| חלקי | 34 (חסר סכום הנחה) |
| טקסט חופשי בלבד | **0** |

⚠️ **אל תקרא את זה כ"99.72% מהמבצעים ניתנים לחישוב ב-v1".**
המדידה בודקת שהשדות **מאוכלסים** — סכום, כמות מינימלית ורשימת פריטים —
ולא שהסמנטיקה שלהם ניתנת למימוש. מבצע כמו
*"קנה גלידות ב-99 ₪ וקבל צידנית במתנה"* (שופרסל, `MinPurchaseAmount=99`,
`RewardType=10`) מאוכלס במלואו אבל הוא **מבצע סף ✚ מתנה** — לוגיקה שהאפיון
דוחה ל-v2 במפורש.

**מה שכן נקבע:** `PromotionDescription` אינו הכרחי לפרסור. השדות המובנים
קיימים כמעט תמיד. זה משפר משמעותית את התחזית של ADR-008, אבל **המספר שצריך
למדוד הוא כמה מהמבצעים נופלים לארבעת הסוגים של v1** — מחיר קבוע, מין' כמות,
1+1, מועדון. זו מדידה נפרדת שטרם בוצעה.

### מדידה #4 — בכמה רשתות מופיע כל ברקוד

15,023 ברקודים ייחודיים על פני 5 רשתות:

| מופיע ב־ | ברקודים | אחוז | מצטבר (≥) |
|---|---|---|---|
| רשת 1 | 9,418 | 62.69% | 100% |
| 2 רשתות | 2,702 | 17.99% | **37.31%** |
| 3 רשתות | 1,684 | 11.21% | 19.32% |
| 4 רשתות | 810 | 5.39% | 8.11% |
| 5 רשתות | 409 | 2.72% | 2.72% |

**הברך היא בין 1 ל-2, והיא לא סתם ברך — היא גבול בין שתי אוכלוסיות.**
הצניחה מ-62.69% ל-17.99% היא בדיוק ההבחנה מ-`02-DATA-SOURCES.md` §7:
מוצר שקיים ברשת אחת בלבד הוא מותג פרטי או פריט ייחודי, ו**אין לו מקבילה
להשוות אליה מעצם הגדרתו**. כל מה שב-2+ הוא בר־השוואה.

**המלצה: `CATALOG_MIN_CHAIN_COUNT=2`** (5,605 מוצרים).
הערך שהיה בקונפיג, `4`, משאיר 1,219 מוצרים בלבד — 8% מהקטלוג — ומשליך
מותגים לאומיים רק משום שלא הופיעו בסניפים שנדגמו.

⚠️ **המספר הזה יעלה בכיסוי מלא.** 5 רשתות מתוך 12, ו-2–3 סניפים לרשת.
**מדוד מחדש אחרי קליטה מלאה** ואז קבע סופית.

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

### F-14 — ✅ נסגר: laibcatalog תקין. הכשל היה סביבתי

**המסקנה המקורית הייתה שגויה.** 2026-08-12, ממכונה אחרת, שתי הרשתות
הורידו בהצלחה:

```
VictoryNewSource        PriceFull7290696200003-001-001-...  8.6 MB
MahsaniAShukNewSource   PriceFull7290661400001-002-250-...  6.6 MB
```

`phase0_check_laibcatalog.py` מראה API בריא לחלוטין: 70 ו-71 סניפים,
1,520 ו-934 קבצים, וכל ערכי `fileType` תואמים לאוצר המילים של הספרייה.
גם כתובת ההורדה תקינה — HTTP 200 ו-gzip.

**מה באמת קרה:** ההרצה שנכשלה הייתה חמש רשתות במקביל על חיבור שקרס
באמצע הורדות (`0x80072eff`) ורץ ב-80–300 kB/s. במכונה עם חיבור תקין —
הכול עובד. **ייחסתי לפורטלים מה שהיה תקלת רשת.**

**הלקח, והוא הסיבה שהממצא נשאר במסמך:** "אפס קבצים בלי שגיאה" הוא סימפטום
משותף לכשל רשת, לחסימה גיאוגרפית, ולבאג בספרייה. אי אפשר להסיק ממנו סיבה.
`phase0_check_laibcatalog.py` נשאר ברפו בדיוק בשביל להפריד ביניהם בפעם הבאה.

**ממצא צדדי שכן עומד:** ה-edi השני של כל אחת מהרשתות —
`7290058103393` ו-`7290633800006` — מחזיר אפס סניפים. הספרייה מדלגת עליו
ועוברת לראשון, אז אין נזק, אבל אלו שתי קריאות API מיותרות בכל ריצה.
**לא הוסר בקוד** — לפי ADR-006 זה `UPDATE` על `chains.gov_chain_ids`,
לא deploy:

```sql
UPDATE chains SET gov_chain_ids = ARRAY['7290696200003']
 WHERE scraper_name = 'VICTORY_NEW_SOURCE';
UPDATE chains SET gov_chain_ids = ARRAY['7290661400001']
 WHERE scraper_name = 'MAHSANI_ASHUK_NEW_SOURCE';
```

<details>
<summary>האבחון המקורי, לתיעוד</summary>

### F-14 (מקורי) — laibcatalog: ויקטורי ומחסני השוק מחזירות אפס

שתי הרשתות על `https://laibcatalog.co.il` מחזירות אפס קבצים **בלי שגיאה**.
בקריאת `scrappers/victory.py` ו-`engines/api_web.py` יש שלוש נקודות שבהן זה
קורה בשקט, וכולן נראות זהות מבחוץ:

| # | מה קורה | איפה |
|---|---|---|
| 1 | `getbranches` מחזיר ריק ⇒ `continue`. `getfiles` לא נקרא בכלל | `get_request_url` |
| 2 | `get_api_data` תופס כל `RequestException`, רושם ומחזיר `[]` | `api_web.py` |
| 3 | `apply_filter_by_type` שומר רק `fileType` מאוצר מילים קשיח | `victory.py` |

**נקודה 3 היא החשודה המעניינת:** הספרייה משווה `entry["fileType"].lower()`
מול `{price, pricefull, promo, promofull, store, stores, storefull}`.
כל איות אחר — `price_full`, `branches` — נזרק **אחרי** שתי קריאות מוצלחות.

**אבחון:** `python scripts/phase0_check_laibcatalog.py`
הסקריפט מבצע את אותן קריאות באותו סדר, בלי לעבור דרך הספרייה, ומדפיס מה כל
אחת מחזירה. הוא גם משווה את ערכי `fileType` בפועל מול אוצר המילים.

**חשד רביעי, בקוד:** כתובת ההורדה נבנית מ-`primary_chain_id` — ה-edi הראשון
ברשימה. לוויקטורי ולמחסני השוק יש שניים, ולכן קבצים של ה-edi השני מקבלים
נתיב שגוי. ראה `--download` בסקריפט.

**מצב:** הורץ. כל ההשערות הופרכו — ראה למעלה.

</details>

### F-13 — ✅ נסגר: Cerberus תקין. השרת דווקא כן תומך ב-TLS

**המסקנה המקורית הייתה שגויה, והעקיפה שנכתבה בעקבותיה הוסרה.**

`phase0_check_cerberus.py` ממכונה תקינה, 2026-08-12:

```
3. FEAT      AUTH mechanisms advertised: ['AUTH SSL', 'AUTH TLS']
5. AUTH TLS  234 Authentication method accepted
6. FTP_TLS   AUTH TLS accepted and login succeeded — 2249 entries
```

רמי לוי ואושר עד הורידו בהצלחה (11.3MB ו-5.9MB), כולל עם `--no-ftp-fallback`.
**ה-504 לא הגיע מהשרת** אלא מ-middlebox ברשת של המכונה הקודמת — כנראה FTP ALG
בראוטר, שמשבש בדיוק את שדרוג ה-TLS.

**למה העקיפה הוסרה ולא הושארה "ליתר ביטחון":** שלב 4 מראה ש-**FTP רגיל נכשל**
(`Errno 113` בערוץ הנתונים) בזמן ש-FTPS עובד. כלומר הנפילה חזרה לא רק מורידה
את ההתחברות לטקסט גלוי — היא נופלת למסלול שלא עובד. בכל תקלה חולפת ב-`AUTH`
היא הייתה הופכת בעיה זמנית לכשל קשה, ומדליפה שם משתמש בדרך. עקיפה שגם מחלישה
אבטחה וגם לא פותרת כלום היא גרועה מכלום.

`scripts/phase0_check_cerberus.py` נשאר — הוא מה שהכריע כאן.

<details>
<summary>האבחון המקורי, לתיעוד</summary>

### F-13 (מקורי) — משפחת Cerberus כולה לא נגישה

**שתי רשתות Cerberus, שתיהן אפס קבצים:** רמי לוי ואושר עד. זו לא תקלה
נקודתית אלא כשל של משפחת הפורטלים.

השגיאה: `504 Command not implemented for that parameter`.
`scripts/phase0_check_cerberus.py` מפרק את זה:

```
1. חיבור FTP רגיל       ✅  220 Welcome to Public Published Prices Server (NCR L.T.D)
2. התחברות רגילה         ✅  230 Password Ok, User logged in
3. FTP_TLS (כמו הספרייה) ❌  504
```

**FTP רגיל עם `RamiLevi` וסיסמה ריקה עובד מצוין.** רק ה-TLS נדחה.
הספרייה משתמשת ב-`ftplib.FTP_TLS`, שהבנאי שלו מתחבר ומבצע `AUTH TLS` —
וזה מה שנדחה.

**השלכה:** 2 מתוך 12 הרשתות לא נקלטות, כולל רמי לוי שהיא מהגדולות. זה משפיע
ישירות על מדידה #4 ועל כיסוי רגולטורי (`01-SPEC.md` §8).

**נותר לברר:** האם השרת לא תומך ב-AUTH TLS בכלל, או שמשהו בדרך משבש —
`FEAT` דורש התחברות מוקדמת אצל השרת הזה, אז הבדיקה צריכה לרוץ שוב עם הסדר
המתוקן. אם השרת אכן plain-FTP-only, זה באג בספרייה ויש לפתוח issue.

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

## 5. סביבת ההרצה

המדידות רצו ממכונה בישראל תחת **WSL2 / Ubuntu**, עם Python 3.13 שהותקן
דרך `uv` (ראה F-11 ו-`README.md`). ההורדות עברו ללא חסימה גיאוגרפית —
`bina`, `shufersal` ו-`publishprice` כולם נגישים. רק Cerberus נכשל, ומסיבה
שאינה גיאוגרפית (F-13).

0.1 בוצע מראש בסביבה ללא גישה לפורטלים, מתוך קוד הספרייה בלבד.
