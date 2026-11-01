// Daily grid export/import for a selected month, split like an Enea net-billing
// invoice: the prosumer deposit (export × RCEm × 1.23, from rcem:earnings_gross)
// offsets only the energy part of the import; distribution is always paid.
// Use the billing meter's hourly-balanced statistics (Enea integration) as sources.
//
// type: custom:pv-balance-month-card
// import_entity: enea:<PPE>_energia_pobrana
// export_entity: enea:<PPE>_energia_oddana
// earnings_statistic: rcem:earnings_gross
// energy_t1: 0.5829         # PLN/kWh net, energy (sprzedaż), day zone
// energy_t2: 0.3419         #                               night zone
// dist_t1: 0.3214           # PLN/kWh net, variable distribution (sieciowa + jakościowa + OZE + kogeneracyjna)
// dist_t2: 0.1351
// vat: 23
// t2_hours: [13, 14, 22, 23, 0, 1, 2, 3, 4, 5]
// fixed_monthly: 52.2135    # PLN gross: stała sieciowa + mocowa + abonamentowa
// Without energy_*/dist_*, price_t1/price_t2 (gross, all-in) are used as one import price.

const MONTHS_PL = [
  "Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
  "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień",
];

const MONTHS_GEN = [
  "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
  "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
];

const fmtPLN = (v) =>
  `${v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} zł`;
