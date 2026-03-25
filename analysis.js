// analysis.js
// Renders precomputed analysis results.
// No live computation — all metrics and series are loaded from precomputed JSON.
import {
  configureCatalogPaths,
  configurePricePaths,
  getPrecomputed,
} from "./data_loader.js";

document.addEventListener("DOMContentLoaded", () => {
  const SETTINGS = { timescale: "3M", interval: "1d" };
  let CASE_NAME = window.currentCaseName || "Chroma 2";
  configureCatalogPaths({ root: "./data/catalogues" });
  configurePricePaths({ root: "./data/precomputed" });

  // ---- Container ----
  const card = document.getElementById("analysis-card");
  if (!card) return;
  card.innerHTML = `<h3 style="margin-bottom:12px;">Quant Analysis</h3>`;

  // ---- Tabs ----
  const tabs = [
    { id: "core_stats",      title: "Core Stats" },
    { id: "efficiency",      title: "Efficiency" },
    { id: "volatility",      title: "Volatility" },
    { id: "cross_section",   title: "Cross-Section" },
    { id: "hurst",           title: "Hurst" },
    { id: "autocorrelation", title: "Autocorrelation" },
    { id: "cointegration",   title: "Cointegration" },
    { id: "regimes",         title: "Regimes" },
    { id: "signals",         title: "Signals" },
    { id: "liquidity",       title: "Liquidity" },
  ];

  const nav = document.createElement("div");
  nav.style.display = "flex";
  nav.style.gap = "6px";
  nav.style.marginBottom = "12px";

  const panels = document.createElement("div");
  panels.style.display = "block";

  const state = { active: tabs[0].id };

  tabs.forEach(t => {
    const btn = document.createElement("button");
    btn.textContent = t.title;
    styleTab(btn, t.id === state.active);
    btn.addEventListener("click", () => {
      state.active = t.id;
      [...nav.children].forEach(b => styleTab(b, b.textContent === t.title));
      render();
    });
    nav.appendChild(btn);
  });

  card.appendChild(nav);
  card.appendChild(panels);

  // ---- React to global changes sent by case_ev.js ----
  window.addEventListener("case-settings-changed", (e) => {
    const d = e?.detail || {};
    if (d.timescale) SETTINGS.timescale = d.timescale;
    if (d.interval)  SETTINGS.interval  = d.interval;
    render();
  });
  window.addEventListener("case-selected", (e) => {
    CASE_NAME = (e?.detail?.caseName) || CASE_NAME;
    render();
  });

  render();

  // =========================
  // Renderer — reads precomputed data
  // =========================
  async function render() {
    panels.innerHTML = `<div style="opacity:.8;">Loading…</div>`;

    const precomputed = await getPrecomputed(CASE_NAME);
    if (!precomputed) {
      panels.innerHTML = `<div style="opacity:.8;">No precomputed data for "${CASE_NAME}"</div>`;
      return;
    }

    const ts = SETTINGS.timescale || "3M";
    const tsData = precomputed.timescales?.[ts];
    if (!tsData || !tsData.analysis) {
      panels.innerHTML = `<div style="opacity:.8;">No analysis data for timescale "${ts}"</div>`;
      return;
    }

    const result = tsData.analysis[state.active];
    if (!result) {
      panels.innerHTML = `<div style="opacity:.8;">No "${state.active}" data</div>`;
      return;
    }

    panels.innerHTML = "";
    const { title, metrics, series, notes } = result;

    const top = document.createElement("div");
    if (title) {
      const h = document.createElement("h4");
      h.textContent = `${title} — ${CASE_NAME}`;
      h.style.margin = "0 0 10px 0";
      top.appendChild(h);
    }
    if (metrics) top.appendChild(renderMetrics(metrics));
    panels.appendChild(top);

    if (Array.isArray(series)) {
      series.forEach(s => panels.appendChild(renderChart(s)));
    }
    if (notes) {
      const p = document.createElement("p");
      p.style.opacity = "0.85";
      p.style.marginTop = "8px";
      p.textContent = notes;
      panels.appendChild(p);
    }
  }

  // =========================
  // Render helpers
  // =========================
  function renderMetrics(metrics) {
    const wrap = document.createElement("div");
    wrap.style.display = "grid";
    wrap.style.gridTemplateColumns = "repeat(auto-fit,minmax(160px,1fr))";
    wrap.style.gap = "8px";
    Object.entries(metrics).forEach(([k, v]) => {
      const b = document.createElement("div");
      b.style.padding = "8px";
      b.style.border = "1px solid rgba(255,255,255,0.12)";
      b.style.borderRadius = "8px";
      b.style.background = "rgba(255,255,255,0.03)";
      b.innerHTML = `<div style="opacity:.7;font-size:12px;margin-bottom:4px;">${k}</div>
                     <div style="font-weight:600;">${Number.isFinite(v) ? num(v) : v}</div>`;
      wrap.appendChild(b);
    });
    return wrap;
  }

  function renderChart({ name, lines }) {
    const box = document.createElement("div");
    box.style.marginTop = "10px";
    const label = document.createElement("div");
    label.textContent = name || "Chart";
    label.style.opacity = "0.9";
    label.style.margin = "4px 0 6px";
    box.appendChild(label);

    const canvas = document.createElement("canvas");
    canvas.style.width = "100%";
    canvas.style.height = "280px";
    box.appendChild(canvas);

    // Size canvas backing buffer to match actual CSS size × devicePixelRatio
    const dpr = window.devicePixelRatio || 1;
    // Use requestAnimationFrame to ensure layout is resolved
    requestAnimationFrame(() => {
      const rect = canvas.getBoundingClientRect();
      const W = Math.round(rect.width);
      const H = Math.round(rect.height);
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      function yToPx(y, lo, hi) {
        const t = (y - lo) / Math.max(1e-9, (hi - lo));
        return 16 + (1 - t) * (H - 40);
      }

      const normalizedLines = (lines || []).map(L => {
        const pts = Array.isArray(L.points)
          ? L.points.map(p => Array.isArray(p) ? { x: p[0], y: p[1] } : p)
          : [];
        return { ...L, points: pts };
      });

      let lo = +Infinity, hi = -Infinity;
      normalizedLines.forEach(L => L.points.forEach(p => { lo = Math.min(lo, p.y); hi = Math.max(hi, p.y); }));
      if (!isFinite(lo) || !isFinite(hi) || lo === hi) { lo = 0; hi = 1; }
      const pad = 0.08 * (hi - lo); lo -= pad; hi += pad;

      ctx.fillStyle = "#0b0f14"; ctx.fillRect(0,0,W,H);
      ctx.strokeStyle = "rgba(255,255,255,.12)";
      ctx.beginPath(); ctx.moveTo(56, 16); ctx.lineTo(56, H-24); ctx.lineTo(W-12, H-24); ctx.stroke();

      ctx.fillStyle = "rgba(255,255,255,.7)";
      ctx.font = "12px system-ui, -apple-system, Segoe UI, Roboto, Arial";
      ctx.textAlign = "right"; ctx.textBaseline = "middle";
      for (let i=0;i<=4;i++){
        const v = lo + (i*(hi-lo))/4;
        const py = yToPx(v, lo, hi);
        ctx.strokeStyle = "rgba(255,255,255,.06)";
        ctx.beginPath(); ctx.moveTo(56, py); ctx.lineTo(W-12, py); ctx.stroke();
        ctx.fillText(num(v), 52, py);
      }

      normalizedLines.forEach((L, idx) => {
        ctx.strokeStyle = L.color || ["#38bdf8","#ef4444","#a78bfa","#22c55e"][idx%4];
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        L.points.forEach((p, i) => {
          const px = 56 + p.x * (W - 68);
          const py = yToPx(p.y, lo, hi);
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        });
        ctx.stroke();
      });
    });

    return box;
  }

  // =========================
  // Utils
  // =========================
  function num(v) { return Number.isFinite(v) ? (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(3)) : String(v); }

  function styleTab(btn, active) {
    btn.style.padding = "6px 10px";
    btn.style.fontSize = "12px";
    btn.style.borderRadius = "8px";
    btn.style.border = "1px solid rgba(255,255,255,0.15)";
    btn.style.cursor = "pointer";
    btn.style.background = active ? "rgba(56,189,248,0.18)" : "transparent";
    btn.style.color = "white";
  }
});
