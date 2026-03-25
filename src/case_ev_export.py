"""Export case EV data from CSGO processed CSVs to the Quant repo format.

Reads:
    - data/catalogues/ JSON files (case definitions, collections, knives, gloves)
    - Data/processed/ per-item CSVs (date, provider, price_usd)

Writes to Quant repo:
    - data/prices/cases/<CaseName>.csv         (date, price_usd)
    - data/prices/skins/<Weapon>_<Skin>.csv    (date, wear, provider, price_usd)
    - data/prices/knives/<Knife>_<Finish>.csv  (date, wear, provider, price_usd)
    - data/prices/gloves/<Glove>_<Finish>.csv  (date, wear, provider, price_usd)

Usage:
    python case_ev_export.py --quant-dir ../Quant
    python case_ev_export.py --quant-dir ../Quant --cases "Chroma 2" "Prisma"
    python case_ev_export.py --quant-dir ../Quant --provider steam  # single provider
"""

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROCESSED_DIR = Path(r"C:\Users\z00503ku\Documents\CSGO\Data\processed")

WEARS = ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]
WEAR_SHORT = {"Factory New": "FN", "Minimal Wear": "MW", "Field-Tested": "FT",
              "Well-Worn": "WW", "Battle-Scarred": "BS"}


# ── Catalog loading ─────────────────────────────────────────────────

def load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_collection_json(catalogues_dir, collection_name):
    """Try multiple filename variants to find the collection JSON."""
    stem = re.sub(r'\s*Collection\s*$', '', collection_name, flags=re.I).strip()
    variants = [stem]
    no_the = re.sub(r'^The\s+', '', stem, flags=re.I).strip()
    if no_the != stem:
        variants.append(no_the)
    if not stem.lower().startswith("the "):
        variants.append(f"The {stem}")

    for v in variants:
        for s in [v, v.replace("&", "and"), v.replace("&", "")]:
            slug = re.sub(r'[^\w\s&-]', '', s).strip().replace(" ", "_")
            path = catalogues_dir / "collections" / f"{slug}.json"
            if path.exists():
                return load_json(path)
    return None


def find_knife_finishes(catalogues_dir, extraordinary_items):
    """Load knife finishes from the appropriate pack file."""
    if not extraordinary_items:
        return []
    s = extraordinary_items.strip().lower()
    s = re.sub(r'\s+knives?$', '', s)

    hints = {
        "original": "Original", "chroma": "Chroma", "gamma": "Gamma",
        "spectrum": "Spectrum", "fracture": "Fracture", "horizon": "Horizon",
        "prisma": "Prisma", "prisma 2": "Prisma_2", "gamma 2": "Gamma_2",
        "chroma 2": "Chroma_2", "chroma 3": "Chroma_3", "spectrum 2": "Spectrum_2",
    }
    filename = hints.get(s)
    if not filename:
        filename = "_".join(w.capitalize() for w in s.split())

    path = catalogues_dir / "knives" / f"{filename}.json"
    data = load_json(path)
    if not data:
        return []

    finishes = []
    for lst in (data.get("Finishes") or {}).values():
        finishes.extend(lst or [])
    return finishes


def find_glove_finishes(catalogues_dir, extraordinary_items):
    """Load glove finishes from the appropriate pack file."""
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

    path = catalogues_dir / "gloves" / f"{filename}.json"
    data = load_json(path)
    if not data:
        return {}
    return data.get("Finishes") or {}


def is_glove_label(label):
    if not label:
        return False
    s = label.lower()
    return any(k in s for k in ["glove", "gloves", "broken fang", "clutch"])


# ── Item name mapping ───────────────────────────────────────────────

def _safe_folder(name):
    """Convert item name to the folder name used in Data/processed/."""
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip('. ')


def find_item_csv(weapon, skin):
    """Find the processed CSV for a weapon+skin combo.

    Returns Path or None.
    Handles cases like:
        weapon="AK-47", skin="Elite Build" -> Data/processed/AK-47 - Elite Build/
    """
    folder_name = _safe_folder(f"{weapon} - {skin}")
    folder = PROCESSED_DIR / folder_name
    if folder.exists():
        return folder
    return None


def find_knife_csv(knife, finish):
    """Find processed CSV folder for a knife.

    In PriceEmpire data, knife names look like:
        "★ Karambit | Marble Fade (Factory New)"
    Our processed folder would be: "Karambit - Marble Fade"

    Doppler phases are collapsed: "Doppler Phase 1" -> "Doppler"
    """
    # Try exact match first
    folder_name = _safe_folder(f"{knife} - {finish}")
    folder = PROCESSED_DIR / folder_name
    if folder.exists():
        return folder

    # Doppler phases -> single "Doppler" item in PriceEmpire
    if "doppler" in finish.lower():
        base = "Gamma Doppler" if "gamma" in finish.lower() else "Doppler"
        folder_name = _safe_folder(f"{knife} - {base}")
        folder = PROCESSED_DIR / folder_name
        if folder.exists():
            return folder

    return None