const num = (v, d = 1) => v.toLocaleString("pl-PL", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtKWh = (v) => `${v.toLocaleString("pl-PL", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} kWh`;

class PvBalanceMonthCard extends HTMLElement {
  setConfig(config) {
    if (!config.import_entity || !config.export_entity) {
      throw new Error("import_entity i export_entity są wymagane");
    }
    this._c = Object.assign(
      {
        title: "Bilans z siecią – wybrany miesiąc",
        earnings_statistic: "rcem:earnings_gross",
        price_t1: 1.1123,
        price_t2: 0.5863,
        vat: 23,
        t2_hours: [13, 14, 22, 23, 0, 1, 2, 3, 4, 5],
        fixed_monthly: 0,
      },
      config,
    );
    this._month = null;
    this._months = null;
    this._data = null;
    this._built = false;
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._built) this._build();
    if (first) this._init();
  }

  _build() {
    this._built = true;
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
        .title { font-size: 16px; font-weight: 500; }
        select { font: inherit; padding: 6px 10px; border-radius: 8px; border: 1px solid var(--divider-color);
                 background: var(--card-background-color); color: var(--primary-text-color); }
        .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin-bottom: 12px; }
        .kpi { background: var(--secondary-background-color); border-radius: 12px; padding: 10px 12px; }
        .kpi .l { font-size: 12px; color: var(--secondary-text-color); }
        .kpi .v { font-size: 20px; font-weight: 600; margin: 2px 0; }
        .kpi .s { font-size: 12px; color: var(--secondary-text-color); }
        .pos { color: #30a46c; } .neg { color: #e5484d; }
        .chart { position: relative; }
        svg { width: 100%; display: block; overflow: visible; }
        .tip { position: absolute; pointer-events: none; background: var(--card-background-color);
               border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; font-size: 12px;
               box-shadow: 0 4px 12px rgba(0,0,0,.15); white-space: nowrap; display: none; z-index: 2; }
        .tip b { font-weight: 600; }
        .legend { display: flex; gap: 16px; justify-content: center; font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; flex-wrap: wrap; }
        .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
        .badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: var(--warning-color, #ffa600); color: #fff; margin-left: 8px; }
        .msg { color: var(--secondary-text-color); padding: 24px; text-align: center; }
      </style>
      <ha-card>
        <div class="head">
          <div class="title"></div>
          <select></select>
        </div>
        <div class="kpis"></div>
        <div class="chart"><svg></svg><div class="tip"></div></div>
        <div class="legend">
          <span><span class="dot" style="background:#30a46c"></span>Eksport do sieci</span>
          <span><span class="dot" style="background:#e5484d"></span>Import z sieci</span>
          <span class="hint">Kwota dnia (depozyt − energia − dystrybucja zmienna): nad słupkiem lub po najechaniu / dotknięciu</span>
        </div>
      </ha-card>`;
    this.shadowRoot.querySelector(".title").textContent = this._c.title;
    this._select = this.shadowRoot.querySelector("select");
    this._select.addEventListener("change", () => {
      this._month = this._select.value;
      this._load();
    });
    this._resize = new ResizeObserver(() => this._render());
    this._resize.observe(this.shadowRoot.querySelector(".chart"));
  }

  async _init() {
    const res = await this._stats(new Date(2020, 0, 1), null, [this._c.export_entity], "month");
    const rows = res[this._c.export_entity] || [];
    const now = new Date();
    const first = rows.length ? new Date(rows[0].start) : now;
    const months = [];
    for (let d = new Date(first.getFullYear(), first.getMonth(), 1); d <= now; d.setMonth(d.getMonth() + 1)) {
      months.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
    }
    this._months = months.reverse();
    this._month = this._months[0];
    this._select.innerHTML = this._months
      .map((m) => `<option value="${m}">${MONTHS_PL[+m.slice(5) - 1]} ${m.slice(0, 4)}</option>`)
      .join("");
    this._load();
  }

  _stats(start, end, ids, period) {
    return this._hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: start.toISOString(),
      end_time: end ? end.toISOString() : undefined,
      statistic_ids: ids,
      period,
      types: ["change"],
      units: { energy: "kWh" },
    });
  }

  async _load() {
    const month = this._month;
    const [y, m] = month.split("-").map(Number);
    const start = new Date(y, m - 1, 1);
    const end = new Date(y, m, 1);
    const days = new Date(y, m, 0).getDate();
    const c = this._c;
    this._data = null;
    this._render();
    let res;
    try {
      res = await this._stats(start, end, [c.import_entity, c.export_entity, c.earnings_statistic], "hour");
    } catch (err) {
      this._error = String(err.message || err);
      this._render();
      return;
    }
    if (month !== this._month) return;
    this._error = null;
    const t2 = new Set(c.t2_hours.map(Number));
    const vat = 1 + c.vat / 100;
    const split = c.energy_t1 != null;
    const rate = {
      e1: split ? c.energy_t1 * vat : c.price_t1, e2: split ? c.energy_t2 * vat : c.price_t2,
      d1: split ? (c.dist_t1 || 0) * vat : 0, d2: split ? (c.dist_t2 || 0) * vat : 0,
    };
    const day = Array.from({ length: days }, (_, i) => ({
      d: i + 1, exp: 0, imp: 0, impT1: 0, impT2: 0, energy: 0, dist: 0, cost: 0, earn: 0,
    }));
    for (const r of res[c.import_entity] || []) {
      const t = new Date(r.start);
      const kwh = r.change || 0;
      const o = day[t.getDate() - 1];
      if (t2.has(t.getHours())) { o.impT2 += kwh; o.energy += kwh * rate.e2; o.dist += kwh * rate.d2; }
      else { o.impT1 += kwh; o.energy += kwh * rate.e1; o.dist += kwh * rate.d1; }
      o.imp += kwh;
      o.cost = o.energy + o.dist;
    }
    for (const r of res[c.export_entity] || []) day[new Date(r.start).getDate() - 1].exp += r.change || 0;
    for (const r of res[c.earnings_statistic] || []) day[new Date(r.start).getDate() - 1].earn += r.change || 0;
    for (const o of day) o.net = o.earn - o.cost;

    const now = new Date();
    const elapsed = now >= end ? days : Math.max(1, Math.min(days, now.getDate()));
    const months = this._hass.states["sensor.rcem_pse_earnings_total_gross"]?.attributes?.months || {};
    this._data = {
      month, days, day, elapsed,
      fixed: (c.fixed_monthly * elapsed) / days,
      estimated: !!months[month]?.estimated,
      split,
    };
    this._render();
  }

  _render() {
    if (!this._built) return;
    const svg = this.shadowRoot.querySelector("svg");
    const kpis = this.shadowRoot.querySelector(".kpis");
    const title = this.shadowRoot.querySelector(".title");
    if (this._error) {
      kpis.innerHTML = "";
      svg.innerHTML = "";
      svg.setAttribute("viewBox", "0 0 100 10");
      kpis.innerHTML = `<div class="msg">Błąd pobierania statystyk: ${this._error}</div>`;
      return;
    }
    const D = this._data;
    if (!D) {
      kpis.innerHTML = `<div class="msg">Wczytywanie…</div>`;
      svg.innerHTML = "";
      return;
    }
    const sum = (k) => D.day.reduce((a, o) => a + o[k], 0);
    const exp = sum("exp"), imp = sum("imp"), earn = sum("earn"), cost = sum("cost");
    const energy = sum("energy"), dist = sum("dist");
    const impT1 = sum("impT1"), impT2 = sum("impT2");
    const balance = earn - cost - D.fixed;
    title.innerHTML = "";
    title.append(this._c.title);
    if (D.estimated) {
      const b = document.createElement("span");
      b.className = "badge";
      b.textContent = "RCEm szacowana";
      title.append(b);
    }
    const importKpis = D.split
      ? `<div class="kpi"><div class="l">Energia pobrana (pokrywa depozyt)</div>
          <div class="v neg">${fmtPLN(energy)}</div>
          <div class="s">${fmtKWh(imp)} (T1 ${num(impT1)} · T2 ${num(impT2)})</div></div>
        <div class="kpi"><div class="l">Dystrybucja (płatna zawsze)</div>
          <div class="v neg">${fmtPLN(dist + D.fixed)}</div>
          <div class="s">zmienna ${fmtPLN(dist)} + stała ${fmtPLN(D.fixed)}</div></div>`
      : `<div class="kpi"><div class="l">Import → koszt energii</div>
          <div class="v neg">${fmtPLN(cost)}</div>
          <div class="s">${fmtKWh(imp)} (T1 ${num(impT1)} · T2 ${num(impT2)})</div></div>
        <div class="kpi"><div class="l">Opłaty stałe</div>
          <div class="v">${fmtPLN(D.fixed)}</div>
          <div class="s">z ${fmtPLN(this._c.fixed_monthly)} / mies.</div></div>`;
    kpis.innerHTML = `
      <div class="kpi"><div class="l">Bilans miesiąca</div>
        <div class="v ${balance >= 0 ? "pos" : "neg"}">${fmtPLN(balance)}</div>
        <div class="s">depozyt − energia − dystrybucja · śr. ${fmtPLN(balance / D.elapsed)} / dzień</div></div>
      <div class="kpi"><div class="l">Depozyt z eksportu (RCEm × 1,23)</div>
        <div class="v pos">${fmtPLN(earn)}</div>
        <div class="s">${fmtKWh(exp)} · ${exp ? num(earn / exp, 3) : "–"} zł/kWh</div></div>
      ${importKpis}
      <div class="kpi"><div class="l">Saldo energii</div>
        <div class="v">${(exp - imp >= 0 ? "+" : "−") + fmtKWh(Math.abs(exp - imp))}</div>
        <div class="s">eksport − import (po bilansowaniu)</div></div>`;

    const W = Math.max(320, this.shadowRoot.querySelector(".chart").clientWidth || 600);
    const H = 300, padL = 40, padR = 8, padT = 22, padB = 22;
    const maxUp = Math.max(1, ...D.day.map((o) => o.exp));
    const maxDown = Math.max(1, ...D.day.map((o) => o.imp));
    const plotH = H - padT - padB;
    const zero = padT + (plotH * maxUp) / (maxUp + maxDown);
    const scale = plotH / (maxUp + maxDown);
    const slot = (W - padL - padR) / D.days;
    const bw = Math.max(3, slot * 0.62);
    const step = maxUp + maxDown > 60 ? 20 : maxUp + maxDown > 25 ? 10 : 5;
    let g = "";
    for (let v = -Math.floor(maxDown / step) * step; v <= maxUp; v += step) {
      const yy = zero - v * scale;
      g += `<line x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}" stroke="var(--divider-color)" stroke-dasharray="${v ? "3 3" : ""}"/>`;
      g += `<text x="${padL - 6}" y="${yy + 4}" text-anchor="end" font-size="11" fill="var(--secondary-text-color)">${Math.abs(v)}</text>`;
    }
    g += `<text x="${padL - 6}" y="${padT - 8}" text-anchor="end" font-size="10" fill="var(--secondary-text-color)">kWh</text>`;
    const labelEvery = slot < 18 ? 2 : 1;
    D.day.forEach((o, i) => {
      const x = padL + i * slot + (slot - bw) / 2;
      const hu = o.exp * scale, hd = o.imp * scale;
      g += `<g data-i="${i}">`;
      g += `<rect x="${padL + i * slot}" y="${padT}" width="${slot}" height="${plotH}" fill="transparent"/>`;
      if (hu > 0) g += `<rect x="${x}" y="${zero - hu}" width="${bw}" height="${hu}" rx="2" fill="#30a46c"/>`;
      if (hd > 0) g += `<rect x="${x}" y="${zero}" width="${bw}" height="${hd}" rx="2" fill="#e5484d"/>`;
      // Day amounts need ~16 px per bar to stay legible; narrower charts rely on the tooltip.
      if ((o.exp || o.imp) && slot >= 16) {
        const net = o.net;
        const txt = (net >= 0 ? "+" : "−") + num(Math.abs(net), Math.abs(net) >= 10 || slot < 26 ? 0 : 1);
        g += `<text x="${x + bw / 2}" y="${zero - hu - 4}" text-anchor="middle" font-size="${slot < 22 ? 9 : 10}" font-weight="600" fill="${net >= 0 ? "#30a46c" : "#e5484d"}">${txt}</text>`;
      }
      if (labelEvery === 1 || o.d % 2 === 1)
        g += `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--secondary-text-color)">${o.d}</text>`;
      g += `</g>`;
    });
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = g;

    const tip = this.shadowRoot.querySelector(".tip");
    svg.onmousemove = (ev) => {
      const grp = ev.target.closest("g[data-i]");
      if (!grp) { tip.style.display = "none"; return; }
      const o = D.day[+grp.dataset.i];
      tip.innerHTML = `<b>${o.d} ${MONTHS_GEN[+D.month.slice(5) - 1]}</b><br>
        Eksport: ${fmtKWh(o.exp)} → depozyt <span class="pos">${fmtPLN(o.earn)}</span><br>
        Import: ${fmtKWh(o.imp)} (T1 ${num(o.impT1)} / T2 ${num(o.impT2)}) → <span class="neg">${fmtPLN(-o.cost)}</span><br>
        ${D.split ? `<span style="color:var(--secondary-text-color)">energia ${fmtPLN(o.energy)} · dystrybucja ${fmtPLN(o.dist)}</span><br>` : ""}
        <b>Dzień: <span class="${o.net >= 0 ? "pos" : "neg"}">${fmtPLN(o.net)}</span></b>`;
      tip.style.display = "block";
      const box = this.shadowRoot.querySelector(".chart").getBoundingClientRect();
      const left = ev.clientX - box.left + 12;
      tip.style.left = `${Math.min(left, box.width - tip.offsetWidth - 4)}px`;
      tip.style.top = `${ev.clientY - box.top - tip.offsetHeight - 8}px`;
    };
    // Touch screens have no hover: a tap on a day shows the same tooltip.
    svg.onpointerdown = svg.onmousemove;
    svg.onmouseleave = () => { tip.style.display = "none"; };
  }
}

customElements.define("pv-balance-month-card", PvBalanceMonthCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "pv-balance-month-card",
  name: "Bilans z siecią – miesiąc",
  description: "Dzienny eksport/import z wyceną G12 i RCEm",
});
