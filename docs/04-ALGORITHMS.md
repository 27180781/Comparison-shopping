# 04 — Algorithms

---

## 1. נירמול ברקוד

```python
INTERNAL_PREFIXES = {'02'} | {str(n) for n in range(20, 30)}

def normalize_barcode(raw: str) -> str | None:
    """מחזיר EAN-13 מנורמל, או None אם לא ברקוד ציבורי תקין."""
    if not raw:
        return None
    code = ''.join(ch for ch in raw if ch.isdigit())

    if len(code) == 12:              # UPC-A → EAN-13
        code = '0' + code
    elif len(code) in (11, 13):
        code = code.zfill(13)        # אפסים מובילים שנחתכו ב-export
    elif len(code) not in (8, 13):
        return None

    if code[:2] in INTERNAL_PREFIXES:
        return None                  # קוד פנימי — לא ייחודי גלובלית
    if not _valid_check_digit(code):
        return None
    return code


def _valid_check_digit(code: str) -> bool:
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    # EAN-13: משקל 1/3 מימין; EAN-8: 3/1
    weights = ([1, 3] * 7)[:len(body)] if len(code) == 13 else ([3, 1] * 4)[:len(body)]
    total = sum(d * w for d, w in zip(reversed(body), [3, 1] * 7))
    return (10 - total % 10) % 10 == check
```

⚠️ `_valid_check_digit` לעיל דורש בדיקה מול ברקודים אמיתיים ב-Phase 0.
כתוב לו unit tests עם ברקודים ידועים לפני שסומכים עליו.

**קריטי:** ברקודים בקידומת `02` ו־`20`–`29` הם קודי שימוש פנימי של קמעונאים
(שקילים, דלפק, מותג פרטי). הם **אינם ייחודיים גלובלית** — join עליהם ייצור
התאמות שגויות בין רשתות.
`729` = ישראל, תקין וגלובלי.

---

## 2. פירוק גודל אריזה — קריטי

רשתות דיסקאונט מוכרות במארזים. **לכל גודל אריזה ברקוד שונה** — join על
ברקוד יראה "קוטג' 250 גר" ו"מארז 4 × קוטג' 250 גר" כשני מוצרים לא קשורים.

בלי נרמול, ההשוואה מפספסת בדיוק במקום שבו רשתות הדיסקאונט מנצחות.

```python
import re

PACK_RE = re.compile(r'(?:מארז(?:\s+של)?|מבצע)?\s*(\d+)\s*(?:[xX*×]|יח[\'׳"]?)')
SIZE_RE = re.compile(
    r'(\d+(?:[.,]\d+)?)\s*'
    r'(ק["\']?ג|קילו|גרם|גר[\'׳]?|ג[\'׳]|ליטר|ל[\'׳]|מ["\']?ל|מל)'
)

TO_BASE = {          # → גרם או מ"ל
    'ק"ג': 1000, "ק'ג": 1000, 'קילו': 1000,
    'גרם': 1, 'גר': 1, "גר'": 1, "ג'": 1,
    'ליטר': 1000, "ל'": 1000,
    'מ"ל': 1, "מ'ל": 1, 'מל': 1,
}

def parse_pack(name_he: str) -> dict:
    """מחלץ pack_count, unit_size, unit_of_measure משם מוצר בעברית."""
    pack = 1
    if m := PACK_RE.search(name_he):
        pack = int(m.group(1))

    size, uom = None, 'unit'
    if m := SIZE_RE.search(name_he):
        size = float(m.group(1).replace(',', '.'))
        unit_raw = m.group(2)
        size *= TO_BASE.get(unit_raw, 1)
        uom = 'ml' if unit_raw in ('ליטר', "ל'", 'מ"ל', "מ'ל", 'מל') else 'g'

    return {'pack_count': pack, 'unit_size': size, 'unit_of_measure': uom}


def normalized_unit_price(price, pack_count, unit_size, uom) -> float | None:
    """₪ ל-100 גרם / 100 מ"ל / יחידה."""
    if uom == 'unit' or not unit_size:
        return float(price) / pack_count if pack_count else None
    return float(price) / (pack_count * unit_size) * 100
```

הביטויים הרגולריים לעיל הם **נקודת פתיחה**. חייבים כיול מול שמות אמיתיים
ב-Phase 0. כתוב טסטים עם 50+ שמות מוצרים אמיתיים מכל רשת.

זהו גם פיצ'ר מוצר בפני עצמו: *"האריזה הגדולה יקרה ב־12% ליחידה."*

---

## 3. אופטימיזציית סל

### 3.1 הניסוח

בהינתן סל `B = {(canonical_id, qty)}`, מיקום `L`, רדיוס `R`:

```sql
SELECT * FROM stores
WHERE is_active AND ST_DWithin(geom, :location, :radius_meters);
-- טיפוסית 10–25 סניפים
```

מזער: `Total = Σ effective_price(item, store) + TravelPenalty(k)`

### 3.2 🚫 אל תבנה solver

הבעיה NP-hard באופן כללי, **אבל המופע המעשי זעיר**:

```
k=1:  לכל סניף — עלות סל מלא          O(|S| × |B|)  ≈ 25 × 40  = 1,000
k=2:  כל זוג מתוך top-10 של k=1        O(10² × |B|)  ≈ 100 × 40 = 4,000
k=3:  רק אם החיסכון ב-k=2 משמעותי      O(10³ × |B|)  ≈ 40,000
```