def find_case_csv(case_name):
    """Find processed CSV for a case itself.

    Cases are stored as: Data/processed/Cases/<case_name>.csv
    """
    # Try exact match first
    safe = _safe_folder(case_name)
    path = PROCESSED_DIR / "Cases" / f"{safe}.csv"
    if path.exists():
        return path

    # Try with "Case" suffix
    for suffix in ["", " Case"]:
        safe = _safe_folder(f"{case_name}{suffix}")
        path = PROCESSED_DIR / "Cases" / f"{safe}.csv"
        if path.exists():
            return path

    return None


# ── CSV reading ─────────────────────────────────────────────────────

def read_item_prices(csv_path, provider_filter=None):
    """Read a processed CSV and return list of (date, provider, price_usd)."""
    rows = []
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if provider_filter and row["provider"] not in provider_filter:
                    continue
                rows.append((row["date"], row["provider"], float(row["price_usd"])))
    except (OSError, KeyError, ValueError):
        pass
    return rows


def read_folder_prices(folder, provider_filter=None):
    """Read all wear CSVs from an item folder.

    Reads both normal wears (e.g. "Field-Tested.csv") and StatTrak
    wears (e.g. "ST Field-Tested.csv").

    Returns: list of (date, wear, stattrak, provider, price_usd)
    """
    rows = []
    if not folder or not folder.exists():
        return rows

    for csv_file in folder.iterdir():
        if not csv_file.name.endswith(".csv"):
            continue
        stem = csv_file.stem  # e.g. "Factory New", "ST Field-Tested"

        # Parse StatTrak prefix
        is_st = stem.startswith("ST ")
        wear = stem[3:] if is_st else stem

        if wear not in WEARS:
            continue

        for date, prov, price in read_item_prices(csv_file, provider_filter):
            rows.append((date, wear, is_st, prov, price))

    return rows


# ── Export logic ────────────────────────────────────────────────────

