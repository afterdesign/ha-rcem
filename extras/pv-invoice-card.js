// Estimated monthly electricity invoice for an Enea net-billing prosumer:
// with and without the prosumer deposit.
//
// Deposit rules (checked against the Enea eBOK deposit history 2025–2026):
// - a month's deposit (export × RCEm × 1.23, rcem:earnings_gross) can be used
//   from the next month on, for 12 months, oldest first;
// - it covers only the energy part of the invoice, never distribution or fixed fees.
//
// type: custom:pv-invoice-card
// import_entity: enea:<PPE>_energia_pobrana
// earnings_statistic: rcem:earnings_gross
// months: 12
// vat: 23
// t2_hours: [13, 14, 22, 23, 0, 1, 2, 3, 4, 5]
// fixed_monthly: 52.2135
// tariffs: [...]            # same format as custom:pv-balance-month-card

const MONTHS_SHORT = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];
const MONTHS_NAME = [
  "styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
  "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień",
];

const fmtPLN = (v) =>
  `${v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} zł`;
const fmtInt = (v) => `${Math.round(v).toLocaleString("pl-PL")}`;
const monthKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const monthIndex = (key) => +key.slice(0, 4) * 12 + (+key.slice(5) - 1);

class PvInvoiceCard extends HTMLElement {
  setConfig(config) {
    if (!config.import_entity) throw new Error("import_entity jest wymagane");
    this._c = Object.assign(
      {
        title: "Szacunek faktur",
        earnings_statistic: "rcem:earnings_gross",
        months: 12,
        vat: 23,
        t2_hours: [13, 14, 22, 23, 0, 1, 2, 3, 4, 5],
        fixed_monthly: 0,
        tariffs: [],
        deposit_valid_months: 12,
      },
      config,
    );
    this._rows = null;
    this._built = false;
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._built) this._build();
    if (first) this._load();
  }

  _build() {
    this._built = true;
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .title { font-size: 16px; font-weight: 500; margin-bottom: 12px; }
        .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; margin-bottom: 12px; }
        .kpi { background: var(--secondary-background-color); border-radius: 12px; padding: 10px 12px; }
        .kpi .l { font-size: 12px; color: var(--secondary-text-color); }
        .kpi .v { font-size: 20px; font-weight: 600; margin: 2px 0; }
        .kpi .s { font-size: 12px; color: var(--secondary-text-color); }
        .chart { position: relative; }
        svg { width: 100%; display: block; overflow: visible; }
        .tip { position: absolute; pointer-events: none; background: var(--card-background-color);
               border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; font-size: 12px;
               box-shadow: 0 4px 12px rgba(0,0,0,.15); white-space: nowrap; display: none; z-index: 2; line-height: 1.5; }
        .legend { display: flex; gap: 16px; justify-content: center; font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; flex-wrap: wrap; }
        .dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 4px; vertical-align: -1px; }
        .note { font-size: 11px; color: var(--secondary-text-color); margin-top: 8px; }
        .msg { color: var(--secondary-text-color); padding: 24px; text-align: center; }
      </style>
      <ha-card>
        <div class="title"></div>
        <div class="kpis"><div class="msg">Wczytywanie…</div></div>
        <div class="chart"><svg></svg><div class="tip"></div></div>
        <div class="legend">
          <span><span class="dot" style="background:#c9cdd6"></span>Faktura bez depozytu</span>
          <span><span class="dot" style="background:#3e63dd"></span>Do zapłaty z depozytem</span>
          <span><span class="dot" style="background:#30a46c"></span>Depozyt do wykorzystania (koniec miesiąca)</span>
        </div>
        <div class="note">Depozyt pokrywa tylko energię; dystrybucja i opłaty stałe są płatne zawsze. Bieżący miesiąc: dane do wczoraj (licznik Enea), opłaty stałe proporcjonalnie. Bez nadpłat i odsetek. „+” przy kwocie: brak cen dystrybucji dla tego okresu (taryfa G11), kwota jest zaniżona o dystrybucję zmienną.</div>
      </ha-card>`;
    this.shadowRoot.querySelector(".title").textContent = this._c.title;
    this._resize = new ResizeObserver(() => this._render());
    this._resize.observe(this.shadowRoot.querySelector(".chart"));
  }

  _stats(start, end, ids, period, types = ["change"]) {
    return this._hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: start.toISOString(),
      end_time: end ? end.toISOString() : undefined,
      statistic_ids: ids,
      period,
      types,
      units: { energy: "kWh" },
    });
  }

  _rateFor(ts) {
    const c = this._c;
    const vat = 1 + c.vat / 100;
    if (!this._tariffs) {
      this._tariffs = (c.tariffs.length ? c.tariffs : [c])
        .map((t) => ({ ...t, fromTs: t.from ? new Date(`${t.from}T00:00:00`).getTime() : -Infinity }))
        .sort((a, b) => a.fromTs - b.fromTs);
    }
    let t = this._tariffs[0];
    for (const x of this._tariffs) if (x.fromTs <= ts) t = x;
    if (t.energy_t1 != null)
      return { zones: true, e1: t.energy_t1 * vat, e2: t.energy_t2 * vat,
               d1: t.dist_t1 != null ? t.dist_t1 * vat : null, d2: t.dist_t2 != null ? t.dist_t2 * vat : null };
    const d = t.dist != null ? t.dist * vat : null;
    return { zones: false, e1: (t.energy || 0) * vat, e2: (t.energy || 0) * vat, d1: d, d2: d };
  }

  async _load() {
    const c = this._c;
    try {
      const dep = await this._stats(new Date(2020, 0, 1), null, [c.earnings_statistic], "month");
      const depRows = dep[c.earnings_statistic] || [];
      const deposit = {};
      for (const r of depRows) deposit[monthKey(new Date(r.start))] = r.change || 0;
      const first = depRows.length ? new Date(depRows[0].start) : new Date();
      const start = new Date(first.getFullYear(), first.getMonth(), 1);
      const imp = await this._stats(start, null, [c.import_entity], "hour");
      const t2 = new Set(c.t2_hours.map(Number));
      const months = {};
      const now = new Date();
      for (let d = new Date(start); d <= now; d.setMonth(d.getMonth() + 1)) {
        months[monthKey(d)] = { key: monthKey(d), kwh: 0, energy: 0, dist: 0, distUnknown: false };
      }
      for (const r of imp[c.import_entity] || []) {
        const t = new Date(r.start);
        const m = months[monthKey(t)];
        if (!m) continue;
        const kwh = r.change || 0;
        const rate = this._rateFor(t.getTime());
        const night = rate.zones && t2.has(t.getHours());
        const dRate = night ? rate.d2 : rate.d1;
        m.kwh += kwh;
        m.energy += kwh * (night ? rate.e2 : rate.e1);
        if (dRate == null) { if (kwh > 0) m.distUnknown = true; }
        else m.dist += kwh * dRate;
      }
      // Deposit ledger: FIFO, usable from the next month, valid for N months.
      const queue = [];
      const rows = [];
      for (const key of Object.keys(months).sort()) {
        const m = months[key];
        const idx = monthIndex(key);
        while (queue.length && idx - queue[0].idx > c.deposit_valid_months) queue.shift();
        let need = m.energy;
        let used = 0;
        for (const q of queue) {
          const take = Math.min(q.value, need);
          q.value -= take; need -= take; used += take;
          if (need <= 0) break;
        }
        while (queue.length && queue[0].value <= 1e-9) queue.shift();
        const earned = deposit[key] || 0;
        queue.push({ idx, value: earned });
        const [y, mo] = key.split("-").map(Number);
        const days = new Date(y, mo, 0).getDate();
        const current = key === monthKey(now);
        const fixed = current ? (c.fixed_monthly * Math.max(1, now.getDate() - 1)) / days : c.fixed_monthly;
        const gross = m.energy + m.dist + fixed;
        rows.push({
          ...m, earned, used, fixed, current,
          withoutDeposit: gross,
          withDeposit: gross - used,
          balance: queue.reduce((a, q) => a + q.value, 0),
        });
      }
      this._rows = rows;
      this._error = null;
    } catch (err) {
      this._error = String(err.message || err);
    }
    this._render();
  }

  _render() {
    if (!this._built) return;
    const kpis = this.shadowRoot.querySelector(".kpis");
    const svg = this.shadowRoot.querySelector("svg");
    if (this._error) {
      kpis.innerHTML = `<div class="msg">Błąd pobierania statystyk: ${this._error}</div>`;
      svg.innerHTML = "";
      return;
    }
    if (!this._rows) return;
    const rows = this._rows.slice(-this._c.months);
    const cur = rows[rows.length - 1];
    const prev = rows[rows.length - 2];
    const label = (r) => `${MONTHS_NAME[+r.key.slice(5) - 1]} ${r.key.slice(0, 4)}`;
    const q = (r) => (r.distUnknown ? " +?" : "");
    kpis.innerHTML = `
      ${prev ? `<div class="kpi"><div class="l">Faktura za ${label(prev)}</div>
        <div class="v">${fmtPLN(prev.withDeposit)}${q(prev)}</div>
        <div class="s">bez depozytu ${fmtPLN(prev.withoutDeposit)}${q(prev)}</div></div>` : ""}
      <div class="kpi"><div class="l">${label(cur)} (do wczoraj)</div>
        <div class="v">${fmtPLN(cur.withDeposit)}${q(cur)}</div>
        <div class="s">bez depozytu ${fmtPLN(cur.withoutDeposit)}${q(cur)}</div></div>
      <div class="kpi"><div class="l">Depozyt do wykorzystania</div>
        <div class="v" style="color:#30a46c">${fmtPLN(cur.balance)}</div>
        <div class="s">na koniec okresu z danymi</div></div>`;

    const W = Math.max(320, this.shadowRoot.querySelector(".chart").clientWidth || 600);
    const H = 280, padL = 44, padR = 8, padT = 24, padB = 24;
    const max = Math.max(50, ...rows.map((r) => Math.max(r.withoutDeposit, r.balance)));
    const step = max > 1500 ? 500 : max > 600 ? 200 : max > 250 ? 100 : 50;
    const top = Math.ceil(max / step) * step;
    const plotH = H - padT - padB;
    const y = (v) => padT + plotH - (v / top) * plotH;
    const slot = (W - padL - padR) / rows.length;
    const bw = Math.max(4, Math.min(28, slot * 0.32));
    let g = "";
    for (let v = 0; v <= top; v += step) {
      g += `<line x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}" stroke="var(--divider-color)" stroke-dasharray="${v ? "3 3" : ""}"/>`;
      g += `<text x="${padL - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--secondary-text-color)">${fmtInt(v)}</text>`;
    }
    g += `<text x="${padL - 6}" y="${padT - 10}" text-anchor="end" font-size="10" fill="var(--secondary-text-color)">zł</text>`;
    const pts = [];
    rows.forEach((r, i) => {
      const cx = padL + i * slot + slot / 2;
      const x1 = cx - bw - 1, x2 = cx + 1;
      g += `<g data-i="${i}"><rect x="${padL + i * slot}" y="${padT}" width="${slot}" height="${plotH}" fill="transparent"/>`;
      g += `<rect x="${x1}" y="${y(r.withoutDeposit)}" width="${bw}" height="${y(0) - y(r.withoutDeposit)}" rx="2" fill="#c9cdd6" ${r.current ? 'opacity="0.6"' : ""}/>`;
      g += `<rect x="${x2}" y="${y(r.withDeposit)}" width="${bw}" height="${y(0) - y(r.withDeposit)}" rx="2" fill="#3e63dd" ${r.current ? 'opacity="0.6"' : ""}/>`;
      if (slot >= 34)
        g += `<text x="${x2 + bw / 2}" y="${y(r.withDeposit) - 4}" text-anchor="middle" font-size="10" font-weight="600" fill="#3e63dd">${fmtInt(r.withDeposit)}${r.distUnknown ? "+" : ""}</text>`;
      // Narrow charts: every other month, without the year.
      const showLabel = slot >= 30 || (rows.length - 1 - i) % 2 === 0;
      const withYear = slot >= 40 && (r.key.endsWith("-01") || i === 0);
      if (showLabel)
        g += `<text x="${cx}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--secondary-text-color)">${MONTHS_SHORT[+r.key.slice(5) - 1]}${withYear ? ` ${r.key.slice(2, 4)}` : ""}</text>`;
      g += `</g>`;
      pts.push(`${cx},${y(r.balance)}`);
    });
    g += `<polyline points="${pts.join(" ")}" fill="none" stroke="#30a46c" stroke-width="2" pointer-events="none"/>`;
    rows.forEach((r, i) => {
      g += `<circle cx="${padL + i * slot + slot / 2}" cy="${y(r.balance)}" r="3" fill="#30a46c" pointer-events="none"/>`;
    });
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = g;

    const tip = this.shadowRoot.querySelector(".tip");
    const show = (ev) => {
      const grp = ev.target.closest("g[data-i]");
      if (!grp) { tip.style.display = "none"; return; }
      const r = rows[+grp.dataset.i];
      tip.innerHTML = `<b>${label(r)}${r.current ? " (do wczoraj)" : ""}</b><br>
        Energia: ${fmtPLN(r.energy)} (${r.kwh.toLocaleString("pl-PL", { maximumFractionDigits: 1 })} kWh)<br>
        Dystrybucja zmienna: ${r.distUnknown ? "brak cen" : fmtPLN(r.dist)}<br>
        Opłaty stałe: ${fmtPLN(r.fixed)}<br>
        <b>Faktura bez depozytu: ${fmtPLN(r.withoutDeposit)}${q(r)}</b><br>
        Depozyt wykorzystany: −${fmtPLN(r.used)}<br>
        <b style="color:#3e63dd">Do zapłaty: ${fmtPLN(r.withDeposit)}${q(r)}</b><br>
        Depozyt z eksportu w tym miesiącu: +${fmtPLN(r.earned)}<br>
        Depozyt do wykorzystania: ${fmtPLN(r.balance)}`;
      tip.style.display = "block";
      const box = this.shadowRoot.querySelector(".chart").getBoundingClientRect();
      const left = ev.clientX - box.left + 12;
      tip.style.left = `${Math.max(0, Math.min(left, box.width - tip.offsetWidth - 4))}px`;
      tip.style.top = `${Math.max(0, ev.clientY - box.top - tip.offsetHeight - 8)}px`;
    };
    svg.onmousemove = show;
    svg.onpointerdown = show;
    svg.onmouseleave = () => { tip.style.display = "none"; };
  }
}

customElements.define("pv-invoice-card", PvInvoiceCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "pv-invoice-card",
  name: "Szacunek faktur (net-billing)",
  description: "Miesięczna faktura z depozytem prosumenckim i bez",
});
