/* No framework and no build step, so this ships with the API and stays honest
   about what it shows. Every price carries its update stamp, every basket
   result carries the count of promotions that could not be applied, and a
   product no store stocks is named rather than dropped (ADR-010). */

const API = "";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// Prices are laid out left-to-right inside a right-to-left page. Without the
// isolate the shekel sign and digits get reordered by the bidi algorithm.
const money = (value) =>
  value == null ? "—" : `₪${Number(value).toLocaleString("he-IL", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const num = (value, digits = 0) =>
  value == null ? "—" : Number(value).toLocaleString("he-IL", { maximumFractionDigits: digits });

const ltr = (text) => `<span dir="ltr">${text}</span>`;

function ago(iso) {
  if (!iso) return "";
  const minutes = Math.round((Date.now() - new Date(iso)) / 60000);
  if (minutes < 60) return `לפני ${minutes} דק׳`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `לפני ${hours} שע׳`;
  const days = Math.round(hours / 24);
  return days === 1 ? "אתמול" : `לפני ${days} ימים`;
}

function packLabel(product) {
  const parts = [];
  if (product.pack_count > 1) parts.push(`מארז ${product.pack_count}`);
  if (product.unit_size) {
    const unit = { g: "גרם", ml: 'מ"ל' }[product.unit_of_measure] || "";
    parts.push(`${num(product.unit_size)} ${unit}`.trim());
  }
  parts.push(`ב־${product.chain_count} רשתות`);
  return parts.join(" · ");
}

function unitLabel(measure) {
  return { g: "ל־100 גרם", ml: 'ל־100 מ"ל' }[measure] || "ליחידה";
}

async function api(path, options) {
  const response = await fetch(API + path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} — ${body.slice(0, 200)}`);
  }
  return response.json();
}

function setStatus(el, message, isError = false) {
  el.hidden = !message;
  el.textContent = message || "";
  el.classList.toggle("is-error", Boolean(isError));
}

/* ─── navigation ──────────────────────────────────────────── */

function showView(name) {
  $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("is-active", view.dataset.view === name));
  if (name === "coverage") loadCoverage();
  if (name === "basket") renderBasket();
  if (name === "catalog") loadCatalog();
}

$$(".tab").forEach((tab) => tab.addEventListener("click", () => showView(tab.dataset.view)));
document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-goto]");
  if (target) showView(target.dataset.goto);
});

/* ─── basket state ────────────────────────────────────────── */

const STORAGE_KEY = "basket.v1";
let basket = [];

try {
  basket = JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
} catch {
  basket = [];
}

function saveBasket() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(basket));
  const count = basket.reduce((sum, item) => sum + item.qty, 0);
  const pill = $("#basket-count");
  pill.hidden = count === 0;
  pill.textContent = count;
}

function inBasket(id) {
  return basket.some((item) => item.canonical_id === id);
}

function addToBasket(product) {
  const existing = basket.find((item) => item.canonical_id === product.canonical_id);
  if (existing) existing.qty += 1;
  else basket.push({ canonical_id: product.canonical_id, name: product.name_he, qty: 1 });
  saveBasket();
}

/* ─── shared card behaviour ───────────────────────────────── */

/* Both card types show the same three things -- which stores, what the price
   did over 90 days, and a way into the basket -- so the wiring lives once.
   The catalogue does not carry per-store prices in its payload, which is why
   the store list arrives through a callback rather than a value. */
