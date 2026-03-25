// case_ev.js
import {
  configureCatalogPaths,
  configurePricePaths,
  loadCaseData,
  getVariantSeries,
  getPrecomputed,
  WEARS as LOADER_WEARS,
  ST_FLAGS as LOADER_ST_FLAGS,
  getCasePriceSeries as loaderCasePriceSeries,
  listCases,
} from "./data_loader.js";

document.addEventListener("DOMContentLoaded", () => {
  // ---------------- Global settings ----------------
  const SETTINGS = {
    timescale: "3M",
    interval: "1d",
  };
  const TIME_SCALES = ["1W", "1M", "3M", "6M", "1Y", "ALL"];
  const INTERVALS = ["1h", "4h", "1d", "1w"];

  window.currentCaseName = window.currentCaseName || "Chroma 2";
  let CASE_NAME = window.currentCaseName;

  configureCatalogPaths({ root: "./data/catalogues" });
  configurePricePaths({ root: "./data/precomputed" });

  const subscribers = [];

  const ST_PROB = 0.1;
  const DEFAULT_FLOAT_RANGE = { fmin: 0.06, fmax: 0.8 };
  const WEAR_BUCKETS = [
    { wear: "FN", range: [0.0, 0.07] },
    { wear: "MW", range: [0.07, 0.15] },
    { wear: "FT", range: [0.15, 0.38] },
    { wear: "WW", range: [0.38, 0.45] },
    { wear: "BS", range: [0.45, 1.0] },
  ];

  // Colors
  const SERIES_COLORS = [
    "rgb(56, 189, 248)", // ST • FN
    "rgb(167, 139, 250)", // ST • MW
    "rgb(16, 185, 129)", // ST • FT
    "rgb(148, 163, 184)", // ST • WW
    "rgb(245, 158, 11)", // ST • BS
    "rgb(34, 197, 94)", // FN
    "rgb(234, 179, 8)", // MW
    "rgb(239, 68, 68)", // FT
    "rgb(244, 114, 182)", // WW
    "rgb(88, 166, 255)", // BS
  ];
  const CASE_COLORS = {
    ev: "rgb(56, 189, 248)",
    case: "rgb(239, 68, 68)",
  };

  // Rarity map used for case EV weighting
  const RARITY_TO_PROB = {
    "Mil-Spec Grade": 0.7992,
    Restricted: 0.1598,
    Classified: 0.032,
    Covert: 0.0064,
    "Exceedingly Rare": 0.0026,
  };

  const WEARS = LOADER_WEARS;
  const ST_FLAGS = LOADER_ST_FLAGS;

  let ALL_ITEMS = [];
  let SKINS_WITH_RARITY = []; // [{ Name, Rarity, Kind: 'skin'|'knife'|'glove', allowST, p }]

  const KNIFE_KEYWORDS = [
    "Bayonet",
    "M9 Bayonet",
    "Karambit",
    "Flip Knife",
    "Gut Knife",
    "Huntsman",
    "Falchion",
    "Bowie",
    "Butterfly",
    "Shadow Daggers",
    "Navaja",
    "Stiletto",
    "Talon",
    "Ursus",
    "Classic Knife",
    "Paracord Knife",
    "Survival Knife",
    "Nomad Knife",
    "Skeleton Knife",
    "Kukri",
    "Kukri Knife",
  ];

  // ---------- tiny helpers (local; parallel to loader’s versions) ----------
  function slugifyFilename(name) {
    const s = (name || "")
      .trim()
      .replace(/[^\w\s&-]/g, "")
      .replace(/\s+/g, "_");
    return `${s}.json`;
  }
  function joinUrl(...parts) {
    return parts
      .filter(Boolean)
      .map((p, i) =>
        i === 0
          ? String(p).replace(/\/+$/, "")
          : String(p).replace(/^\/+|\/+$/g, "")
      )
      .join("/");
  }
  async function fetchJSON(url) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }
  function genCollectionFilenameCandidates(rootCollectionsDir, collectionName) {
    const raw = (collectionName || "").trim();
    const stem = raw.replace(/\s*Collection\s*$/i, "").trim();
    const forms = new Set();
    if (stem) {
      forms.add(stem);
      forms.add(stem.replace(/^the\s+/i, "").trim());
      if (!/^the\s/i.test(stem)) forms.add(`The ${stem}`);
    }
    const ampVariants = (s) => [s, s.replace(/&/g, "and"), s.replace(/&/g, "")];
    const candidates = [];
    for (const f of forms) {
      for (const v of ampVariants(f)) {
        candidates.push(joinUrl(rootCollectionsDir, slugifyFilename(v)));
      }
    }
    return [...new Set(candidates)];
  }
  function isGlovePackLabel(label) {
    if (!label) return false;
    const s = label.toLowerCase();
    return ["glove", "gloves", "broken fang", "clutch"].some((k) =>
      s.includes(k)
    );
  }
  function normalizeGlovePack(extraordinaryItems) {
    if (!isGlovePackLabel(extraordinaryItems)) return null;
    const s = extraordinaryItems.toLowerCase();
    if (s.includes("broken") && s.includes("fang")) return "Broken_Fang.json";
    if (s.includes("clutch")) return "Clutch.json";
    if (s.includes("glove")) return "Glove.json";
    return null;
  }
  function normalizeRarity(r) {
    if (!r) return null;
    const s = r.trim().toLowerCase();
    if (s.startsWith("mil-spec")) return "Mil-Spec Grade";
    if (s.startsWith("restricted")) return "Restricted";
    if (s.startsWith("classified")) return "Classified";
    if (s.startsWith("covert")) return "Covert";
    if (s.includes("exceed")) return "Exceedingly Rare";
    return r;
  }
  function isKnifeName(name) {
    return KNIFE_KEYWORDS.some((k) =>
      name.toLowerCase().startsWith(k.toLowerCase() + " ")
    );
  }
  function isGloveName(name) {
    return /glove(s)?/i.test(name);
  }

  // ---------- init ----------
  init();

  async function init() {
    await reloadData(); // populates ALL_ITEMS and SKINS_WITH_RARITY
    initCaseChart();
    initCaseTitleDropdown();

    const { skins, special } = splitItemsIntoSkinsAndSpecial(ALL_ITEMS);
    buildItemCards(skins, "skins-grid");
    buildItemCards(special, "special-grid");

    subscribers.forEach((fn) => fn());
  }

  async function reloadData() {
    // Load precomputed data first (cached by data_loader)
    _precomputedData = await getPrecomputed(CASE_NAME);

    const { items, errors } = await loadCaseData(CASE_NAME, {
      settings: SETTINGS,
    });
    ALL_ITEMS = items || [];
    if (errors && errors.length) {
      console.warn("Catalog load warnings:\n" + errors.join("\n"));
    }

    // Build rarity map from precomputed data or collection JSON
    if (_precomputedData) {
      // Use rarity info from precomputed data
      SKINS_WITH_RARITY = ALL_ITEMS.map(name => {
        const itemData = _precomputedData.items?.[name];
        const rarity = itemData?.rarity || "Restricted";
        const kind = itemData?.kind || "skin";
        const allowST = itemData?.allow_st !== false;
        return { Name: name, Rarity: rarity, Kind: kind, allowST, p: 0 };
      });

      // Compute per-tier counts and assign per-item probability
      const tierCounts = SKINS_WITH_RARITY.reduce((acc, x) => {
        acc[x.Rarity] = (acc[x.Rarity] || 0) + 1;
        return acc;
      }, {});
      SKINS_WITH_RARITY = SKINS_WITH_RARITY.map(x => {
        const pr = RARITY_TO_PROB[x.Rarity] || 0;
        const nTier = tierCounts[x.Rarity] || 1;
        return { ...x, p: pr / nTier };
      });
      return;
    }

    // Fallback: Build rarity map from actual collection JSON
    const ROOT = "./../data/catalogues";
    const caseJsonUrl = joinUrl(ROOT, "cases", slugifyFilename(CASE_NAME));
    const caseJson = await fetchJSON(caseJsonUrl);
    if (!caseJson) {
      console.warn("Missing case JSON:", caseJsonUrl);
      // Fallback: tag everything restricted with zero prob (EV will be flat/zero)
      SKINS_WITH_RARITY = ALL_ITEMS.map((n) => ({
        Name: n,
        Rarity: "Restricted",
        Kind: "skin",
        allowST: true,
        p: 0,
      }));
      return;
    }

    // 1) Load collection JSON and pull skins with real rarities
    const collectionName = (caseJson.Collection || "").trim();
    let collectionSkins = [];
    if (collectionName) {
      const candidates = genCollectionFilenameCandidates(
        joinUrl(ROOT, "collections"),
        collectionName
      );
      let collectionJson = null;
      for (const url of candidates) {
        collectionJson = await fetchJSON(url);
        if (collectionJson) break;
      }
      if (!collectionJson) {
        console.warn(
          "Could not locate collection JSON for",
          collectionName,
          "tried:",
          candidates
        );
      } else {
        collectionSkins = (collectionJson.Skins || []).map((s) => {
          const nm = `${(s.Weapon || "").trim()} ${(
            s.Name || ""
          ).trim()}`.trim();
          return {
            Name: nm,
            Rarity: normalizeRarity(s.Rarity),
            Kind: "skin",
            allowST: true,
          };
        });
      }
    }

    // 2) Specials (knives/gloves) are already expanded by loader into ALL_ITEMS.
    //    Tag them and set "Exceedingly Rare" rarity for prob purposes.
    const specials = ALL_ITEMS.filter(
      (n) => isKnifeName(n) || isGloveName(n)
    ).map((n) => ({
      Name: n,
      Rarity: "Exceedingly Rare",
      Kind: isGloveName(n) ? "glove" : "knife",
      allowST: isGloveName(n) ? false : true, // gloves: no StatTrak
    }));

    // 3) Merge: keep only items that exist in ALL_ITEMS (defensive)
    const allByName = new Set(ALL_ITEMS);
    const skinsFiltered = collectionSkins.filter((x) => allByName.has(x.Name));
    const merged = [...skinsFiltered, ...specials];

    // 4) Compute per-tier counts and assign per-item probability p = p(tier)/N_tier
    const tierCounts = merged.reduce((acc, x) => {
      const key = x.Rarity || "Restricted";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    SKINS_WITH_RARITY = merged.map((x) => {
      const tier = x.Rarity || "Restricted";
      const pr = RARITY_TO_PROB[tier] || 0;
      const nTier = tierCounts[tier] || 1;
      return { ...x, p: pr / nTier };
    });
  }

  function refreshAll() {
    reloadData().then(() => {
      const h2 = document.querySelector(".case-banner h2 .case-title-button");
      if (h2) h2.textContent = CASE_NAME;

      const { skins, special } = splitItemsIntoSkinsAndSpecial(ALL_ITEMS);
      buildItemCards(skins, "skins-grid");
      buildItemCards(special, "special-grid");
      subscribers.forEach((fn) => fn());
    });
  }

  function splitItemsIntoSkinsAndSpecial(items) {
    const special = [];
    const skins = [];
    for (const name of items || []) {
      const isGloves = /glove(s)?/i.test(name);
      const isKnife = KNIFE_KEYWORDS.some((k) =>
        name.toLowerCase().startsWith(k.toLowerCase() + " ")
      );
      (isGloves || isKnife ? special : skins).push(name);
    }
    return { skins, special };
  }

  // ===============================
  // Case title dropdown
  // ===============================
  function initCaseTitleDropdown() {
    const host = document.querySelector(".case-banner h2");
    if (!host) return;

    // Make clickable label
    host.innerHTML = "";
    const btn = document.createElement("span");
    btn.className = "case-title-button";
    btn.textContent = CASE_NAME;
    btn.style.cursor = "pointer";
    btn.style.textDecoration = "underline";
    btn.style.textUnderlineOffset = "3px";
    btn.style.textDecorationThickness = "1.5px";
    btn.style.color = "rgb(56, 189, 248)";
    btn.title = "Click to switch case";

    // dropdown container
    const menu = document.createElement("div");
    menu.style.position = "absolute";
    menu.style.background = "#0b0f14";
    menu.style.border = "1px solid rgba(255,255,255,0.15)";
    menu.style.borderRadius = "8px";
    menu.style.boxShadow = "0 8px 24px rgba(0,0,0,0.35)";
    menu.style.padding = "6px";
    menu.style.display = "none";
    menu.style.maxHeight = "320px";
    menu.style.overflow = "auto";
    menu.style.zIndex = "999";

    const banner = document.querySelector(".case-banner");
    banner.style.position = "relative";
    menu.style.top = "36px";
    menu.style.left = "12px";

    let open = false;
    const close = () => {
      open = false;
      menu.style.display = "none";
    };
    const toggle = async () => {
      if (open) return close();
      open = true;
      menu.style.display = "block";

      menu.innerHTML = `<div style="padding:8px;opacity:.8;">Loading cases…</div>`;
      const names = await listCases();
      if (!names.length) {
        menu.innerHTML = `<div style="padding:8px;opacity:.8;">No cases found</div>`;
        return;
      }
      menu.innerHTML = "";
      names.forEach((n) => {
        const item = document.createElement("div");
        item.textContent = n;
        item.style.padding = "6px 10px";
        item.style.borderRadius = "6px";
        item.style.cursor = "pointer";
        item.style.whiteSpace = "nowrap";
        item.addEventListener(
          "mouseenter",
          () => (item.style.background = "rgba(56,189,248,0.15)")
        );
        item.addEventListener(
          "mouseleave",
          () => (item.style.background = "transparent")
        );
        item.addEventListener("click", () => {
          close();
          if (n !== CASE_NAME) {
            CASE_NAME = n;
            window.currentCaseName = n;
            window.dispatchEvent(
              new CustomEvent("case-selected", { detail: { caseName: n } })
            );
            refreshAll();
          }
        });
        menu.appendChild(item);
      });
    };

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggle();
    });
    document.addEventListener("click", () => {
      if (open) close();
    });

    host.appendChild(btn);
    host.appendChild(menu);
  }

  // ===============================
  // Case chart (top banner) section
  // ===============================
  function initCaseChart() {
    const host = document.querySelector('.chart[data-chart-id="case-chart"]');
    if (!host) return;

    // Controls
    const controls = document.createElement("div");
    controls.style.display = "flex";
    controls.style.flexWrap = "wrap";
    controls.style.gap = "8px";
    controls.style.justifyContent = "space-between";
    controls.style.alignItems = "center";
    controls.style.marginBottom = "8px";

    const left = document.createElement("div");
    left.style.display = "flex";
    left.style.gap = "12px";
    left.style.flexWrap = "wrap";

    const tsGroup = makeButtonGroup(
      "Timescale",
      ["1W", "1M", "3M", "6M", "1Y", "ALL"],
      SETTINGS.timescale,
      (val) => {
        SETTINGS.timescale = val;
        window.dispatchEvent(
          new CustomEvent("case-settings-changed", {
            detail: {
              timescale: SETTINGS.timescale,
              interval: SETTINGS.interval,
            },
          })
        );
        refreshAll();
      }
    );
    const ivGroup = makeButtonGroup(
      "Interval",
      ["1h", "4h", "1d", "1w"],
      SETTINGS.interval,
      (val) => {
        SETTINGS.interval = val;
        window.dispatchEvent(
          new CustomEvent("case-settings-changed", {
            detail: {
              timescale: SETTINGS.timescale,
              interval: SETTINGS.interval,
            },
          })
        );
        refreshAll();
      }
    );
    left.appendChild(tsGroup.container);
    left.appendChild(ivGroup.container);

    // Include key toggle (applies +$2.49 to case price only)
    const includeKey = makeSimpleToggle("Include Key ($2.49)");
    includeKey.input.checked = true;
    includeKey.sync();

    controls.appendChild(left);
    controls.appendChild(includeKey.container);

    // Canvas (DPI-aware, responsive)
    const canvas = document.createElement("canvas");
    canvas.style.width = "100%";
    canvas.style.height = "360px";
    canvas.style.display = "block";

    host.innerHTML = "";
    host.appendChild(controls);
    host.appendChild(canvas);

    const caseChart = createCaseChart(canvas, CASE_COLORS, SETTINGS, {
      getData: async ({ settings }) => {
        const precomputed = await getPrecomputed(CASE_NAME);
        const ts = settings.timescale || "3M";
        const tsData = precomputed?.timescales?.[ts];

        const priceArr = tsData?.case_price || [];
        const evArr = tsData?.ev || [];
        const price = priceArr.map(([x, y]) => ({ x, y }));
        const ev = evArr.map(([x, y]) => ({ x, y }));

        // Align lengths
        const n = Math.min(ev.length, price.length);
        return { ev: ev.slice(0, n), price: price.slice(0, n) };
      },
    });

    includeKey.input.addEventListener("change", () => {
      caseChart.setIncludeKey(includeKey.input.checked);
      caseChart.render();
    });

    subscribers.push(() => {
      caseChart.updateSettings(SETTINGS);
    });

    caseChart.setIncludeKey(includeKey.input.checked);
    caseChart.render();

    window.addEventListener("case-selected", () => {
      caseChart.updateSettings(SETTINGS);
    });
  }

  function buildItemCards(items, gridId) {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    grid.innerHTML = "";

    // helper: detect gloves by name
    const isGloveName = (name) => /glove(s)?/i.test(name);

    for (const skinName of items) {
      // --- per-item series order ---
      // gloves: NO StatTrak; others: ST + non-ST
      const SERIES_ORDER = isGloveName(skinName)
        ? [...WEARS.map((w) => ({ wear: w, st: false }))]
        : [
            ...WEARS.map((w) => ({ wear: w, st: true })),
            ...WEARS.map((w) => ({ wear: w, st: false })),
          ];

      const card = document.createElement("div");
      card.className = "card";
      card.style.position = "relative";

      const header = document.createElement("div");
      header.style.display = "flex";
      header.style.alignItems = "center";
      header.style.justifyContent = "space-between";

      const h3 = document.createElement("h3");
      h3.textContent = skinName;

      const toggleWrap = makeSimpleToggle();
      header.appendChild(h3);
      header.appendChild(toggleWrap.container);
      card.appendChild(header);

      const chartWrap = document.createElement("div");
      chartWrap.className = "chart";

      const canvas = document.createElement("canvas");
      canvas.style.width = "100%";
      canvas.style.height = "260px";
      chartWrap.appendChild(canvas);

      const legend = document.createElement("div");
      legend.className = "legend";

      const chart = createSkinChart(
        canvas,
        SERIES_COLORS,
        skinName,
        SERIES_ORDER,
        SETTINGS
      );

      SERIES_ORDER.forEach((s, idx) => {
        const item = document.createElement("div");
        item.className = "legend-item";
        item.dataset.idx = String(idx);
        item.style.cursor = "pointer";
        item.style.userSelect = "none";
        item.style.display = "flex";
        item.style.alignItems = "center";
        item.style.gap = "6px";
        item.style.padding = "2px 6px";
        item.style.borderRadius = "6px";

        const dot = document.createElement("span");
        dot.className = "legend-dot";
        dot.style.display = "inline-block";
        dot.style.width = "10px";
        dot.style.height = "10px";
        dot.style.borderRadius = "50%";
        dot.style.background = SERIES_COLORS[idx % SERIES_COLORS.length];

        const label = document.createElement("span");
        label.textContent = s.st ? `${s.wear} • ST` : `${s.wear}`;

        item.appendChild(dot);
        item.appendChild(label);
        legend.appendChild(item);

        item.addEventListener("click", () => {
          const enabled = chart.toggleSeries(idx);
          item.style.opacity = enabled ? 1.0 : 0.45;
        });
      });

      chartWrap.appendChild(legend);
      card.appendChild(chartWrap);
      grid.appendChild(card);

      toggleWrap.input.addEventListener("change", () => {
        const simple = toggleWrap.input.checked;
        legend.style.display = simple ? "none" : "";
        chart.setSimpleView(simple);
      });

      window.addEventListener("case-selected", () =>
        chart.updateSettings(SETTINGS)
      );

      chart.render();
    }
  }

  // -------------------------------
  // Skin chart (per item, uses loader data)
  // -------------------------------
  function createSkinChart(canvas, colors, skinName, seriesOrder, settings) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;

    function sizeCanvas() {
      const rect = canvas.getBoundingClientRect();
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { W: w, H: h };
    }
    let { W, H } = sizeCanvas();

    let currSettings = { ...settings };
    let simpleView = false;

    function fetchSeries({ wear, st }, n) {
      const s = getVariantSeries({ skin: skinName, wear, st });
      if (s && s.length) return s;
      return new Array(n).fill(0).map((_, i) => ({ x: i / (n - 1), y: 0 }));
    }

    function buildSeries() {
      const n = choosePointCount(currSettings);
      return seriesOrder.map((cfg) => fetchSeries(cfg, n));
    }

    function buildAverage() {
      const n = choosePointCount(currSettings) * 2;
      // detect if this item is a glove to disable ST in averaging
      const swr = SKINS_WITH_RARITY.find((x) => x.Name === skinName);
      const allowST = swr ? !!swr.allowST : true;
      return getAverageUSD({
        skin: skinName,
        n,
        settings: currSettings,
        allowST,
      });
    }

    let series = buildSeries();
    let averageSeries = buildAverage();
    const enabled = new Array(series.length).fill(true);

    function clear() {
      ctx.fillStyle = "#0b0f14";
      ctx.fillRect(0, 0, W, H);
    }

    function computeYRange() {
      let ymin = Infinity,
        ymax = -Infinity;

      if (simpleView) {
        for (const p of averageSeries) {
          ymin = Math.min(ymin, p.y);
          ymax = Math.max(ymax, p.y);
        }
      } else {
        for (let s = 0; s < series.length; s++) {
          if (!enabled[s]) continue;
          for (const p of series[s]) {
            ymin = Math.min(ymin, p.y);
            ymax = Math.max(ymax, p.y);
          }
        }
      }

      if (!isFinite(ymin) || !isFinite(ymax)) {
        ymin = 0;
        ymax = 1;
      }
      if (ymax === ymin) ymax = ymin + 1;
      const pad = (ymax - ymin) * 0.08;
      return { ymin: ymin - pad, ymax: ymax + pad };
    }

    function yToPx(y, ymin, ymax) {
      const ih = H - 50;
      const t = (y - ymin) / (ymax - ymin);
      return 20 + (1 - t) * ih;
    }

    function axes(ymin, ymax) {
      ctx.strokeStyle = "rgba(255,255,255,0.15)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(50, 20);
      ctx.lineTo(50, H - 30);
      ctx.lineTo(W - 20, H - 30);
      ctx.stroke();

      ctx.strokeStyle = "rgba(255,255,255,0.07)";
      ctx.fillStyle = "rgba(255,255,255,0.6)";
      ctx.font = "12px system-ui, -apple-system, Segoe UI, Roboto, Arial";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";

      for (let i = 0; i <= 4; i++) {
        const yv = ymin + (i * (ymax - ymin)) / 4;
        const py = yToPx(yv, ymin, ymax);
        ctx.beginPath();
        ctx.moveTo(50, py);
        ctx.lineTo(W - 20, py);
        ctx.stroke();
        ctx.fillText(`$${yv.toFixed(2)}`, 45, py);
      }
    }

    function drawMultiSeries(ymin, ymax) {
      for (let s = 0; s < series.length; s++) {
        if (!enabled[s]) continue;
        ctx.strokeStyle = colors[s % colors.length];
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        series[s].forEach((p, i) => {
          const px = 50 + p.x * (W - 70);
          const py = yToPx(p.y, ymin, ymax);
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.stroke();
      }
    }

    function drawAverageSeries(ymin, ymax) {
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 2.0;
      ctx.beginPath();
      averageSeries.forEach((p, i) => {
        const px = 50 + p.x * (W - 70);
        const py = yToPx(p.y, ymin, ymax);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }

    function render() {
      ({ W, H } = sizeCanvas());
      clear();
      const { ymin, ymax } = computeYRange();
      axes(ymin, ymax);
      if (simpleView) drawAverageSeries(ymin, ymax);
      else drawMultiSeries(ymin, ymax);
    }

    function toggleSeries(idx) {
      if (simpleView) return true;
      enabled[idx] = !enabled[idx];
      render();
      return enabled[idx];
    }

    function setSimpleView(on) {
      simpleView = !!on;
      render();
    }

    function updateSettings(newSettings) {
      currSettings = { ...newSettings };
      series = buildSeries();
      averageSeries = buildAverage();
      render();
    }

    return { render, toggleSeries, setSimpleView, updateSettings };
  }

  // -------------------------------
  // Case chart (two lines + include key)
  // -------------------------------
  function createCaseChart(canvas, colors, settings, providers) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;

    function sizeCanvas() {
      const rect = canvas.getBoundingClientRect();
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { W: w, H: h };
    }
    let { W, H } = sizeCanvas();

    let currSettings = { ...settings };
    let includeKey = true;
    let data = { ev: [], price: [] };

    // Initial load
    (async () => {
      data = await providers.getData({ settings: currSettings });
      render();
    })();

    function setIncludeKey(on) {
      includeKey = !!on;
    }
    async function updateSettings(newSettings) {
      currSettings = { ...newSettings };
      data = await providers.getData({ settings: currSettings });
      render();
    }

    function clear() {
      ctx.fillStyle = "#0b0f14";
      ctx.fillRect(0, 0, W, H);
    }

    function computeYRange() {
      const KEY = 2.49;
      const priceShift = includeKey ? KEY : 0;
      let ymin = Infinity,
        ymax = -Infinity;

      data.ev.forEach((p) => {
        ymin = Math.min(ymin, p.y);
        ymax = Math.max(ymax, p.y);
      });
      data.price.forEach((p) => {
        ymin = Math.min(ymin, p.y + priceShift);
        ymax = Math.max(ymax, p.y + priceShift);
      });

      if (!isFinite(ymin) || !isFinite(ymax)) {
        ymin = 0;
        ymax = 1;
      }
      if (ymax === ymin) ymax = ymin + 1;
      const pad = (ymax - ymin) * 0.08;
      return { ymin: ymin - pad, ymax: ymax + pad };
    }

    function yToPx(y, ymin, ymax) {
      const ih = H - 50;
      const t = (y - ymin) / (ymax - ymin);
      return 20 + (1 - t) * ih;
    }

    function axes(ymin, ymax) {
      ctx.strokeStyle = "rgba(255,255,255,0.15)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(60, 20);
      ctx.lineTo(60, H - 30);
      ctx.lineTo(W - 20, H - 30);
      ctx.stroke();

      ctx.strokeStyle = "rgba(255,255,255,0.07)";
      ctx.fillStyle = "rgba(255,255,255,0.7)";
      ctx.font = "12px system-ui, -apple-system, Segoe UI, Roboto, Arial";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";

      for (let i = 0; i <= 4; i++) {
        const yv = ymin + (i * (ymax - ymin)) / 4;
        const py = yToPx(yv, ymin, ymax);
        ctx.beginPath();
        ctx.moveTo(60, py);
        ctx.lineTo(W - 20, py);
        ctx.stroke();
        ctx.fillText(`$${yv.toFixed(2)}`, 55, py);
      }
    }

    function drawLine(pts, color, ymin, ymax) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      pts.forEach((p, i) => {
        const px = 60 + p.x * (W - 80);
        const py = yToPx(p.y, ymin, ymax);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }

    function render() {
      ({ W, H } = sizeCanvas());
      clear();
      const KEY = 2.49;
      const priceShift = includeKey ? KEY : 0;
      const shifted = data.price.map((p) => ({ x: p.x, y: p.y + priceShift }));
      const { ymin, ymax } = computeYRange();
      axes(ymin, ymax);
      drawLine(shifted, colors.case, ymin, ymax);
      drawLine(data.ev, colors.ev, ymin, ymax);
    }

    return { render, setIncludeKey, updateSettings };
  }

  // ===========================
  // Helpers (UI + math + series)
  // ===========================
  function makeButtonGroup(title, options, active, onChange) {
    const container = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = title + ":";
    label.style.fontSize = "12px";
    label.style.opacity = "0.85";
    label.style.marginRight = "6px";

    const group = document.createElement("div");
    group.style.display = "inline-flex";
    group.style.gap = "6px";

    function styleBtn(btn, isActive) {
      btn.style.padding = "4px 8px";
      btn.style.borderRadius = "8px";
      btn.style.cursor = "pointer";
      btn.style.border = "1px solid rgba(255,255,255,0.15)";
      btn.style.background = isActive ? "rgba(56,189,248,0.15)" : "transparent";
      btn.style.color = "white";
      btn.style.fontSize = "12px";
    }

    const buttons = options.map((opt) => {
      const b = document.createElement("button");
      b.textContent = opt;
      styleBtn(b, opt === active);
      b.addEventListener("click", () => {
        buttons.forEach((x) => styleBtn(x, false));
        styleBtn(b, true);
        onChange(opt);
      });
      group.appendChild(b);
      return b;
    });

    container.appendChild(label);
    container.appendChild(group);
    return { container };
  }

  function makeSimpleToggle(text = "Simple View") {
    const container = document.createElement("div");
    container.style.display = "flex";
    container.style.alignItems = "center";
    container.style.gap = "8px";

    const label = document.createElement("span");
    label.textContent = text;
    label.style.fontSize = "12px";
    label.style.opacity = "0.9";

    const track = document.createElement("div");
    track.style.width = "42px";
    track.style.height = "22px";
    track.style.borderRadius = "999px";
    track.style.background = "rgba(255,255,255,0.2)";
    track.style.position = "relative";
    track.style.cursor = "pointer";

    const thumb = document.createElement("div");
    thumb.style.width = "18px";
    thumb.style.height = "18px";
    thumb.style.borderRadius = "50%";
    thumb.style.background = "white";
    thumb.style.position = "absolute";
    thumb.style.top = "2px";
    thumb.style.left = "2px";
    thumb.style.transition = "transform 150ms ease, background 150ms ease";

    track.appendChild(thumb);

    const input = document.createElement("input");
    input.type = "checkbox";
    input.style.display = "none";

    function sync() {
      if (input.checked) {
        track.style.background = "rgb(34, 197, 94)";
        thumb.style.transform = "translateX(20px)";
      } else {
        track.style.background = "rgba(255,255,255,0.2)";
        thumb.style.transform = "translateX(0)";
      }
    }
    track.addEventListener("click", () => {
      input.checked = !input.checked;
      sync();
      input.dispatchEvent(new Event("change"));
    });

    container.appendChild(label);
    container.appendChild(track);
    container.appendChild(input);

    return { container, input, sync };
  }

  function choosePointCount(settings) {
    const base =
      { "1h": 72, "4h": 48, "1d": 32, "1w": 24 }[settings.interval] || 32;
    const mult =
      { "1W": 0.5, "1M": 1, "3M": 1.5, "6M": 2, "1Y": 2.5, ALL: 3 }[
        settings.timescale
      ] || 1;
    return Math.max(12, Math.floor(base * mult));
  }

  // Cache for precomputed data (set during reloadData)
  let _precomputedData = null;

  function getAverageUSD({ skin, n: nHint = 48, settings, allowST = true }) {
    // Read from precomputed item averages
    const ts = settings.timescale || "3M";
    const itemData = _precomputedData?.items?.[skin];
    const avgArr = itemData?.average?.[ts];

    if (avgArr && avgArr.length) {
      return avgArr.map(([x, y]) => ({ x, y }));
    }

    // Fallback: build from variant series if precomputed average is missing
    let n = nHint;
    for (const wear of WEARS) {
      const s = getVariantSeries({ skin, wear, st: false });
      if (s && s.length > 0) { n = s.length; break; }
    }
    return new Array(n).fill(0).map((_, i) => ({ x: i / (n - 1), y: 0 }));
  }

  function getSkinsWithRarity() {
    // Already normalized + probability-assigned in reloadData()
    return SKINS_WITH_RARITY.slice();
  }

  function getCaseEVSeries({ settings, n: nHint, skinsWithRarity }) {
    // Read precomputed EV if available
    const ts = settings.timescale || "3M";
    const evArr = _precomputedData?.timescales?.[ts]?.ev;
    if (evArr && evArr.length) {
      return evArr.map(([x, y]) => ({ x, y }));
    }

    // Fallback: compute from variant series
    const n = nHint || 48;
    const out = new Array(n).fill(0).map((_, i) => ({ x: i / (n - 1), y: 0 }));
    for (const skin of skinsWithRarity) {
      const name = skin.Name || skin;
      const pItem = typeof skin.p === "number" ? skin.p : 0;
      if (pItem <= 0) continue;

      const avgUsd = getAverageUSD({
        skin: name,
        n,
        settings,
        allowST: !!skin.allowST,
      });
      for (let i = 0; i < Math.min(n, avgUsd.length); i++) out[i].y += pItem * avgUsd[i].y;
    }
    return smooth(out, 2);
  }

  function smooth(points, win = 3) {
    if (win <= 1) return points;
    const out = points.map((p) => ({ ...p }));
    for (let i = 0; i < points.length; i++) {
      let sum = 0,
        cnt = 0;
      for (let k = -win; k <= win; k++) {
        const j = i + k;
        if (j >= 0 && j < points.length) {
          sum += points[j].y;
          cnt++;
        }
      }
      out[i].y = sum / cnt;
    }
    return out;
  }
});