הכל רץ במילישניות. **אין שום צורך ב־ILP, PuLP, OR-Tools.**

### 3.3 שיוך בתוך זוג — כאן זה מעניין

מבצעים שוברים את העצמאות בין פריטים (1+1 תלוי בכמות **באותו סניף**),
ולכן אי אפשר פשוט לשייך כל פריט לסניף הזול יותר.

```python
def assign_pair(basket, store_a, store_b, price_fn, promo_fn):
    # 1. שיוך חמדני
    assign = {
        item: (store_a if price_fn(item, store_a) <= price_fn(item, store_b)
               else store_b)
        for item in basket
    }

    # 2. שיפור מקומי עד התכנסות (טיפוסית 2–4 סבבים)
    improved = True
    while improved:
        improved = False
        base_cost = total_with_promos(assign, basket, promo_fn)
        for item in basket:
            other = store_b if assign[item] is store_a else store_a
            trial = {**assign, item: other}
            if total_with_promos(trial, basket, promo_fn) < base_cost:
                assign, base_cost, improved = trial, \
                    total_with_promos(trial, basket, promo_fn), True
    return assign
```

מגיע קרוב מאוד לאופטימום בעלות זניחה.

### 3.4 עלות נסיעה — בורר למשתמש, לא מספר קסם

| מצב | קנס |
|---|---|
| "הזול ביותר" | 0 |
| "מאוזן" (ברירת מחדל) | `TRAVEL_PENALTY_PER_STOP_ILS` ✚ זמן × `TRAVEL_TIME_VALUE_ILS_PER_HOUR` |
| "סניף אחד" | k=1 בלבד |

**הצג תמיד את הפער:** *"פיצול בין 2 סניפים חוסך ₪34 ומוסיף 12 דקות נסיעה."*
המשתמש מחליט, לא המערכת.

### 3.5 זמינות

פריט שאינו קיים בסניף אינו בר־שיוך אליו. אם אף שילוב לא מכסה את כל הסל —
הצג את הטוב ביותר ✚ **רשימת "לא נמצא" מפורשת**.

---

## 4. מנוע מבצעים — v1

### 4.1 היקף

| סוג | v1 | הערה |
|---|---|---|
| מחיר מבצע קבוע לפריט | ✅ | `discounted_price` |
| מינימום כמות (2 ב־₪10) | ✅ | `min_qty` ✚ `discounted_price` |
| 1+1 / 2+1 | ✅ | `reward_type` ✚ `min_qty` |
| מבצע מועדון | ✅ toggle בממשק | `club_id IS NOT NULL` |
| מבצע סף ("מעל ₪200") | ⏳ v2 | לוגיקה ברמת סל |
| חוצה־קטגוריות | ⏳ v2 | |
| טקסט חופשי בלבד | ⏳ v2 | LLM offline |

### 4.2 מבנה ה-evaluator

```python
def apply_promotions(basket_lines, promotions):
    """
    basket_lines: [(variant_id, qty, unit_price)]
    מחזיר (total, applied[], skipped_count)

    חובה: לא לסכם מחירי פריטים ואז להוריד הנחות.
    כל מבצע מוערך מול הכמות בפועל של הפריטים שהוא חל עליהם.
    """
```

### 4.3 כלל UI מחייב

בכל תוצאה:
> *"החישוב כולל 4 מבצעים. 3 מבצעים נוספים בסניף זה לא נכללו בחישוב."*

`skipped_count` מגיע מ־`promotions.parse_status != 'structured'`.
**מספר שמרני עם שקיפות עדיף על מספר שגוי בביטחון.**

---

## 5. גיאו

- קובץ ה-Stores נותן כתובת וישוב; קואורדינטות חסרות או לא אמינות
- Google Geocoding על כל הסניפים — חד־פעמי, ~900 קריאות, זניח
- שמור `geocode_confidence`; מתחת לסף → תור אימות ידני
- **זמן נסיעה, לא מרחק אווירי.** בגוש דן ההבדל דרמטי.
- Distance Matrix עם cache: מיקום משתמש מעוגל ל-500 מ' = מפתח cache, TTL 30 יום

---

## 6. התאמת מוצרים — v2 (מותג פרטי ושקילים)

לא ב-v1, אבל התכנון קיים כדי שהסכמה תתמוך:

- **מסלול ב׳ — נירמול טקסט:** הסרת ניקוד/גרשיים → חילוץ מותג, נפח, אחוז →
  signature `{brand}|{category}|{base_size}` → התאמה מדויקת
- **מסלול ג׳ — embeddings:** דמיון > `MATCH_CONFIDENCE_AUTO` (0.92) → אוטומטי;
  `MATCH_CONFIDENCE_REVIEW`–0.92 → תור סקירה; מתחת → אין התאמה
- **שקילים:** טקסונומיה ידנית של ~400 קטגוריות טריות ✚ השוואת ₪/ק"ג

**כלל על:** מוצר שלא הותאם בביטחון גבוה **לא נכנס לחישוב בשקט**.
עדיף "לא נמצא ב-X" מהשוואה של קוטג' לגבינה לבנה.