function wireCardActions(card, product, { storeCount, loadPrices }) {
  const detail = $('[data-f="detail"]', card);
  const storesBtn = $('[data-act="stores"]', card);

  storesBtn.textContent = storeCount ? `${num(storeCount)} סניפים` : "אין מחיר זמין";
  storesBtn.disabled = !storeCount;

  const toggle = (mode, render) => async () => {
    const open = !detail.hidden && detail.dataset.mode === mode;
    detail.hidden = open;
    detail.dataset.mode = mode;
    // A six-column price table cannot live inside a 310px grid column, so an
    // open catalogue card takes the full row and its neighbours move down.
    // One at a time, or the grid becomes a stack of tables.
    if (!open) {
      $$(".pcard.is-open").forEach((other) => {
        if (other === card) return;
        other.classList.remove("is-open");
        $('[data-f="detail"]', other).hidden = true;
      });
    }
    card.classList.toggle("is-open", !open);
    if (open) return;
    detail.innerHTML = "<p class='hint'>טוען…</p>";
    try {
      detail.innerHTML = await render();
    } catch (error) {
      detail.innerHTML = `<p class="hint">לא ניתן לטעון: ${error.message}</p>`;
    }
  };

  storesBtn.addEventListener("click", toggle("stores", async () => storeTable(await loadPrices())));
  $('[data-act="history"]', card).addEventListener(
    "click",
    toggle("history", async () =>
      historyPanel(await api(`/products/${product.canonical_id}/history?days=90`))
    )
  );

  const addBtn = $('[data-act="add"]', card);
  const markAdded = () => {
    addBtn.textContent = "בסל ✓";
    addBtn.classList.add("is-added");
  };
  if (inBasket(product.canonical_id)) markAdded();
  addBtn.addEventListener("click", () => {
    addToBasket(product);
    markAdded();
  });
}

/* ─── catalog ─────────────────────────────────────────────── */

const catalogGrid = $("#catalog-grid");
const catalogStatus = $("#catalog-status");
const selectedChains = new Set();
let catalogPage = 1;
let facetsLoaded = false;

$("#filters").addEventListener("submit", (event) => {
  event.preventDefault();
  catalogPage = 1;
  runCatalog();
});

$("#catalog-reset").addEventListener("click", () => {
  $("#catalog-q").value = "";
  $("#catalog-max-price").value = "";
  $("#catalog-sort").value = "spread";
  $("#catalog-min-chains").value = "2";
  $("#catalog-promo").checked = false;
  selectedChains.clear();
  $$("#catalog-chains .chip").forEach((chip) => chip.setAttribute("aria-pressed", "false"));
  catalogPage = 1;
  runCatalog();
});

async function loadCatalog() {
  if (facetsLoaded) return;
  facetsLoaded = true;
  try {
    const data = await api("/filters");
    $("#catalog-chains").innerHTML = data.chains
      .map(
        (chain) => `<button type="button" class="chip" aria-pressed="false" data-chain="${chain.value}">
          ${chain.label} <span class="chip-n">${num(chain.count)}</span>
        </button>`
      )
      .join("");
  } catch {
    // Filters are a convenience; the catalogue still loads without them.
  }
  runCatalog();
}

$("#catalog-chains").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-chain]");
  if (!chip) return;
  const id = chip.dataset.chain;
  const on = selectedChains.has(id);
  on ? selectedChains.delete(id) : selectedChains.add(id);
  chip.setAttribute("aria-pressed", String(!on));
  catalogPage = 1;
  runCatalog();
});

function catalogParams() {
  const params = new URLSearchParams();
  const q = $("#catalog-q").value.trim();
  if (q.length >= 2) params.set("q", q);
  params.set("sort", $("#catalog-sort").value);
  params.set("min_chains", $("#catalog-min-chains").value);
  const maxPrice = $("#catalog-max-price").value.trim();
  if (maxPrice) params.set("max_price", maxPrice);
  if ($("#catalog-promo").checked) params.set("promo_only", "true");
  selectedChains.forEach((id) => params.append("chain_ids", id));
  params.set("page", catalogPage);
  params.set("page_size", "24");
  return params;
}

