"""
STACKARB — SCRAPER RUNNER FOR GITHUB ACTIONS
=============================================
Run:  python scraper/run_all.py

Runs all supplement scrapers and outputs JSON to public/data/results.json.
This is called by the GitHub Actions workflow on a schedule.
The React frontend reads this JSON to render the dashboard.
"""

import json, math, time, sys, os
from datetime import datetime, timezone

# Add scraper directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import omega3_scraper as o3
import vegan_omega3_scraper as vo3
import astaxanthin_scraper as ax
import lycopene_scraper as lyc

import requests as req_lib

# ── Config ─────────────────────────────────────────────────────────────────

SUPPLEMENT_CONFIG = {
    "omega3": {
        "module": o3,
        "target_dose": o3.TARGET_DOSE,
        "dose_label": "EPA",
        "products": o3.PRODUCTS + [dict(p, vegan=True) for p in vo3.PRODUCTS],
        "known": {**o3.KNOWN, **vo3.KNOWN},
        "strategies": {
            "shopify":     o3.extract_shopify,
            "jsonld":      o3.extract_jsonld,
            "meta_jsonld": o3.extract_meta_jsonld,
            "iherb":       o3.extract_iherb,
            "superdrug":   o3.extract_superdrug,
            "v_shopify":   vo3.extract_shopify,
            "v_jsonld":    vo3.extract_jsonld,
            "magento":     vo3.extract_magento,
            "vegetology":  vo3.extract_vegetology,
        },
    },
    "astaxanthin": {
        "module": ax,
        "target_dose": ax.TARGET_DOSE,
        "dose_label": "Astaxanthin",
        "products": ax.PRODUCTS,
        "known": ax.KNOWN,
        "strategies": {
            "shopify":  ax.extract_shopify,
            "dolphin":  ax.extract_dolphin,
            "iherb":    ax.extract_iherb,
        },
    },
    "lycopene": {
        "module": lyc,
        "target_dose": lyc.TARGET_DOSE,
        "dose_label": "Lycopene",
        "products": lyc.PRODUCTS,
        "known": lyc.KNOWN,
        "strategies": {
            "shopify": lyc.extract_shopify,
            "ebay":    lyc.extract_ebay,
            "iherb":   lyc.extract_iherb,
        },
    },
}


def run_scraper(supplement_key):
    """Run a supplement scraper, returning results list."""
    config = SUPPLEMENT_CONFIG[supplement_key]
    products = config["products"]
    known = config["known"]
    target = config["target_dose"]
    strategies = config["strategies"]

    def log(msg):
        print(f"  [{supplement_key}] {msg}")

    session = req_lib.Session()
    all_results = []

    for i, product in enumerate(products):
        brand = product["brand"]
        strategy = product["strategy"]
        log(f"[{i+1}/{len(products)}] {brand} ({strategy})")

        try:
            extractor = strategies.get(strategy)
            if not extractor:
                log(f"  !! Unknown strategy: {strategy}")
                scraped = None
            else:
                scraped = extractor(product, session, log)
        except Exception as e:
            log(f"  !! Exception: {e}")
            scraped = None

        # Normalise
        if scraped is None:
            entries = [(None, None)]
        elif isinstance(scraped, list):
            entries = [(s, s.get("variant_label")) for s in scraped]
        else:
            entries = [(scraped, None)]

        for scraped_item, vlabel in entries:
            display_brand = f"{brand} ({vlabel})" if vlabel else brand

            k_amt, k_dos, k_pri = known.get(display_brand,
                                    known.get(brand, (None, None, None)))
            s_pri = scraped_item["price"]   if scraped_item else None
            s_amt = scraped_item["amount"]  if scraped_item else None
            s_dos = scraped_item["dosage"]  if scraped_item else None

            final_pri = s_pri if s_pri is not None else k_pri
            final_amt = s_amt if s_amt is not None else k_amt
            final_dos = s_dos if s_dos is not None else k_dos

            theo_cost = prac_cost = prac_caps = None
            if all([final_pri, final_amt, final_dos]) and final_dos > 0:
                theo_caps = target / final_dos
                theo_cost = (theo_caps / final_amt) * final_pri
                prac_caps = math.ceil(target / final_dos)
                prac_cost = (prac_caps / final_amt) * final_pri

            if all([s_pri, s_amt, s_dos]):
                data_src = "live"
            elif any([s_pri, s_amt, s_dos]):
                data_src = "mixed"
            else:
                data_src = "spreadsheet"

            price_changed = False
            if s_pri is not None and k_pri is not None and abs(s_pri - k_pri) > 0.50:
                price_changed = True

            all_results.append({
                "brand": display_brand,
                "url": product["url"],
                "strategy": strategy,
                "vegan": product.get("vegan", False),
                "scraped_price": s_pri,
                "scraped_amount": s_amt,
                "scraped_dosage": s_dos,
                "known_price": k_pri,
                "known_amount": k_amt,
                "known_dosage": k_dos,
                "final_price": final_pri,
                "final_amount": final_amt,
                "final_dosage": final_dos,
                "theo_cost": theo_cost,
                "prac_cost": prac_cost,
                "prac_caps": prac_caps,
                "data_source": data_src,
                "price_changed": price_changed,
            })

        time.sleep(0.5)

    # Sort by practical daily cost
    ranked = sorted(
        [r for r in all_results if r["prac_cost"] is not None],
        key=lambda r: r["prac_cost"]
    )
    unranked = [r for r in all_results if r["prac_cost"] is None]
    return ranked + unranked


def main():
    # Determine output path (relative to repo root)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(repo_root, "public", "data")
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "results.json")

    print(f"\n  STACKARB — Scraper Runner")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Output: {output_file}\n")

    output = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "supplements": {},
    }

    for key, config in SUPPLEMENT_CONFIG.items():
        print(f"\n{'='*60}")
        print(f"  Scraping: {key} ({len(config['products'])} products)")
        print(f"{'='*60}")

        results = run_scraper(key)

        output["supplements"][key] = {
            "target_dose": config["target_dose"],
            "dose_label": config["dose_label"],
            "results": results,
        }

        live_count = sum(1 for r in results if r["data_source"] == "live")
        print(f"\n  {key}: {live_count}/{len(results)} live")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n  Done! JSON written to {output_file}")
    print(f"  Total size: {os.path.getsize(output_file) / 1024:.1f} KB")

    # ── Append to history.json ─────────────────────────────────────
    history_file = os.path.join(output_dir, "history.json")
    history = {"snapshots": []}
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = {"snapshots": []}

    # Build snapshot: { date, omega3: {brand: cost, ...}, ... }
    snapshot = {"date": output["scraped_at"]}
    for key, supp in output["supplements"].items():
        costs = {}
        for r in supp["results"]:
            if r["prac_cost"] is not None:
                costs[r["brand"]] = round(r["prac_cost"], 6)
        snapshot[key] = costs
    history["snapshots"].append(snapshot)

    # Cap at 365 entries (~6 months at 2x/day)
    MAX_SNAPSHOTS = 365
    if len(history["snapshots"]) > MAX_SNAPSHOTS:
        history["snapshots"] = history["snapshots"][-MAX_SNAPSHOTS:]

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=None, ensure_ascii=False)

    print(f"  History: {len(history['snapshots'])} snapshots in {history_file}")
    print(f"  History size: {os.path.getsize(history_file) / 1024:.1f} KB\n")


if __name__ == "__main__":
    main()
