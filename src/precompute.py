"""Precompute all case data into single JSON files for the dashboard.

Reads raw CSVs from data/prices/ and catalogue JSONs from data/catalogues/,
produces one JSON per case in data/precomputed/ containing:
- Case price series (per timescale)
- EV series (per timescale)
- Per-item per-wear price series (per timescale)
- All 6 analysis modules precomputed (per timescale)

Usage:
    python src/precompute.py
    python src/precompute.py --cases "Chroma 2" "Prisma"
    python src/precompute.py --output data/precomputed
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICES_DIR = ROOT / "data" / "prices"
CATALOGUES_DIR = ROOT / "data" / "catalogues"
OUTPUT_DIR = ROOT / "data" / "precomputed"

TIMESCALES = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365, "ALL": None}

# Valve official unboxing probabilities
RARITY_PROBS = {
    "Mil-Spec Grade": 0.7992,
    "Mil-spec": 0.7992,
    "Restricted": 0.1598,
    "Classified": 0.032,
    "Covert": 0.0064,
    "Exceedingly Rare": 0.0026,
}

# Wear buckets and default float range
WEAR_BUCKETS = [
    ("FN", "Factory New", 0.00, 0.07),
    ("MW", "Minimal Wear", 0.07, 0.15),
    ("FT", "Field-Tested", 0.15, 0.38),
    ("WW", "Well-Worn", 0.38, 0.45),
    ("BS", "Battle-Scarred", 0.45, 1.00),
]
WEAR_SHORT = {long: short for short, long, _, _ in WEAR_BUCKETS}
WEAR_LONG = {short: long for short, long, _, _ in WEAR_BUCKETS}
WEARS = ["FN", "MW", "FT", "WW", "BS"]

DEFAULT_FMIN, DEFAULT_FMAX = 0.06, 0.80
ST_PROB = 0.10


def compute_wear_probs(fmin=DEFAULT_FMIN, fmax=DEFAULT_FMAX):
    """Compute wear tier probabilities from float range overlap."""
    length = max(0, fmax - fmin)
    if length <= 0:
        return {"FN": 0.0135, "MW": 0.108, "FT": 0.311, "WW": 0.095, "BS": 0.473}
    raw = {}
    total = 0
    for short, _, lo, hi in WEAR_BUCKETS:
        overlap = max(0, min(fmax, hi) - max(fmin, lo))
        raw[short] = overlap
        total += overlap
    if total <= 0:
        return {"FN": 0.0135, "MW": 0.108, "FT": 0.311, "WW": 0.095, "BS": 0.473}
    return {w: raw[w] / total for w in WEARS}


WEAR_PROBS = compute_wear_probs()


# ── Catalogue loading ─────────────────────────────────────────────

def load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def slugify(name):
    s = re.sub(r"[^\w\s&-]", "", name.strip())
    return re.sub(r"\s+", "_", s)


def normalize_rarity(r):
    if not r:
        return None
    s = r.strip().lower()
    if s.startswith("mil-spec") or s.startswith("mil spec"):
        return "Mil-Spec Grade"
    if s.startswith("restricted"):
        return "Restricted"
    if s.startswith("classified"):
        return "Classified"
    if s.startswith("covert"):
        return "Covert"
    if "exceed" in s:
        return "Exceedingly Rare"
    # Try exact match from RARITY_PROBS
    for key in RARITY_PROBS:
        if key.lower() == s:
            return key
    return r


def find_collection_json(collection_name):
    """Try multiple filename variants to find the collection JSON."""
    stem = re.sub(r"\s*Collection\s*$", "", collection_name, flags=re.I).strip()
    forms = [stem]
    no_the = re.sub(r"^The\s+", "", stem, flags=re.I).strip()
    if no_the != stem:
        forms.append(no_the)
    if not stem.lower().startswith("the "):
        forms.append(f"The {stem}")

    for f in forms:
        for v in [f, f.replace("&", "and"), f.replace("&", "")]:
            path = CATALOGUES_DIR / "collections" / f"{slugify(v)}.json"
            data = load_json(path)
            if data:
                return data
    return None


KNIFE_PACK_HINTS = {
    "original": "Original", "chroma": "Chroma", "gamma": "Gamma",
    "spectrum": "Spectrum", "fracture": "Fracture", "horizon": "Horizon",
    "prisma": "Prisma", "prisma 2": "Prisma_2", "gamma 2": "Gamma_2",
    "chroma 2": "Chroma_2", "chroma 3": "Chroma_3", "spectrum 2": "Spectrum_2",
}


def find_knife_finishes(extraordinary_items):
    if not extraordinary_items:
        return []
    s = extraordinary_items.strip().lower()
    s = re.sub(r"\s+knives?$", "", s)
    s = s.replace("&", "and").strip()
    filename = KNIFE_PACK_HINTS.get(s)
    if not filename:
        filename = "_".join(w.capitalize() for w in s.split())
    path = CATALOGUES_DIR / "knives" / f"{filename}.json"
    data = load_json(path)
    if not data:
        return []
    finishes = []
    for lst in (data.get("Finishes") or {}).values():
        finishes.extend(lst or [])
    return finishes


def is_glove_label(label):
    if not label:
        return False
    s = label.lower()
    return any(k in s for k in ["glove", "gloves", "broken fang", "clutch"])


def find_glove_finishes(extraordinary_items):
    if not extraordinary_items:
        return {}
    s = extraordinary_items.lower()
    if "broken" in s and "fang" in s:
        filename = "Broken_Fang"
    elif "clutch" in s:
        filename = "Clutch"
    elif "glove" in s:
        filename = "Glove"
    else:
        return {}
    path = CATALOGUES_DIR / "gloves" / f"{filename}.json"
    data = load_json(path)
    if not data:
        return {}
    return data.get("Finishes") or {}


def safe_csv_slug(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip(". ")


def expand_case(case_name):
    """Expand a case into items with metadata.

    Returns list of dicts:
        { "name": "M4A1-S Hyper Beast", "csv_slug": "M4A1-S_Hyper Beast",
          "price_dir": "skins", "kind": "skin", "rarity": "Covert" }
    """
    case_slug = case_name.replace(" ", "_")
    case_path = CATALOGUES_DIR / "cases" / f"{case_slug}.json"
    case_data = load_json(case_path)
    if not case_data:
        return [], [f"Case JSON not found: {case_path}"]

    items = []
    warnings = []
    collection_name = (case_data.get("Collection") or "").strip()
    extraordinary = (case_data.get("ExtraordinaryItems") or "").strip()

    # Knives
    knives = case_data.get("Knives") or []
    if knives and extraordinary and not is_glove_label(extraordinary):
        finishes = find_knife_finishes(extraordinary)
        for knife in knives:
            knife = knife.strip()
            for finish in finishes:
                items.append({
                    "name": f"{knife} {finish}",
                    "csv_slug": safe_csv_slug(f"{knife}_{finish}"),
                    "price_dir": "knives",
                    "kind": "knife",
                    "rarity": "Exceedingly Rare",
                    "allow_st": True,
                })

    # Gloves
    if is_glove_label(extraordinary):
        gmap = find_glove_finishes(extraordinary)
        glove_types = case_data.get("Gloves") or list(gmap.keys())
        for g in glove_types:
            g = g.strip()
            if g in gmap:
                for f in gmap[g]:
                    items.append({
                        "name": f"{g} {f}",
                        "csv_slug": safe_csv_slug(f"{g}_{f}"),
                        "price_dir": "gloves",
                        "kind": "glove",
                        "rarity": "Exceedingly Rare",
                        "allow_st": False,
                    })

        # Also check for knives in glove cases
        if knives:
            finishes = find_knife_finishes(extraordinary)
            if not finishes:
                # Glove cases may have a separate knife pack
                pass
            for knife in knives:
                knife = knife.strip()
                for finish in finishes:
                    items.append({
                        "name": f"{knife} {finish}",
                        "csv_slug": safe_csv_slug(f"{knife}_{finish}"),
                        "price_dir": "knives",
                        "kind": "knife",
                        "rarity": "Exceedingly Rare",
                        "allow_st": True,
                    })

    # Collection skins
    coll = find_collection_json(collection_name) if collection_name else None
    if coll:
        for skin_info in coll.get("Skins", []):
            weapon = (skin_info.get("Weapon") or "").strip()
            skin = (skin_info.get("Name") or "").strip()
            rarity = normalize_rarity(skin_info.get("Rarity", "Restricted"))
            if weapon and skin:
                items.append({
                    "name": f"{weapon} {skin}",
                    "csv_slug": safe_csv_slug(f"{weapon}_{skin}"),
                    "price_dir": "skins",
                    "kind": "skin",
                    "rarity": rarity,
                    "allow_st": True,
                })
    elif collection_name:
        warnings.append(f"Collection not found: {collection_name}")

    return items, warnings


# ── CSV reading ───────────────────────────────────────────────────

def read_item_csv(price_dir, csv_slug):
    """Read an item CSV and return {wear_short: [(date_str, price), ...]}."""
    path = PRICES_DIR / price_dir / f"{csv_slug}.csv"
    if not path.exists():
        return None

    by_wear_date = {}  # wear_short -> date -> [prices]
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            has_wear = "wear" in (reader.fieldnames or [])
            for row in reader:
                price = float(row["price_usd"])
                if price <= 0 or not math.isfinite(price):
                    continue
                date = row["date"]
                if has_wear:
                    wear_long = row["wear"]
                    wear = WEAR_SHORT.get(wear_long)
                    if not wear:
                        continue
                else:
                    wear = "_case"  # Case CSVs have no wear column

                if wear not in by_wear_date:
                    by_wear_date[wear] = {}
                if date not in by_wear_date[wear]:
                    by_wear_date[wear][date] = []
                by_wear_date[wear][date].append(price)
    except (OSError, KeyError, ValueError) as e:
        return None

    # Compute median per (wear, date)
    result = {}
    for wear, dates in by_wear_date.items():
        series = []
        for date in sorted(dates.keys()):
            prices = dates[date]
            median = statistics.median(prices)
            series.append((date, median))
        result[wear] = series

    return result


def read_case_csv(case_slug):
    """Read case price CSV -> [(date, median_price), ...]."""
    path = PRICES_DIR / "cases" / f"{case_slug}.csv"
    if not path.exists():
        return None
    by_date = {}
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                price = float(row["price_usd"])
                if price <= 0 or not math.isfinite(price):
                    continue
                date = row["date"]
                if date not in by_date:
                    by_date[date] = []
                by_date[date].append(price)
    except (OSError, KeyError, ValueError):
        return None

    series = []
    for date in sorted(by_date.keys()):
        series.append((date, statistics.median(by_date[date])))
    return series


# ── Timescale filtering ───────────────────────────────────────────

def filter_timescale(dated_series, days):
    """Filter [(date_str, val), ...] to last N days. None = all."""
    if not dated_series:
        return []
    if days is None:
        return dated_series
    last_date = datetime.strptime(dated_series[-1][0], "%Y-%m-%d")
    cutoff = last_date - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    return [(d, v) for d, v in dated_series if d >= cutoff_str]


def to_xy(dated_series, max_points=400):
    """Convert [(date, val), ...] to [[x, y], ...] with x in [0,1].

    Downsamples to max_points using uniform selection if too many points.
    """
    n = len(dated_series)
    if n == 0:
        return []
    if n == 1:
        return [[0.0, round(dated_series[0][1], 2)]]

    # Downsample if needed
    if n > max_points:
        step = n / max_points
        indices = [0]
        pos = step
        while pos < n - 1:
            indices.append(round(pos))
            pos += step
        indices.append(n - 1)
        indices = sorted(set(indices))
        dated_series = [dated_series[i] for i in indices]
        n = len(dated_series)

    return [[round(i / (n - 1), 4), round(v, 2)] for i, (_, v) in enumerate(dated_series)]


# ── Smoothing ─────────────────────────────────────────────────────

def smooth(series, win=2):
    """5-point centered moving average on [[x,y], ...] series."""
    if len(series) <= 1 or win <= 0:
        return series
    out = [list(p) for p in series]
    for i in range(len(series)):
        total = 0
        cnt = 0
        for k in range(-win, win + 1):
            j = i + k
            if 0 <= j < len(series):
                total += series[j][1]
                cnt += 1
        out[i][1] = round(total / cnt, 4)
    return out


# ── Analysis helpers ──────────────────────────────────────────────

def log_returns(series):
    """Compute log-returns from [[x,y],...] -> [float, ...]."""
    r = []
    for i in range(1, len(series)):
        y0 = max(1e-9, series[i - 1][1])
        y1 = max(1e-9, series[i][1])
        r.append(math.log(y1) - math.log(y0))
    return r


def mean(a):
    return sum(a) / len(a) if a else 0.0


def std(a):
    if len(a) < 2:
        return 0.0
    m = mean(a)
    return math.sqrt(sum((x - m) ** 2 for x in a) / len(a))


def cov(a, b):
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    ma, mb = mean(a[:n]), mean(b[:n])
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / n


def corr(a, b):
    sa, sb = std(a), std(b)
    if sa < 1e-12 or sb < 1e-12:
        return 0.0
    return cov(a, b) / (sa * sb)


def ols(x, y):
    """Simple OLS: y = alpha + beta * x. Returns (alpha, beta, r2)."""
    n = min(len(x), len(y))
    if n < 3:
        return (0.0, 0.0, 0.0)
    mx, my = mean(x[:n]), mean(y[:n])
    sxx = sum((x[i] - mx) ** 2 for i in range(n))
    if sxx < 1e-15:
        return (my, 0.0, 0.0)
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    beta = sxy / sxx
    alpha = my - beta * mx
    ss_res = sum((y[i] - alpha - beta * x[i]) ** 2 for i in range(n))
    ss_tot = sum((y[i] - my) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-15 else 0.0
    return (alpha, beta, r2)


# ── Analysis modules ──────────────────────────────────────────────

def compute_core_stats(ev, price, spread):
    """Replicate core_stats.js."""
    rp = log_returns(price)
    re_ = log_returns(ev)
    n = min(len(rp), len(re_))
    rp, re_ = rp[:n], re_[:n]

    metrics = {
        "Corr(Price, EV)": round(corr(rp, re_), 4),
        "μ Price ret": round(mean(rp), 6),
        "σ Price ret": round(std(rp), 6),
        "μ EV ret": round(mean(re_), 6),
        "σ EV ret": round(std(re_), 6),
        "Spread μ": round(mean([p[1] for p in spread]), 4),
        "Spread σ": round(std([p[1] for p in spread]), 4),
    }

    # Rolling correlation
    W = max(10, round(0.2 * n))
    roll_corr = []
    for i in range(W, n):
        c = corr(rp[i - W:i], re_[i - W:i])
        x = ev[i + 1][0] if i + 1 < len(ev) else 1.0  # +1 because returns are offset
        roll_corr.append([round(x, 4), round(c, 4)])

    # Spread z-score
    sp_vals = [p[1] for p in spread]
    sp_mean = mean(sp_vals)
    sp_std = std(sp_vals)
    z_score = []
    for p in spread:
        z = (p[1] - sp_mean) / sp_std if sp_std > 1e-12 else 0.0
        z_score.append([p[0], round(z, 4)])

    series = [
        {"name": "Rolling Corr (Price vs EV)", "lines": [{"points": roll_corr, "color": "#38bdf8"}]},
        {"name": "Spread Z-Score (EV − Price)", "lines": [{"points": z_score, "color": "#a78bfa"}]},
    ]

    return {"title": "Core Statistics", "metrics": metrics, "series": series}


def compute_efficiency(ev, price):
    """Replicate efficiency.js."""
    n = min(len(ev), len(price))
    dp = [price[i + 1][1] - price[i][1] for i in range(n - 1)]
    de = [ev[i + 1][1] - ev[i][1] for i in range(n - 1)]
    m = min(len(dp), len(de))
    dp, de = dp[:m], de[:m]

    max_lag = min(20, m // 4) if m > 4 else 0
    lags = list(range(-max_lag, max_lag + 1))
    lag_corrs = []
    best_lag = 0
    best_corr = 0.0

    for k in lags:
        if k >= 0:
            a = dp[k:]
            b = de[:len(a)]
        else:
            a = dp[:m + k]
            b = de[-k:len(a) - k] if len(a) > 0 else []
        n_overlap = min(len(a), len(b))
        if n_overlap < 3:
            lag_corrs.append(0.0)
            continue
        c = corr(a[:n_overlap], b[:n_overlap])
        lag_corrs.append(c)
        if abs(c) > abs(best_corr):
            best_corr = c
            best_lag = k

    # OLS at best lag
    if best_lag >= 0:
        y_ols = dp[best_lag:]
        x_ols = de[:len(y_ols)]
    else:
        y_ols = dp[:m + best_lag]
        x_ols = de[-best_lag:]
    n_ols = min(len(x_ols), len(y_ols))
    alpha, beta, r2 = ols(x_ols[:n_ols], y_ols[:n_ols])

    inference = "EV leads Price" if best_lag > 0 else "Price leads EV" if best_lag < 0 else "Contemporaneous"

    metrics = {
        "Best Lag (EV→Price)": best_lag,
        "Corr at Best Lag": round(best_corr, 4),
        "OLS β (ΔP ~ ΔEV)": round(beta, 4),
        "OLS α": round(alpha, 6),
        "R²": round(r2, 4),
        "Inference": inference,
    }

    # Lag correlation series
    lag_points = []
    for i, k in enumerate(lags):
        x = i / max(1, len(lags) - 1)
        lag_points.append([round(x, 6), round(lag_corrs[i], 4)])

    series = [
        {"name": "Lead–Lag Correlation (k: EV leads +k)", "lines": [{"points": lag_points, "color": "#38bdf8"}]},
    ]

    return {"title": "Efficiency (Lead–Lag)", "metrics": metrics, "series": series}


def compute_volatility(ev, price, spread):
    """Replicate volatility.js."""
    rp = log_returns(price)
    re_ = log_returns(ev)
    n = min(len(rp), len(re_))
    rp, re_ = rp[:n], re_[:n]

    W = max(10, round(0.2 * n))
    ann = math.sqrt(252)

    roll_vol_p = []
    roll_vol_e = []
    for i in range(W, n):
        vp = std(rp[i - W:i]) * ann
        ve = std(re_[i - W:i]) * ann
        x = price[i + 1][0] if i + 1 < len(price) else 1.0
        roll_vol_p.append([round(x, 4), round(vp, 4)])
        roll_vol_e.append([round(x, 4), round(ve, 4)])

    # AR(1) on spread
    sp = [p[1] for p in spread]
    phi = 0.0
    if len(sp) > 2:
        x_ar = sp[:-1]
        y_ar = sp[1:]
        _, phi, _ = ols(x_ar, y_ar)

    half_life = math.log(0.5) / math.log(max(1e-9, abs(phi))) if 0 < abs(phi) < 1 else float("inf")

    metrics = {
        "Annualized Vol (Price, last)": round(roll_vol_p[-1][1], 4) if roll_vol_p else 0.0,
        "Annualized Vol (EV, last)": round(roll_vol_e[-1][1], 4) if roll_vol_e else 0.0,
        "AR(1) φ (spread)": round(phi, 4),
        "Spread Half-life (steps)": round(half_life, 1) if math.isfinite(half_life) else "∞",
    }

    series = [
        {"name": "Rolling Ann. Vol — Price", "lines": [{"points": roll_vol_p, "color": "#ef4444"}]},
        {"name": "Rolling Ann. Vol — EV", "lines": [{"points": roll_vol_e, "color": "#38bdf8"}]},
    ]

    return {"title": "Volatility & Mean Reversion", "metrics": metrics, "series": series}


def compute_cross_section(ev, price):
    """Replicate cross_section.js."""
    n = min(len(ev), len(price))
    if n == 0:
        return {"title": "Cross-Section", "metrics": {}, "series": []}

    ratio = []
    premium = []
    for i in range(n):
        p = max(1e-9, price[i][1])
        ratio.append([ev[i][0], round(ev[i][1] / p, 4)])
        premium.append([ev[i][0], round(ev[i][1] - price[i][1], 4)])

    last_ratio = ratio[-1][1] if ratio else 0
    last_premium = premium[-1][1] if premium else 0

    metrics = {
        "EV / Price (last)": round(last_ratio, 4),
        "EV − Price ($, last)": round(last_premium, 4),
        "EV above Price?": "Yes" if last_premium > 0 else "No",
    }

    series = [
        {"name": "EV / Price", "lines": [{"points": ratio, "color": "#38bdf8"}]},
        {"name": "EV − Price (Premium/Discount $)", "lines": [{"points": premium, "color": "#22c55e"}]},
    ]

    return {"title": "Cross-Section", "metrics": metrics, "series": series}


def compute_liquidity(price):
    """Replicate liquidity.js."""
    r = log_returns(price)
    abs_r = []
    for i, v in enumerate(r):
        x = price[i + 1][0] if i + 1 < len(price) else 1.0
        abs_r.append([round(x, 4), round(abs(v), 6)])

    metrics = {
        "Avg |Δlog Price|": round(mean([abs(v) for v in r]), 6) if r else 0.0,
    }

    series = [
        {"name": "|Δlog Price| over time (proxy illiquidity)", "lines": [{"points": abs_r, "color": "#a78bfa"}]},
    ]

    return {"title": "Liquidity", "metrics": metrics, "series": series}


def compute_signals(spread):
    """Replicate signals.js."""
    vals = [p[1] for p in spread]
    sp_mean = mean(vals)
    sp_std = std(vals)

    z_series = []
    band_1p = []
    band_1n = []
    band_2p = []
    band_2n = []
    for p in spread:
        z = (p[1] - sp_mean) / sp_std if sp_std > 1e-12 else 0.0
        z_series.append([p[0], round(z, 4)])
        band_1p.append([p[0], 1.0])
        band_1n.append([p[0], -1.0])
        band_2p.append([p[0], 2.0])
        band_2n.append([p[0], -2.0])

    current_z = z_series[-1][1] if z_series else None

    metrics = {
        "Current z-score": round(current_z, 4) if current_z is not None else None,
        "Signal rule": "Buy z < −2, Sell z > +2",
    }

    series = [
        {"name": "Spread Z-Score", "lines": [
            {"points": z_series, "color": "#a78bfa"},
            {"points": band_1p, "color": "rgba(255,255,255,0.15)"},
            {"points": band_1n, "color": "rgba(255,255,255,0.15)"},
            {"points": band_2p, "color": "rgba(255,255,255,0.08)"},
            {"points": band_2n, "color": "rgba(255,255,255,0.08)"},
        ]},
    ]

    return {"title": "Signals", "metrics": metrics, "series": series}


def compute_hurst(spread):
    """Hurst exponent via rescaled range (R/S) analysis on spread."""
    vals = [p[1] for p in spread]
    n = len(vals)
    if n < 20:
        return {"title": "Hurst Exponent", "metrics": {}, "series": []}

    # R/S analysis at multiple window sizes
    min_win = 10
    max_win = n // 2
    windows = []
    w = min_win
    while w <= max_win:
        windows.append(w)
        w = int(w * 1.4) + 1

    log_n = []
    log_rs = []
    for w in windows:
        rs_vals = []
        for start in range(0, n - w + 1, max(1, w // 2)):
            chunk = vals[start:start + w]
            m = mean(chunk)
            devs = [x - m for x in chunk]
            cumdev = []
            s = 0
            for d in devs:
                s += d
                cumdev.append(s)
            R = max(cumdev) - min(cumdev)
            S = std(chunk)
            if S > 1e-12:
                rs_vals.append(R / S)
        if rs_vals:
            log_n.append(math.log(w))
            log_rs.append(math.log(mean(rs_vals)))

    # Fit H via OLS: log(R/S) = H * log(n) + c
    H = 0.5
    if len(log_n) >= 3:
        _, H, r2 = ols(log_n, log_rs)
    else:
        r2 = 0.0

    regime = "Mean-reverting" if H < 0.45 else "Trending" if H > 0.55 else "Random walk"

    # R/S plot
    rs_points = [[round(log_n[i], 4), round(log_rs[i], 4)] for i in range(len(log_n))]
    # Fit line
    if len(log_n) >= 2:
        fit_line = [[round(log_n[0], 4), round(log_n[0] * H + (mean(log_rs) - H * mean(log_n)), 4)],
                     [round(log_n[-1], 4), round(log_n[-1] * H + (mean(log_rs) - H * mean(log_n)), 4)]]
    else:
        fit_line = []

    metrics = {
        "Hurst Exponent (H)": round(H, 4),
        "Regime": regime,
        "R² (fit)": round(r2, 4),
        "Interpretation": f"H={H:.2f}: {'basis reverts' if H < 0.5 else 'basis trends' if H > 0.5 else 'random walk'}",
    }

    series = [
        {"name": "R/S Analysis: log(R/S) vs log(n)", "lines": [
            {"points": rs_points, "color": "#38bdf8"},
            {"points": fit_line, "color": "#ef4444"},
        ]},
    ]

    return {"title": "Hurst Exponent", "metrics": metrics, "series": series}


def compute_autocorrelation(spread):
    """Autocorrelation lag sweep on spread returns (like Prosperity mean_reversion.py)."""
    vals = [p[1] for p in spread]
    n = len(vals)
    if n < 30:
        return {"title": "Autocorrelation", "metrics": {}, "series": []}

    # Returns of the spread
    rets = [vals[i] - vals[i - 1] for i in range(1, n)]
    m = mean(rets)
    v = sum((r - m) ** 2 for r in rets) / len(rets)

    max_lag = min(50, len(rets) // 4)
    ac_values = []
    sig_threshold = 1.96 / math.sqrt(len(rets))

    for lag in range(1, max_lag + 1):
        if v < 1e-15:
            ac_values.append(0.0)
            continue
        c = sum((rets[i] - m) * (rets[i - lag] - m) for i in range(lag, len(rets))) / (len(rets) * v)
        ac_values.append(c)

    # Find strongest signal
    best_lag = 0
    best_ac = 0.0
    for i, ac in enumerate(ac_values):
        if abs(ac) > abs(best_ac):
            best_ac = ac
            best_lag = i + 1

    # Series: AC bars
    ac_points = [[round(i / max(1, max_lag - 1), 4), round(ac, 4)] for i, ac in enumerate(ac_values)]
    sig_upper = [[round(i / max(1, max_lag - 1), 4), round(sig_threshold, 4)] for i in range(len(ac_values))]
    sig_lower = [[round(i / max(1, max_lag - 1), 4), round(-sig_threshold, 4)] for i in range(len(ac_values))]

    lag1_ac = ac_values[0] if ac_values else 0.0
    sig_lags = sum(1 for ac in ac_values if abs(ac) > sig_threshold)

    metrics = {
        "Lag-1 AC": round(lag1_ac, 4),
        "Best Lag": best_lag,
        "AC at Best Lag": round(best_ac, 4),
        "Significant Lags": f"{sig_lags}/{max_lag}",
        "Signal": "Mean-reverting" if lag1_ac < -sig_threshold else "Momentum" if lag1_ac > sig_threshold else "No signal",
        "95% Threshold": f"±{round(sig_threshold, 4)}",
    }

    series = [
        {"name": "Spread Return Autocorrelation (lag 1–50)", "lines": [
            {"points": ac_points, "color": "#38bdf8"},
            {"points": sig_upper, "color": "rgba(239,68,68,0.4)"},
            {"points": sig_lower, "color": "rgba(239,68,68,0.4)"},
        ]},
    ]

    return {"title": "Autocorrelation", "metrics": metrics, "series": series}


def compute_regime(ev, price, spread):
    """Volatility regime detection + rolling Sharpe of basis mean-reversion."""
    vals = [p[1] for p in spread]
    n = len(vals)
    if n < 30:
        return {"title": "Regimes", "metrics": {}, "series": []}

    # Rolling volatility of spread (30-day window)
    W = max(10, min(30, n // 4))
    rets = [vals[i] - vals[i - 1] for i in range(1, n)]

    # Rolling spread volatility
    roll_vol = []
    for i in range(W, len(rets)):
        window = rets[i - W:i]
        vol = std(window) * math.sqrt(252)
        x = spread[i + 1][0] if i + 1 < len(spread) else 1.0
        roll_vol.append([round(x, 4), round(vol, 4)])

    # Regime classification by quartile
    vol_vals = [p[1] for p in roll_vol]
    if vol_vals:
        q25 = sorted(vol_vals)[len(vol_vals) // 4]
        q75 = sorted(vol_vals)[3 * len(vol_vals) // 4]
        current_vol = vol_vals[-1] if vol_vals else 0
        regime = "Low vol" if current_vol <= q25 else "High vol" if current_vol >= q75 else "Normal"
    else:
        q25 = q75 = 0
        regime = "Unknown"

    # Rolling Sharpe of a simple mean-reversion strategy
    # Strategy: if spread z < -1, go long spread; if z > 1, go short; else flat
    sp_mean = mean(vals)
    sp_std = std(vals)
    strat_returns = []
    position = 0
    for i in range(1, n):
        z = (vals[i - 1] - sp_mean) / sp_std if sp_std > 1e-12 else 0
        if z < -1:
            position = 1
        elif z > 1:
            position = -1
        else:
            position = 0
        strat_returns.append(position * (vals[i] - vals[i - 1]))

    # Rolling Sharpe (60-day window)
    W_sharpe = max(10, min(60, len(strat_returns) // 3))
    roll_sharpe = []
    for i in range(W_sharpe, len(strat_returns)):
        window = strat_returns[i - W_sharpe:i]
        m = mean(window)
        s = std(window)
        sharpe = (m / s * math.sqrt(252)) if s > 1e-12 else 0
        x = spread[i + 1][0] if i + 1 < len(spread) else 1.0
        roll_sharpe.append([round(x, 4), round(sharpe, 4)])

    # Cumulative PnL of the MR strategy
    cum_pnl = []
    running = 0
    for i, r in enumerate(strat_returns):
        running += r
        x = spread[i + 1][0] if i + 1 < len(spread) else 1.0
        cum_pnl.append([round(x, 4), round(running, 4)])

    # Max drawdown
    peak = 0
    max_dd = 0
    for p in cum_pnl:
        peak = max(peak, p[1])
        dd = peak - p[1]
        max_dd = max(max_dd, dd)

    total_pnl = cum_pnl[-1][1] if cum_pnl else 0
    total_sharpe = 0
    if strat_returns:
        m = mean(strat_returns)
        s = std(strat_returns)
        total_sharpe = (m / s * math.sqrt(252)) if s > 1e-12 else 0

    metrics = {
        "Current Regime": regime,
        "Spread Vol (ann.)": round(vol_vals[-1], 4) if vol_vals else 0,
        "Vol Q25/Q75": f"{round(q25, 2)}/{round(q75, 2)}",
        "MR Strategy PnL": round(total_pnl, 2),
        "MR Strategy Sharpe": round(total_sharpe, 2),
        "Max Drawdown": round(max_dd, 2),
    }

    series = [
        {"name": "Spread Rolling Volatility (ann.)", "lines": [
            {"points": roll_vol, "color": "#f59e0b"},
        ]},
        {"name": "MR Strategy Rolling Sharpe", "lines": [
            {"points": roll_sharpe, "color": "#22c55e"},
        ]},
        {"name": "MR Strategy Cumulative PnL", "lines": [
            {"points": cum_pnl, "color": "#38bdf8"},
        ]},
    ]

    return {"title": "Regimes & MR Backtest", "metrics": metrics, "series": series}


def compute_cointegration(ev, price):
    """Engle-Granger cointegration test (ADF on OLS residuals)."""
    n = min(len(ev), len(price))
    if n < 30:
        return {"title": "Cointegration", "metrics": {}, "series": []}

    x = [ev[i][1] for i in range(n)]
    y = [price[i][1] for i in range(n)]

    # OLS: Price = alpha + beta * EV + residual
    alpha, beta, r2 = ols(x, y)
    residuals = [y[i] - alpha - beta * x[i] for i in range(n)]

    # ADF test on residuals (simplified: test if AR(1) coefficient < 1)
    if len(residuals) < 10:
        return {"title": "Cointegration", "metrics": {"Error": "Too few points"}, "series": []}

    # Dickey-Fuller: Δresid = γ * resid(-1) + error
    # If γ < 0, residuals are mean-reverting → cointegrated
    dr = [residuals[i] - residuals[i - 1] for i in range(1, len(residuals))]
    r_lag = residuals[:-1]
    n_df = min(len(dr), len(r_lag))
    _, gamma, r2_df = ols(r_lag[:n_df], dr[:n_df])

    # t-statistic for gamma
    y_hat = [gamma * r_lag[i] for i in range(n_df)]
    sse = sum((dr[i] - y_hat[i]) ** 2 for i in range(n_df))
    se_gamma = math.sqrt(sse / max(1, n_df - 1) / max(1e-15, sum(r_lag[i] ** 2 for i in range(n_df))))
    t_stat = gamma / se_gamma if se_gamma > 1e-12 else 0

    # Critical values (Engle-Granger, n=2, approximate)
    # 1%: -3.90, 5%: -3.34, 10%: -3.04
    coint = "Yes (1%)" if t_stat < -3.90 else "Yes (5%)" if t_stat < -3.34 else "Yes (10%)" if t_stat < -3.04 else "No"

    # Residual series
    resid_points = [[ev[i][0], round(residuals[i], 4)] for i in range(n)]
    zero_line = [[ev[0][0], 0.0], [ev[-1][0], 0.0]]

    resid_std = std(residuals)
    band_up = [[ev[0][0], round(resid_std, 4)], [ev[-1][0], round(resid_std, 4)]]
    band_dn = [[ev[0][0], round(-resid_std, 4)], [ev[-1][0], round(-resid_std, 4)]]

    metrics = {
        "Cointegrated?": coint,
        "ADF t-stat": round(t_stat, 4),
        "γ (speed of adj.)": round(gamma, 4),
        "OLS β (Price~EV)": round(beta, 4),
        "OLS R²": round(r2, 4),
        "Resid. Half-life": round(math.log(0.5) / math.log(max(1e-9, abs(1 + gamma))), 1) if -2 < gamma < 0 else "∞",
    }

    series = [
        {"name": "Cointegration Residuals (Price − β·EV − α)", "lines": [
            {"points": resid_points, "color": "#38bdf8"},
            {"points": zero_line, "color": "rgba(255,255,255,0.2)"},
            {"points": band_up, "color": "rgba(239,68,68,0.3)"},
            {"points": band_dn, "color": "rgba(239,68,68,0.3)"},
        ]},
    ]

    return {"title": "Cointegration", "metrics": metrics, "series": series}


def run_analysis(ev, price):
    """Run all analysis modules. Returns dict of module results."""
    n = min(len(ev), len(price))
    ev = ev[:n]
    price = price[:n]
    spread = [[ev[i][0], round(ev[i][1] - price[i][1], 4)] for i in range(n)]

    if n < 5:
        return {}

    return {
        "core_stats": compute_core_stats(ev, price, spread),
        "efficiency": compute_efficiency(ev, price),
        "volatility": compute_volatility(ev, price, spread),
        "cross_section": compute_cross_section(ev, price),
        "liquidity": compute_liquidity(price),
        "signals": compute_signals(spread),
        "hurst": compute_hurst(spread),
        "autocorrelation": compute_autocorrelation(spread),
        "regimes": compute_regime(ev, price, spread),
        "cointegration": compute_cointegration(ev, price),
    }


# ── EV computation ────────────────────────────────────────────────

def build_ev(items, item_series, timescale_key, days):
    """Build EV series for a given timescale.

    items: list of item dicts from expand_case
    item_series: dict name -> {wear -> [(date, price), ...]}
    """
    # Count items per rarity tier
    tier_counts = {}
    for item in items:
        r = item["rarity"]
        tier_counts[r] = tier_counts.get(r, 0) + 1

    # Collect all dates across items for this timescale
    all_dates = set()
    for item in items:
        data = item_series.get(item["name"])
        if not data:
            continue
        for wear in WEARS:
            for date, _ in data.get(wear, []):
                all_dates.add(date)

    if not all_dates:
        return []

    sorted_dates = sorted(all_dates)

    # Filter by timescale
    if days is not None:
        last_date = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
        cutoff = (last_date - timedelta(days=days)).strftime("%Y-%m-%d")
        sorted_dates = [d for d in sorted_dates if d >= cutoff]

    if not sorted_dates:
        return []

    # Build date -> EV
    ev_by_date = {d: 0.0 for d in sorted_dates}

    for item in items:
        rarity = item["rarity"]
        p_rarity = RARITY_PROBS.get(rarity, 0)
        n_tier = tier_counts.get(rarity, 1)
        p_item = p_rarity / n_tier
        if p_item <= 0:
            continue

        data = item_series.get(item["name"])
        if not data:
            continue

        allow_st = item.get("allow_st", True)

        for wear in WEARS:
            pw = WEAR_PROBS.get(wear, 0)
            wear_dates = dict(data.get(wear, []))

            for st in [True, False]:
                if st and not allow_st:
                    continue
                pst = ST_PROB if st else (1 - ST_PROB if allow_st else 1.0)
                weight = p_item * pw * pst

                for date in sorted_dates:
                    price = wear_dates.get(date)
                    if price is not None:
                        ev_by_date[date] += weight * price

    result = [(d, ev_by_date[d]) for d in sorted_dates]
    xy = to_xy(result)
    return smooth(xy, 2)


# ── Main precompute ───────────────────────────────────────────────

def precompute_case(case_name, output_dir):
    """Precompute everything for one case."""
    print(f"\n{'='*60}")
    print(f"  {case_name}")
    print(f"{'='*60}")

    items, warnings = expand_case(case_name)
    if not items:
        print(f"  [SKIP] No items expanded")
        return

    # Load all item price data
    item_series = {}  # name -> {wear -> [(date, price), ...]}
    missing = []
    for item in items:
        data = read_item_csv(item["price_dir"], item["csv_slug"])
        if data:
            item_series[item["name"]] = data
        else:
            missing.append(item["name"])

    skins_found = sum(1 for i in items if i["kind"] == "skin" and i["name"] in item_series)
    knives_found = sum(1 for i in items if i["kind"] == "knife" and i["name"] in item_series)
    gloves_found = sum(1 for i in items if i["kind"] == "glove" and i["name"] in item_series)
    print(f"  Items: {len(items)} total, {len(item_series)} with data, {len(missing)} missing")
    print(f"  Skins: {skins_found}, Knives: {knives_found}, Gloves: {gloves_found}")

    # Load case price
    case_slug = case_name.replace(" ", "_")
    case_prices = read_case_csv(case_slug)
    if not case_prices:
        warnings.append(f"No case price CSV for {case_name}")
        print(f"  [WARN] No case price CSV")

    if missing:
        warnings.extend(f"Missing CSV: {n}" for n in missing[:20])
        if len(missing) > 20:
            warnings.append(f"... and {len(missing) - 20} more missing")

    # Build output
    output = {
        "case_name": case_name,
        "generated_at": datetime.now().astimezone().isoformat(),
        "item_count": len(items),
        "items_with_data": len(item_series),
        "timescales": {},
        "items": {},
        "warnings": warnings,
    }

    # Per-item data (per timescale, per wear)
    for item in items:
        data = item_series.get(item["name"])
        item_out = {
            "rarity": item["rarity"],
            "kind": item["kind"],
            "allow_st": item.get("allow_st", True),
            "wears": {},
            "average": {},
        }
        for ts_key, ts_days in TIMESCALES.items():
            for wear in WEARS:
                if data and wear in data:
                    filtered = filter_timescale(data[wear], ts_days)
                    xy = to_xy(filtered, max_points=120)
                else:
                    xy = []
                if wear not in item_out["wears"]:
                    item_out["wears"][wear] = {}
                item_out["wears"][wear][ts_key] = xy

            # Weighted average across wears
            # Find common dates for this timescale
            all_dates_for_avg = set()
            wear_dated = {}
            for wear in WEARS:
                if data and wear in data:
                    filtered = filter_timescale(data[wear], ts_days)
                    wear_dated[wear] = dict(filtered)
                    all_dates_for_avg.update(wear_dated[wear].keys())
                else:
                    wear_dated[wear] = {}

            avg_series = []
            allow_st = item.get("allow_st", True)
            for date in sorted(all_dates_for_avg):
                val = 0.0
                for wear in WEARS:
                    pw = WEAR_PROBS.get(wear, 0)
                    price = wear_dated[wear].get(date)
                    if price is None:
                        continue
                    for st in [True, False]:
                        if st and not allow_st:
                            continue
                        pst = ST_PROB if st else (1 - ST_PROB if allow_st else 1.0)
                        val += pw * pst * price
                avg_series.append((date, val))
            item_out["average"][ts_key] = to_xy(avg_series, max_points=120)

        output["items"][item["name"]] = item_out

    # Per-timescale: case price, EV, analysis
    for ts_key, ts_days in TIMESCALES.items():
        ts_out = {}

        # Case price
        if case_prices:
            filtered = filter_timescale(case_prices, ts_days)
            ts_out["case_price"] = to_xy(filtered)
        else:
            ts_out["case_price"] = []

        # EV
        ev = build_ev(items, item_series, ts_key, ts_days)
        ts_out["ev"] = ev

        # Basis (EV - Price)
        cp = ts_out["case_price"]
        n = min(len(ev), len(cp))
        ts_out["basis"] = [[ev[i][0], round(ev[i][1] - cp[i][1], 4)] for i in range(n)] if n > 0 else []

        # Analysis
        if n >= 5:
            ts_out["analysis"] = run_analysis(ev[:n], cp[:n])
        else:
            ts_out["analysis"] = {}

        output["timescales"][ts_key] = ts_out
        print(f"  {ts_key}: case_price={len(ts_out['case_price'])}pts, ev={len(ev)}pts, analysis={'yes' if ts_out['analysis'] else 'no'}")

    # Write output
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{case_slug}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  Output: {out_path.name} ({size_mb:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description="Precompute case data for dashboard")
    parser.add_argument("--cases", nargs="*", help="Specific cases to process")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR, help="Output directory")
    args = parser.parse_args()

    output_dir = args.output

    # Get case list
    cases_dir = CATALOGUES_DIR / "cases"
    if args.cases:
        case_names = args.cases
    else:
        case_names = []
        for f in sorted(cases_dir.glob("*.json")):
            name = f.stem.replace("_", " ")
            case_names.append(name)

    print(f"Precomputing {len(case_names)} cases...")
    print(f"Prices dir: {PRICES_DIR}")
    print(f"Output dir: {output_dir}")

    for case_name in case_names:
        precompute_case(case_name, output_dir)

    # Summary
    total_size = sum(f.stat().st_size for f in output_dir.glob("*.json"))
    print(f"\nDone. {len(case_names)} cases, {total_size / (1024*1024):.1f} MB total")


if __name__ == "__main__":
    main()