async function runCatalog() {
  catalogGrid.innerHTML = "";
  $("#catalog-pager").hidden = true;
  setStatus(catalogStatus, "טוען…");

  try {
    const data = await api(`/products?${catalogParams()}`);
    setStatus(catalogStatus, "");
    $("#catalog-count").textContent = data.total
      ? `${num(data.total)} מוצרים`
      : "";

    if (!data.results.length) {
      setStatus(
        catalogStatus,
        "אין מוצרים שעונים על הסינון. נסו להוריד את מספר הרשתות המינימלי או לנקות את הסינון."
      );
      return;
    }

    data.results.forEach((card) => catalogGrid.append(catalogCard(card)));
    renderPager(data);
  } catch (error) {
    setStatus(catalogStatus, `לא ניתן לטעון את הקטלוג: ${error.message}`, true);
  }
}

function catalogCard(entry) {
  const { product } = entry;
  const node = $("#tpl-catalog-card").content.cloneNode(true);
  const card = $(".pcard", node);

  $('[data-f="name"]', card).textContent = product.name_he;
  $('[data-f="meta"]', card).textContent = packLabel(product);
  $('[data-f="low"]', card).innerHTML = ltr(money(entry.cheapest_price));
  $('[data-f="high"]', card).innerHTML = ltr(money(entry.dearest_price));
  $('[data-f="low-chain"]', card).textContent = entry.cheapest_chain || "";
  $('[data-f="high-chain"]', card).textContent = entry.dearest_chain || "";

  // Bar length is the gap as a share of the dearest price, so a 5% difference
  // looks like 5% instead of filling the track the way a normalised bar would.
  $('[data-f="bar"]', card).style.width = `${Math.min(100, entry.spread_pct || 0)}%`;

  const spread = $('[data-f="spread"]', card);
  spread.innerHTML =
    Number(entry.spread) > 0
      ? `חיסכון של עד ${ltr(money(entry.spread))} <span class="flat">(${num(entry.spread_pct, 1)}%)</span>`
      : `<span class="flat">אותו מחיר בכל הסניפים שנבדקו</span>`;

  const unit = $('[data-f="unit"]', card);
  const bits = [];
  if (entry.cheapest_unit_price)
    bits.push(`${money(entry.cheapest_unit_price)} ${unitLabel(product.unit_of_measure)}`);
  bits.push(`${num(entry.priced_chain_count)} רשתות`);
  if (entry.updated_at) bits.push(ago(entry.updated_at));
  unit.innerHTML = bits.join(" · ");

  if (entry.has_promotion) {
    // Inline in the meta line rather than on its own row: a badge that adds a
    // line makes the cards in a grid row stop lining up with each other.
    $('[data-f="meta"]', card).insertAdjacentHTML(
      "beforeend",
      ' <span class="tag">יש מבצע</span>'
    );
  }

  wireCardActions(card, product, {
    storeCount: entry.store_count,
    loadPrices: () => api(`/products/${product.canonical_id}/prices`),
  });

  return node;
}

function renderPager(data) {
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  if (pages < 2) return;

  const pager = $("#catalog-pager");
  pager.hidden = false;
  pager.innerHTML = `
    <button data-page="${data.page - 1}" ${data.page <= 1 ? "disabled" : ""}>הקודם</button>
    <span class="page-of">עמוד ${num(data.page)} מתוך ${num(pages)}</span>
    <button data-page="${data.page + 1}" ${data.page >= pages ? "disabled" : ""}>הבא</button>`;

  $$("button[data-page]", pager).forEach((button) =>
    button.addEventListener("click", () => {
      catalogPage = Number(button.dataset.page);
      runCatalog();
      window.scrollTo({ top: 0, behavior: "smooth" });
    })
  );
}

/* ─── search ──────────────────────────────────────────────── */

const results = $("#results");
const searchStatus = $("#search-status");
let lastQuery = "";

$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch();
});
$("#sort").addEventListener("change", () => lastQuery && runSearch());

async function runSearch() {
  const query = $("#search-input").value.trim();
  if (query.length < 2) return;
  lastQuery = query;

  results.innerHTML = "";
  setStatus(searchStatus, "מחפש…");

  try {
    const params = new URLSearchParams({ q: query, sort: $("#sort").value });
    const data = await api(`/search?${params}`);

    if (!data.results.length) {
      setStatus(
        searchStatus,
        `לא נמצא מוצר בשם "${query}". נסו שם קצר יותר — החיפוש סובלני לשגיאות כתיב אבל צריך משהו להיאחז בו.`
      );
      return;
    }

    setStatus(searchStatus, "");
    data.results.forEach((result) => results.append(productCard(result)));
  } catch (error) {
    setStatus(searchStatus, `החיפוש נכשל: ${error.message}`, true);
  }
}

