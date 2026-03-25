// data_loader.js
// Loads precomputed JSON data for the dashboard.
// Each case has one JSON file with all timescales, items, and analysis precomputed.
//
// ------- Public API -------
//
// configureCatalogPaths({ root })
// configurePricePaths({ root })  — sets precomputed data path
// loadCaseData(caseName, { settings }) -> { items, variants, seriesByKey, errors, caseUrl }
// getVariantSeries({ skin, wear, st }) -> [{x,y}, ...]  (after loadCaseData)
// listVariants() -> [{ key, skin, wear, st }]           (after loadCaseData)
// getCasePriceSeries({ settings, caseName }) -> [{x,y}, ...]
// getPrecomputed(caseName) -> full precomputed JSON (for analysis.js)
// listCases() -> ["Chroma 2", "Spectrum 2", ...]
// WEARS, ST_FLAGS for convenience
//

// ==============================
// Configurable paths
// ==============================
const _paths = {
  root: "/data/catalogues",
  casesDir: "cases",
  collectionsDir: "collections",
  knivesDir: "knives",
  glovesDir: "gloves",
};

const _pricePaths = {
  root: "/data/precomputed",
};

export function configureCatalogPaths({ root, casesDir, collectionsDir, knivesDir, glovesDir } = {}) {
  if (root) _paths.root = root;
  if (casesDir) _paths.casesDir = casesDir;
  if (collectionsDir) _paths.collectionsDir = collectionsDir;
  if (knivesDir) _paths.knivesDir = knivesDir;
  if (glovesDir) _paths.glovesDir = glovesDir;
}

export function configurePricePaths({ root } = {}) {
  if (root) _pricePaths.root = root;
}

function joinUrl(...parts) {
  return parts
    .filter(Boolean)
    .map((p, i) => (i === 0 ? String(p).replace(/\/+$/,"") : String(p).replace(/^\/+|\/+$/g,"")))
    .join("/");
}

function slugifyFilename(name) {
  const s = (name || "")
    .trim()
    .replace(/[^\w\s&-]/g, "")
    .replace(/\s+/g, "_");
  return `${s}.json`;
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

async function fetchText(url) {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.text();
  } catch {
    return null;
  }
}

// ==============================
// Precomputed JSON cache
// ==============================
const _jsonCache = new Map();

async function fetchPrecomputed(caseName) {
  if (_jsonCache.has(caseName)) return _jsonCache.get(caseName);
  const slug = (caseName || "").trim().replace(/\s+/g, "_");
  const url = joinUrl(_pricePaths.root, `${slug}.json`);
  const data = await fetchJSON(url);
  if (data) _jsonCache.set(caseName, data);
  return data;
}

export async function getPrecomputed(caseName) {
  return fetchPrecomputed(caseName);
}

// ==============================
// Convert precomputed [[x,y],...] to [{x,y},...]
// ==============================
function arrayToPoints(arr) {
  if (!arr || !arr.length) return [];
  return arr.map(([x, y]) => ({ x, y }));
}

// ==============================
// Variant enumeration
// ==============================
export const WEARS    = ["FN", "MW", "FT", "WW", "BS"];
export const ST_FLAGS = [true, false];

function variantKey(skin, wear, st) {
  return `${skin} | ${wear} | ${st ? "ST" : "N"}`;
}

function enumerateVariants(skins) {
  const out = [];
  for (const skin of skins) {
    for (const wear of WEARS) {
      for (const st of ST_FLAGS) {
        out.push({ key: variantKey(skin, wear, st), skin, wear, st });
      }
    }
  }
  return out;
}

// ==============================
// Public store
// ==============================
const _store = {
  variants: [],
  seriesByKey: new Map(),
  items: [],
  errors: [],
  caseUrl: "",
};

export function listVariants() {
  return _store.variants.slice();
}

export function getVariantSeries({ skin, wear, st }) {
  return _store.seriesByKey.get(variantKey(skin, wear, st)) || null;
}

export async function loadCaseData(caseName, opts = {}) {
  const settings = opts.settings || { timescale: "3M", interval: "1d" };
  const ts = settings.timescale || "3M";

  const data = await fetchPrecomputed(caseName);
  if (!data) {
    _store.items = [];
    _store.errors = [`Failed to load precomputed data for "${caseName}"`];
    _store.variants = [];
    _store.seriesByKey = new Map();
    return {
      items: [],
      variants: [],
      seriesByKey: new Map(),
      errors: _store.errors.slice(),
      caseUrl: "",
    };
  }

  const caseUrl = joinUrl(_paths.root, _paths.casesDir, slugifyFilename(caseName));
  _store.items = Object.keys(data.items || {});
  _store.errors = data.warnings || [];
  _store.caseUrl = caseUrl;
  _store.variants = enumerateVariants(_store.items);
  _store.seriesByKey = new Map();

  // Populate series from precomputed data
  for (const v of _store.variants) {
    const itemData = data.items[v.skin];
    if (!itemData) continue;

    const wearData = itemData.wears?.[v.wear]?.[ts];
    if (wearData && wearData.length) {
      const points = arrayToPoints(wearData);
      // ST and non-ST share the same series (ST premium handled in EV)
      _store.seriesByKey.set(v.key, points);
    }
  }

  return {
    items: _store.items.slice(),
    variants: _store.variants.slice(),
    seriesByKey: _store.seriesByKey,
    errors: _store.errors.slice(),
    caseUrl: _store.caseUrl,
  };
}

// ==============================
// Case price series
// ==============================
export async function getCasePriceSeries({ n, settings = { timescale: "3M" }, caseName = "" } = {}) {
  const ts = settings.timescale || "3M";
  const data = await fetchPrecomputed(caseName);
  if (!data) return [{ x: 0, y: 0 }, { x: 1, y: 0 }];

  const series = data.timescales?.[ts]?.case_price;
  if (!series || !series.length) return [{ x: 0, y: 0 }, { x: 1, y: 0 }];

  return arrayToPoints(series);
}

// No-op for backwards compatibility
export function clearPriceCache() {}

// ==============================
// Case list (for dropdown)
// ==============================
export async function listCases() {
  const dirUrl = joinUrl(_paths.root, _paths.casesDir, "/");

  // 1) Try JSON index
  const idx = await fetchJSON(joinUrl(_paths.root, _paths.casesDir, "index.json"));
  if (idx) {
    const files = Array.isArray(idx)
      ? idx.map(v => (typeof v === "string" ? v : (v.file || v.name || "")))
      : [];
    return normalizeCaseNames(files.filter(f => f && /\.json$/i.test(f)));
  }

  const html = await fetchText(dirUrl);
  if (html) {
    const files = [];
    const re = /href="([^"]+\.json)"/gi;
    let m; while ((m = re.exec(html))) {
      const f = m[1].split("/").pop();
      if (f) files.push(f);
    }
    if (files.length) return normalizeCaseNames([...new Set(files)]);
  }

  return [];
}

function normalizeCaseNames(files) {
  return files
    .map(f => String(f).replace(/\.json$/i, ""))
    .map(base => base.replace(/_/g, " ").trim())
    .map(s => s.replace(/\s+/g, " "));
}