def _write_item_csv(out_path, rows):
    """Write item price rows to CSV.

    Args:
        out_path: Path to output CSV
        rows: list of (date, wear, is_stattrak, provider, price_usd)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "wear", "stattrak", "provider", "price_usd"])
        for date, wear, is_st, prov, price in sorted(rows):
            w.writerow([date, wear, "true" if is_st else "false", prov, f"{price:.2f}"])


def export_case(case_name, catalogues_dir, output_dir, provider_filter=None):
    """Export all price data for one case."""
    case_json_name = case_name.replace(" ", "_")
    case_path = catalogues_dir / "cases" / f"{case_json_name}.json"
    case_data = load_json(case_path)
    if not case_data:
        print(f"  [SKIP] Case JSON not found: {case_path}")
        return

    collection_name = (case_data.get("Collection") or "").strip()
    extraordinary = (case_data.get("ExtraordinaryItems") or "").strip()

    stats = {"case": False, "skins": 0, "knives": 0, "gloves": 0,
             "skins_missing": [], "knives_missing": [], "gloves_missing": []}

    # 1) Export case price itself
    case_csv = find_case_csv(case_data.get("Case", case_name))
    if not case_csv:
        # Try alternate names
        case_csv = find_case_csv(case_name)

    prices_dir = output_dir / "data" / "prices"

    if case_csv:
        rows = read_item_prices(case_csv, provider_filter)
        if rows:
            out_path = prices_dir / "cases" / f"{case_json_name}.csv"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "provider", "price_usd"])
                for date, prov, price in sorted(rows):
                    w.writerow([date, prov, f"{price:.2f}"])
            stats["case"] = True
            print(f"    Case price: {len(rows)} rows -> {out_path.name}")

    # 2) Export collection skins
    collection = find_collection_json(catalogues_dir, collection_name)
    if collection:
        for skin_info in collection.get("Skins", []):
            weapon = (skin_info.get("Weapon") or "").strip()
            skin = (skin_info.get("Name") or "").strip()
            if not weapon or not skin:
                continue

            folder = find_item_csv(weapon, skin)
            rows = read_folder_prices(folder, provider_filter)

            if rows:
                slug = _safe_folder(f"{weapon}_{skin}")
                out_path = prices_dir / "skins" / f"{slug}.csv"
                _write_item_csv(out_path, rows)
                stats["skins"] += 1
            else:
                stats["skins_missing"].append(f"{weapon} {skin}")

    # 3) Export knives
    knives = case_data.get("Knives") or []
    if knives and extraordinary:
        finishes = find_knife_finishes(catalogues_dir, extraordinary)
        for knife in knives:
            knife = knife.strip()
            for finish in finishes:
                folder = find_knife_csv(knife, finish)
                rows = read_folder_prices(folder, provider_filter)

                if rows:
                    slug = _safe_folder(f"{knife}_{finish}")
                    out_path = prices_dir / "knives" / f"{slug}.csv"
                    _write_item_csv(out_path, rows)
                    stats["knives"] += 1
                else:
                    stats["knives_missing"].append(f"{knife} {finish}")

    # 4) Export gloves
    if is_glove_label(extraordinary):
        glove_map = find_glove_finishes(catalogues_dir, extraordinary)
        glove_types = case_data.get("Gloves") or list(glove_map.keys())

        for glove in glove_types:
            glove = glove.strip()
            glove_finishes = glove_map.get(glove, [])
            for finish in glove_finishes:
                folder = find_knife_csv(glove, finish)  # same folder structure
                rows = read_folder_prices(folder, provider_filter)

                if rows:
                    slug = _safe_folder(f"{glove}_{finish}")
                    out_path = prices_dir / "gloves" / f"{slug}.csv"
                    _write_item_csv(out_path, rows)
                    stats["gloves"] += 1
                else:
                    stats["gloves_missing"].append(f"{glove} {finish}")

    # Summary
    print(f"    Skins: {stats['skins']} exported", end="")
    if stats["skins_missing"]:
        print(f" ({len(stats['skins_missing'])} missing)", end="")
    print()
    print(f"    Knives: {stats['knives']} exported", end="")
    if stats["knives_missing"]:
        print(f" ({len(stats['knives_missing'])} missing)", end="")
    print()
    if stats["gloves"] or stats["gloves_missing"]:
        print(f"    Gloves: {stats['gloves']} exported", end="")
        if stats["gloves_missing"]:
            print(f" ({len(stats['gloves_missing'])} missing)", end="")
        print()

    if not stats["case"]:
        print(f"    WARNING: Case price not found for '{case_name}'")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Export case EV data for the Quant repo")
    parser.add_argument("--quant-dir", required=True,
                        help="Path to the Quant repo root")
    parser.add_argument("--cases", nargs="+", default=None,
                        help="Case names to export (default: all)")
    parser.add_argument("--provider", default=None,
                        help="Only export data from this provider (e.g. steam)")
    parser.add_argument("--list", action="store_true",
                        help="List available cases and exit")
    args = parser.parse_args()

    quant_dir = Path(args.quant_dir).resolve()
    catalogues_dir = quant_dir / "data" / "catalogues"

    if not catalogues_dir.exists():
        print(f"ERROR: Catalogues not found at {catalogues_dir}")
        sys.exit(1)

    if not PROCESSED_DIR.exists():
        print(f"ERROR: No processed data at {PROCESSED_DIR}")
        print("Run 'python -m ingest convert' first.")
        sys.exit(1)

    # Discover all cases
    cases_dir = catalogues_dir / "cases"
    all_cases = sorted(
        f.stem.replace("_", " ")
        for f in cases_dir.glob("*.json")
    )

    if args.list:
        print(f"Available cases ({len(all_cases)}):")
        for c in all_cases:
            print(f"  {c}")
        return

    cases_to_export = args.cases or all_cases
    provider_filter = {args.provider} if args.provider else None

    print(f"Exporting {len(cases_to_export)} cases to {quant_dir}")
    if provider_filter:
        print(f"Provider filter: {args.provider}")
    print(f"Source: {PROCESSED_DIR}")
    print()

    total_stats = {"cases": 0, "skins": 0, "knives": 0, "gloves": 0}

    for case_name in cases_to_export:
        print(f"  [{case_name}]")
        stats = export_case(case_name, catalogues_dir, quant_dir, provider_filter)
        if stats:
            if stats["case"]:
                total_stats["cases"] += 1
            total_stats["skins"] += stats["skins"]
            total_stats["knives"] += stats["knives"]
            total_stats["gloves"] += stats["gloves"]
        print()

    print("=" * 60)
    print(f"Done: {total_stats['cases']} cases, {total_stats['skins']} skins, "
          f"{total_stats['knives']} knives, {total_stats['gloves']} gloves")
    print(f"Output: {quant_dir / 'data' / 'prices'}")


if __name__ == "__main__":
    main()