function productCard(result) {
  const { product, prices, cheapest_price, cheapest_unit_price } = result;
  const node = $("#tpl-product").content.cloneNode(true);
  const card = $(".card", node);

  $('[data-f="name"]', card).textContent = product.name_he;
  $('[data-f="meta"]', card).textContent = packLabel(product);
  $('[data-f="price"]', card).innerHTML = ltr(money(cheapest_price));
  $('[data-f="unit"]', card).innerHTML = cheapest_unit_price
    ? `${ltr(money(cheapest_unit_price))} ${unitLabel(product.unit_of_measure)}`
    : "";

  wireCardActions(card, product, {
    storeCount: prices.length,
    loadPrices: async () => prices,
  });

  return node;
}

function storeTable(prices) {
  const cheapest = Math.min(...prices.map((p) => Number(p.price)));
  const rows = prices
    .map((entry) => {
      const store = entry.store;
      const where = [store.name, store.city].filter(Boolean).join(" · ");
      return `
      <tr class="${Number(entry.price) === cheapest ? "is-cheapest" : ""}">
        <td>
          <div class="store-chain">${store.chain_name}</div>
          <div class="store-where">${where || "—"}</div>
        </td>
        <td class="num">${ltr(money(entry.price))}</td>
        <td class="num">${entry.normalized_unit_price ? ltr(money(entry.normalized_unit_price)) : "—"}</td>
        <td class="num">${store.distance_km != null ? ltr(`${num(store.distance_km, 1)} ק״מ`) : "—"}</td>
        <td>${entry.has_promotion ? `<span class="tag">${entry.promotion_description || "מבצע"}</span>` : ""}</td>
        <td class="stamp">${ago(entry.updated_at)}</td>
      </tr>`;
    })
    .join("");

  return `
    <div class="table-scroll">
      <table class="store-table">
        <thead><tr>
          <th>סניף</th><th class="num">מחיר</th><th class="num">ליחידה</th>
          <th class="num">מרחק</th><th>מבצע</th><th>עודכן</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/* ─── price history ───────────────────────────────────────── */

function historyPanel(history) {
  const points = history.points.filter((p) => p.avg_price != null);
  if (points.length < 2) {
    return `<p class="hint">אין עדיין מספיק היסטוריה למוצר הזה. הגרף נבנה מיום קליטה אחד לכל יום.</p>`;
  }

  const verdict = history.verdict
    ? `<span class="verdict" data-v="${history.verdict}">${history.verdict}</span>`
    : "";

  return `
    <div class="history-head">
      ${verdict}
      <span class="hint">
        הזול ביותר כרגע ${ltr(money(history.current_min))} ·
        ממוצע ${history.days} יום ${ltr(money(history.average))}
      </span>
    </div>
    ${sparkline(points)}
    <p class="hint">
      השאלה שגרף כזה עונה עליה היא לא "האם זה זול" אלא "האם זה היה פעם אחרת".
    </p>`;
}

function sparkline(points) {
  const width = 640;
  const height = 130;
  const pad = 8;
  const lows = points.map((p) => Number(p.min_price ?? p.avg_price));
  const highs = points.map((p) => Number(p.max_price ?? p.avg_price));
  const avgs = points.map((p) => Number(p.avg_price));

  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const span = max - min || 1;

  // RTL page, chronological chart: oldest on the right, newest on the left,
  // which is how a Hebrew reader scans it.
  const x = (i) => width - pad - (i / (points.length - 1)) * (width - pad * 2);
  const y = (v) => pad + (1 - (v - min) / span) * (height - pad * 2);

  const line = avgs.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const band =
    highs.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ") +
    " " +
    lows
      .map((v, i) => `L${x(lows.length - 1 - i).toFixed(1)},${y(lows[lows.length - 1 - i]).toFixed(1)}`)
      .join(" ") +
    " Z";

  const mean = avgs.reduce((a, b) => a + b, 0) / avgs.length;

  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img"
         aria-label="מחיר ממוצע לאורך ${points.length} ימים">
      <path class="band" d="${band}"/>
      <line class="avg" x1="${pad}" x2="${width - pad}" y1="${y(mean)}" y2="${y(mean)}"/>
      <path class="line" d="${line}"/>
    </svg>`;
}

/* ─── basket ──────────────────────────────────────────────── */

function renderBasket() {
  const empty = $("#basket-empty");
  const panel = $("#basket-panel");
  empty.hidden = basket.length > 0;
  panel.hidden = basket.length === 0;
  if (!basket.length) return;

  $("#basket-list").innerHTML = basket
    .map(
      (item, index) => `
      <li class="basket-row">
        <span class="name">${item.name}</span>
        <span class="qty">
          <button data-qty="-1" data-i="${index}" aria-label="פחות">−</button>
          <output>${item.qty}</output>
          <button data-qty="1" data-i="${index}" aria-label="עוד">+</button>
        </span>
        <button class="remove" data-remove="${index}" aria-label="הסרה">×</button>
      </li>`
    )
    .join("");
}

$("#basket-list").addEventListener("click", (event) => {
  const step = event.target.closest("[data-qty]");
  const remove = event.target.closest("[data-remove]");
  if (step) {
    const item = basket[Number(step.dataset.i)];
    item.qty = Math.max(1, item.qty + Number(step.dataset.qty));
  } else if (remove) {
    basket.splice(Number(remove.dataset.remove), 1);
  } else return;
  saveBasket();
  renderBasket();
});

$("#optimize").addEventListener("click", async () => {
  const button = $("#optimize");
  const status = $("#basket-status");
  const output = $("#basket-result");

  button.disabled = true;
  output.innerHTML = "";
  setStatus(status, "מחשב…");

  try {
    const data = await api("/basket/optimize", {
      method: "POST",
      body: JSON.stringify({
        items: basket.map(({ canonical_id, qty }) => ({ canonical_id, qty })),
        mode: $("#mode").value,
        include_club_promotions: $("#club").checked,
        max_stores: 2,
      }),
    });
    setStatus(status, "");
    output.innerHTML = basketResult(data);
  } catch (error) {
    setStatus(status, `החישוב נכשל: ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
});

function basketResult(data) {
  if (!data.single_store) {
    return `<div class="callout">
      לא נמצא אף סניף שמוכר את המוצרים האלה. נבדקו ${num(data.stores_considered)} סניפים.
    </div>`;
  }

  const plans = [plan(data.single_store, "סניף אחד", !data.split)];
  if (data.split) plans.push(plan(data.split, "פיצול בין שני סניפים", true));

  const parts = [`<div class="plans">${plans.join("")}</div>`];

  if (data.split && data.split_saving != null) {
    const minutes = data.split_extra_minutes;
    const time = minutes ? ` ומוסיף ${num(minutes)} דקות נסיעה` : "";
    parts.push(`<div class="callout is-saving">
      פיצול בין שני סניפים חוסך ${ltr(money(Math.abs(data.split_saving)))}${time}.
      ההחלטה שלכם.
    </div>`);
  }

  const skipped = data.single_store.skipped_promotions;
  if (skipped) {
    parts.push(`<div class="callout">
      החישוב כולל ${num(data.single_store.applied_promotions)} מבצעים.
      <strong>${num(skipped)} מבצעים נוספים לא נכללו</strong> — הם מסוגים שהמערכת
      עדיין לא יודעת לחשב בביטחון, כמו מבצעי סף ומתנות. עדיף מספר שמרני מאשר מספר שגוי.
    </div>`);
  }

  if (data.missing_products.length) {
    parts.push(`<div class="callout">
      <strong>לא נמצאו בסניפים שנבדקו:</strong>
      <ul>${data.missing_products.map((p) => `<li>${p.name_he}</li>`).join("")}</ul>
    </div>`);
  }

  return parts.join("");
}

function plan(data, title, isBest) {
  const stores = data.stores
    .map(
      (entry) => `<li>
        <span>${entry.store.chain_name}${entry.store.name ? ` · ${entry.store.name}` : ""}</span>
        <span dir="ltr">${money(entry.goods_total)}</span>
      </li>`
    )
    .join("");

  const notes = [];
  // The headline is what the register charges. Travel is a weighting the
  // engine uses to compare plans, not money anyone hands over, and printing
  // the weighted figure as "the total" makes the app look wrong to anyone who
  // adds up the shelf prices.
  if (Number(data.travel_cost) > 0) {
    notes.push(`כולל שקלול נסיעה של ${money(data.travel_cost)} להשוואה בין החלופות`);
  }
  notes.push(
    Number(data.saved) > 0 ? `נחסכו ${money(data.saved)} ממבצעים` : "ללא מבצעים שחלים"
  );

  return `
    <article class="plan ${isBest ? "is-best" : ""}">
      ${isBest ? '<p class="plan-tag">ההמלצה</p>' : ""}
      <h3>${title}</h3>
      <p class="plan-total" dir="ltr">${money(data.goods_total)}</p>
      <p class="plan-sub">${notes.join(" · ")}</p>
      <ul class="plan-stores">${stores}</ul>
    </article>`;
}

/* ─── coverage ────────────────────────────────────────────── */

let coverageLoaded = false;

async function loadCoverage() {
  if (coverageLoaded) return;
  const host = $("#coverage");
  host.innerHTML = "<p class='hint'>טוען…</p>";

  try {
    const data = await api("/coverage");
    coverageLoaded = true;

    const rows = data.chains
      .map(
        (chain) => `
        <tr class="${chain.priced_rows ? "" : "is-idle"}">
          <td>${chain.name_he}</td>
          <td class="num">${num(chain.stores)}</td>
          <td class="num">${num(chain.stores_geocoded)}</td>
          <td class="num">${num(chain.variants)}</td>
          <td class="num">${num(chain.priced_rows)}</td>
          <td class="stamp">${chain.last_ingested ? ago(chain.last_ingested) : "—"}</td>
        </tr>`
      )
      .join("");

    const geoWarning =
      data.stores_geocoded === 0 && data.stores_total > 0
        ? `<div class="callout">
             אף סניף לא מוקם על המפה עדיין, ולכן חיפוש לפי מרחק יחזיר ריק.
             נדרש <code>GOOGLE_MAPS_API_KEY</code> ואז <code>python -m ingestion geocode</code>.
           </div>`
        : "";

    host.innerHTML = `
      <div class="stat-row">
        <div class="stat"><div class="n">${num(data.products_multi_chain)}</div>
          <div class="l">מוצרים בני־השוואה</div></div>
        <div class="stat"><div class="n">${num(data.products)}</div>
          <div class="l">מוצרים בקטלוג</div></div>
        <div class="stat"><div class="n">${num(data.stores_total)}</div>
          <div class="l">סניפים</div></div>
        <div class="stat"><div class="n">${num(data.prices_total)}</div>
          <div class="l">מחירים פעילים</div></div>
      </div>
      <div class="table-scroll">
        <table class="coverage-table">
          <thead><tr>
            <th>רשת</th><th class="num">סניפים</th><th class="num">ממוקמים</th>
            <th class="num">פריטים</th><th class="num">מחירים</th><th>נקלט</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${geoWarning}`;
  } catch (error) {
    host.innerHTML = `<p class="status is-error">לא ניתן לטעון: ${error.message}</p>`;
  }
}

/* ─── boot ────────────────────────────────────────────────── */

saveBasket();
renderBasket();
loadCatalog();
