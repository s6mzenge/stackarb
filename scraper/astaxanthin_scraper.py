"""
ASTAXANTHIN -- FOCUSED SCRAPER v2
==================================
Run:  python astaxanthin_scraper.py

Adapted from omega3_scraper v5.
Astaxanthin dosage extraction is simpler than EPA:
  - Usually "Xmg astaxanthin per softgel/capsule"
  - Or "Astaxanthin Xmg" in title / nutritional info
  - Serving sizes are almost always 1 capsule

Key v2 changes:
  - iHerb products now scraped LIVE via cloudscraper (Cloudflare bypass)
  - "blocked" strategy replaced with "iherb" strategy for all 5 iHerb products
  - Falls back to spreadsheet values if cloudscraper fails

Strategies:
  - shopify:  WeightWorld own site
  - dolphin:  Dolphin Fitness (Lamberts, Swanson) — JSON-LD + meta scraping
  - iherb:    iHerb products (5) — via cloudscraper (Cloudflare bypass)

Output -> C:\\Users\\morit\\Documents\\Sonstiges\\Dokumente\\astaxanthin_scrape_results.txt
Requirements:  pip install requests beautifulsoup4 lxml cloudscraper
"""

import requests, json, re, time, sys, os
from datetime import datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

try:
    from iherb_session import fetch_iherb_page
    HAS_IHERB_SESSION = True
except ImportError:
    HAS_IHERB_SESSION = False

OUTPUT_DIR  = r"C:\Users\morit\Documents\Sonstiges\Dokumente"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "astaxanthin_scrape_results.txt")

TARGET_DOSE = 24  # mg Astaxanthin per day

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Spreadsheet reference values: (amount, dosage_mg, price_gbp)
KNOWN = {
    "WeightWorld":                (240, 18, 18.49),
    "Micro Ingredients":          (120, 12, 30.48),
    "Nutricost (12mg)":           ( 60, 12, 22.62),
    "California Gold Nutrition":  ( 30, 12, 11.45),
    "Nutricost (4mg)":            (120,  4, 16.90),
    "Lamberts":                   ( 30,  4,  9.99),  # 8mg per 2 caps = 4mg/cap
    "Swanson":                    ( 60,  4, 10.49),
    "NOW Foods":                  ( 60, 10, 24.38),
}

PRODUCTS = [
    {"brand": "WeightWorld",
     "url": "https://www.weightworld.uk/products/astaxanthin-softgels",
     "strategy": "shopify", "variant_hint": "Default"},

    {"brand": "Micro Ingredients",
     "url": "https://uk.iherb.com/pr/micro-ingredients-astaxanthin-12-mg-120-softgels/148176",
     "strategy": "iherb", "variant_hint": None},

    {"brand": "Nutricost (12mg)",
     "url": "https://uk.iherb.com/pr/nutricost-astaxanthin-12-mg-60-softgels/139393",
     "strategy": "iherb", "variant_hint": None},

    {"brand": "California Gold Nutrition",
     "url": "https://uk.iherb.com/pr/california-gold-nutrition-astaxanthin-astalif-pure-icelandic-12-mg-30-veggie-softgels/71683",
     "strategy": "iherb", "variant_hint": None},

    {"brand": "Nutricost (4mg)",
     "url": "https://uk.iherb.com/pr/nutricost-astaxanthin-4-mg-120-softgels/132855",
     "strategy": "iherb", "variant_hint": None},

    {"brand": "Lamberts",
     "url": "https://www.dolphinfitness.co.uk/en/lamberts-astaxanthin-8mg-30-capsules/298576",
     "strategy": "dolphin", "variant_hint": None},

    {"brand": "Swanson",
     "url": "https://www.dolphinfitness.co.uk/en/swanson-astaxanthin-4-mg-60-softgels/247639",
     "strategy": "dolphin", "variant_hint": None},

    {"brand": "NOW Foods",
     "url": "https://uk.iherb.com/pr/now-foods-astaxanthin-10-mg-60-softgels/38292",
     "strategy": "iherb", "variant_hint": None},
]


# ── HTTP helpers ───────────────────────────────────────────────────────────

def fetch_page(url, session):
    try:
        resp = session.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        return resp.status_code, resp.text
    except Exception as e:
        return None, str(e)

def fetch_shopify_json(url, session):
    parsed = urlparse(url)
    path = parsed.path.split("?")[0]
    json_url = f"{parsed.scheme}://{parsed.netloc}{path}.json"
    try:
        resp = session.get(json_url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


# ── Word-number conversion helper ──────────────────────────────────────────

WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}

def words_to_digits(text):
    def _replace(m):
        return WORD_NUMBERS[m.group(1).lower()] + m.group(2)
    return re.sub(
        r"\b(" + "|".join(WORD_NUMBERS.keys()) + r")\b(\s+(?:soft\s*gels?|capsules?|tablets?))",
        _replace, text, flags=re.I
    )


# ── ASTAXANTHIN DOSAGE EXTRACTION ──────────────────────────────────────────

def extract_astaxanthin_dosage(title, body, page_text, log):
    """
    Extract astaxanthin dosage per capsule.

    Simpler than EPA extraction because:
      - Astaxanthin products almost always have 1 capsule = 1 serving
      - Dosage is typically stated directly: "12mg astaxanthin"
      - No complex EPA/DHA splitting needed

    PRIORITY 1 — "per softgel/capsule" patterns (direct per-cap):
      "12mg Astaxanthin per softgel"
      "Astaxanthin 12mg per capsule"

    PRIORITY 2 — Nutritional table with explicit serving size:
      "Per Serving (2 Softgels) — Astaxanthin 24mg"

    PRIORITY 3 — First astaxanthin value found + serving size from page:
      Use FIRST value, divide by detected serving.

    PRIORITY 4 — Title mg extraction (e.g. "Astaxanthin 12mg" in product name)
    """
    all_text = f"{title} ||| {body} ||| {page_text}"
    all_text = words_to_digits(all_text)

    # ================================================================
    # PRIORITY 1: "Xmg astaxanthin ... per softgel/capsule" (direct per-cap)
    # ================================================================
    per_cap_patterns = [
        # "12mg astaxanthin per softgel"
        r"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?astaxanthin[^.]*?per\s+(?:soft\s*gel|capsule|tablet)",
        # "Astaxanthin 12mg per capsule"
        r"astaxanthin\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg[^.]*?per\s+(?:soft\s*gel|capsule|tablet)",
        # "per softgel: Astaxanthin 12mg"
        r"per\s+(?:soft\s*gel|capsule|tablet)\s*[:\-]?\s*[^.]*?astaxanthin\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        # "each softgel ... 12mg astaxanthin"
        r"each\s+(?:soft\s*gel|capsule|tablet)[^.]*?(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?astaxanthin",
        # "each softgel ... astaxanthin 12mg"
        r"each\s+(?:soft\s*gel|capsule|tablet)[^.]*?astaxanthin\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
    ]
    for pattern in per_cap_patterns:
        for m in re.finditer(pattern, all_text, re.I):
            val = float(m.group(1))
            if 1 <= val <= 100:
                # Reject matches preceded by negation: "less than 1mg", "under 1mg"
                preceding = all_text[max(0, m.start() - 30):m.start()].lower()
                if re.search(r"(?:less\s+than|under|below|fewer\s+than|up\s+to)\s*$", preceding):
                    log(f"    [P1] Skipping negated match: '{preceding[-25:].strip()}...{val}mg'")
                    continue
                log(f"    [P1] Astaxanthin per capsule directly: {val}mg")
                return val

    # ================================================================
    # PRIORITY 2: Nutritional table astaxanthin + table serving size
    # ================================================================
    table_serving = None
    table_serving_patterns = [
        r"[Pp]er\s+[Ss]erving\s*\(\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*\)",
        r"[Pp]er\s+(\d+)\s+(?:soft\s*gel|capsule|tablet)s?\s*(?:\)|$|[A-Z]|\n)",
        r"[Ss]erving\s*[Ss]ize\s*[:\-]\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        r"[Nn]utritional\s+[Ii]nformation\s+per\s+(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        r"[Aa]mount\s+[Pp]er\s+(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        r"[Pp]er\s+[Dd]aily\s+[Ii]ntake\s*\(\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*\)",
    ]
    for pattern in table_serving_patterns:
        m = re.search(pattern, all_text, re.I)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 10:
                table_serving = n
                log(f"    [P2] Table serving size: {n}")
                break

    # Find all astaxanthin values
    asta_patterns = [
        r"astaxanthin\s*(?:\([^)]*\))?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        r"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?astaxanthin\b",
        r"astaxanthin[^0-9]{0,20}?(\d+(?:\.\d+)?)\s*mg",
    ]

    asta_values = []
    for pattern in asta_patterns:
        matches = re.findall(pattern, all_text, re.I)
        for m in matches:
            val = float(m)
            if 1 <= val <= 100:
                asta_values.append(val)

    # Deduplicate preserving order
    seen = set()
    unique_asta = []
    for v in asta_values:
        if v not in seen:
            seen.add(v)
            unique_asta.append(v)

    if unique_asta and table_serving:
        first_asta = unique_asta[0]
        per_cap = first_asta / table_serving
        log(f"    [P2] Astaxanthin values found: {unique_asta}")
        log(f"    [P2] Using first: {first_asta}mg / {table_serving} = {per_cap:.1f}mg per cap")
        if 1 <= per_cap <= 100:
            return per_cap

    # ================================================================
    # PRIORITY 3: First astaxanthin + general serving size
    # ================================================================
    if unique_asta:
        log(f"    [P3] Astaxanthin values found: {unique_asta}")

        serving = 1
        serving_confidence = "default"

        tier1_serving_patterns = [
            r"for\s+(\d+)\s*(?:soft\s*gel|capsule|cap|tablet)s?\b",
            r"serving\s*size\s*[:\-]?\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
            r"per\s+(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\b",
        ]

        tier2_daily_patterns = [
            r"(?:take|directions?\s*[:\-]?\s*(?:take)?)\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
            r"daily\s+dose\s*[:\-]?\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
            r"(?<![-–])(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*per\s*day",
            r"(?<![-–])(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*a\s*day",
        ]

        for pattern in tier1_serving_patterns:
            m = re.search(pattern, all_text, re.I)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 10:
                    serving = n
                    serving_confidence = "tier1"
                    log(f"    [P3] Serving size: {serving} (tier1)")
                    break

        if serving_confidence == "default":
            for pattern in tier2_daily_patterns:
                m = re.search(pattern, all_text, re.I)
                if m:
                    n = int(m.group(1))
                    if 1 <= n <= 10:
                        serving = n
                        serving_confidence = "tier2"
                        log(f"    [P3] Serving size: {serving} (tier2/daily-intake)")
                        break

        if serving == 1:
            log(f"    [P3] Serving size: 1 (default)")

        first_asta = unique_asta[0]
        per_cap = first_asta / serving

        # Sanity check for tier2 (daily-intake) patterns
        if serving_confidence == "tier2" and per_cap < 2 and first_asta >= 4:
            log(f"    [P3] Tier2 serving {serving} gives {per_cap:.1f}mg/cap — too low, "
                f"likely a daily-intake recommendation not a serving size")
            log(f"    [P3] Falling back to serving=1, astaxanthin={first_asta:.1f}mg per cap")
            serving = 1
            per_cap = first_asta

        log(f"    [P3] Using first: {first_asta}mg / {serving} = {per_cap:.1f}mg per cap")
        if 1 <= per_cap <= 100:
            return per_cap

    # ================================================================
    # PRIORITY 4: Title mg extraction fallback
    # ================================================================
    log(f"    [P4] Trying title mg fallback...")
    m = re.search(r"(\d+(?:\.\d+)?)\s*mg", title, re.I)
    if m:
        val = float(m.group(1))
        if 1 <= val <= 100:
            log(f"    [P4] Title mg: {val}mg (assuming per capsule)")
            return val

    log(f"    !! Could not determine astaxanthin dosage")
    return None


# ── CAPSULE COUNT EXTRACTION ───────────────────────────────────────────────

def extract_capsule_count(variant_title, product_title, body_text, log):
    if variant_title:
        m = re.search(r"(\d+)\s*x\s*(\d+)", variant_title, re.I)
        if m:
            n_packs, per_pack = int(m.group(1)), int(m.group(2))
            total = n_packs * per_pack
            if total >= 10:
                log(f"    Count from variant 'NxM': {n_packs} x {per_pack} = {total}")
                return total
        m = re.search(r"(\d+)\s*(?:count|capsules|softgels|caps|tablets)?", variant_title, re.I)
        if m and int(m.group(1)) >= 10:
            log(f"    Count from variant title: {m.group(1)}")
            return int(m.group(1))

    # Allow adjectives between number and capsule word: "270 High Strength Capsules"
    m = re.search(r"(\d+)[\s\-]+(?:(?:high|extra|super|full|pure)[\s\-]+)?(?:strength\s+)?(?:soft\s*gel|capsule|tablet|veggie\s+soft\s*gel)s?\b", product_title, re.I)
    if not m:
        m = re.search(r"(\d+)[\s\-]*(?:capsules|softgels|tablets|soft\s*gels|caps)\b", product_title, re.I)
    if m and 10 <= int(m.group(1)) <= 2000:
        log(f"    Count from product title: {m.group(1)}")
        return int(m.group(1))

    matches = re.findall(r"(\d+)[\s\-]*(?:(?:high|extra|super)[\s\-]+)?(?:strength\s+)?(?:soft\s*gel|capsule|tablet)s?\b", body_text, re.I)
    if not matches:
        matches = re.findall(r"(\d+)[\s\-]*(?:capsules|softgels|tablets|soft\s*gels)\b", body_text, re.I)
    for m in matches:
        if 20 <= int(m) <= 1000:
            log(f"    Count from body text: {m}")
            return int(m)

    m = re.search(r"(\d+)\s*count\b", f"{variant_title} {product_title}", re.I)
    if m and int(m.group(1)) >= 10:
        log(f"    Count from 'X count': {m.group(1)}")
        return int(m.group(1))

    log(f"    !! Could not extract capsule count")
    return None

def extract_dolphin(product, session, log):
    """Dolphin Fitness — typical retail site with JSON-LD or meta tags."""
    url = product["url"]
    log(f"    Fetching Dolphin Fitness page...")
    status, html = fetch_page(url, session)
    if status != 200:
        log(f"    !! HTTP {status}")
        return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
    log(f"    Title: {title[:120]}")

    # Try JSON-LD for price
    price = None
    name = title
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string)
            items = [data]
            if isinstance(data, dict) and "@graph" in data:
                items = data["@graph"] if isinstance(data["@graph"], list) else [data["@graph"]]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "Product" in str(item.get("@type", "")):
                    name = item.get("name", name)
                    log(f"    JSON-LD Product: {name}")
                    offers = item.get("offers", {})
                    if isinstance(offers, dict) and offers.get("price"):
                        price = float(offers["price"])
                    elif isinstance(offers, list):
                        for o in offers:
                            p = o.get("price")
                            avail = o.get("availability", "")
                            if p and ("InStock" in avail or not avail):
                                price = float(p)
                    if not price:
                        off = item.get("offers", {})
                        if isinstance(off, dict):
                            p = off.get("lowPrice") or off.get("price")
                            if p:
                                price = float(p)
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: meta tags
    if not price:
        for meta_name in ["product:price:amount", "og:price:amount"]:
            tag = soup.find("meta", property=meta_name)
            if tag and tag.get("content"):
                try:
                    price = float(tag["content"])
                    log(f"    Meta price ({meta_name}): GBP {price}")
                except ValueError:
                    pass

    # Fallback: find price in page text
    if not price:
        m = re.search(r"£(\d+\.\d{2})", page_text)
        if m:
            price = float(m.group(1))
            log(f"    Price from page text: GBP {price}")

    log(f"    -> Price: GBP {price}")
    amount = extract_capsule_count("", name, page_text, log)
    dosage = extract_astaxanthin_dosage(name, page_text, page_text, log)
    return {"price": price, "amount": amount, "dosage": dosage}


def extract_iherb(product, session, log):
    """iHerb — uses shared persistent session (curl_cffi → cloudscraper)."""
    url = product["url"]
    brand = product["brand"]

    if not HAS_IHERB_SESSION:
        log(f"    !! iherb_session module not available — falling back to spreadsheet")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    log(f"    Fetching iHerb page via shared session...")
    status, html = fetch_iherb_page(url, log)

    if status is None or status != 200 or not html:
        log(f"    !! iHerb fetch failed — falling back to spreadsheet")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    log(f"    Title: {title[:120]}")
    log(f"    Page size: {len(html)} bytes")

    # ── Price extraction ───────────────────────────────────────────
    price = None

    # JSON-LD (iHerb often includes structured data)
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "@graph" in item:
                    items.extend(item["@graph"] if isinstance(item["@graph"], list)
                                 else [item["@graph"]])
                    continue
                if "Product" in str(item.get("@type", "")):
                    offers = item.get("offers", {})
                    if isinstance(offers, dict) and offers.get("price"):
                        price = float(offers["price"])
                        log(f"    Price from JSON-LD: GBP {price}")
                    elif isinstance(offers, list):
                        for o in offers:
                            if o.get("price"):
                                price = float(o["price"])
                                log(f"    Price from JSON-LD (list): GBP {price}")
                                break
                    if not price:
                        for k in ("lowPrice", "price"):
                            p = (offers if isinstance(offers, dict) else {}).get(k)
                            if p:
                                price = float(p)
                                log(f"    Price from JSON-LD ({k}): GBP {price}")
                                break
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Meta tags
    if not price:
        for prop in ["product:price:amount", "og:price:amount"]:
            tag = soup.find("meta", property=prop)
            if tag and tag.get("content"):
                try:
                    price = float(tag["content"])
                    log(f"    Price from meta {prop}: GBP {price}")
                except ValueError:
                    pass

    # iHerb-specific price elements
    if not price:
        for selector in [
            {"class_": re.compile(r"price", re.I)},
            {"id": re.compile(r"price", re.I)},
        ]:
            els = soup.find_all(["span", "div", "p", "b"], **selector)
            for el in els:
                text = el.get_text(strip=True)
                m = re.search(r"[£$€](\d+\.?\d*)", text)
                if m:
                    val = float(m.group(1))
                    if 1 < val < 200:
                        price = val
                        log(f"    Price from element: GBP {price}")
                        break
            if price:
                break

    # Last resort: first £ in page
    if not price:
        prices = re.findall(r"£(\d+\.\d{2})", page_text[:5000])
        for p in prices:
            val = float(p)
            if 2 < val < 200:
                price = val
                log(f"    Price from page text: GBP {price}")
                break

    log(f"    -> Price: GBP {price}")

    # ── Capsule count ──────────────────────────────────────────────
    amount = None
    m = re.search(r"(\d+)\s*(?:veggie\s+)?(?:soft\s*gel|capsule|tablet|count)s?\b", title, re.I)
    if m and 10 <= int(m.group(1)) <= 500:
        amount = int(m.group(1))
        log(f"    Count from title: {amount}")
    if not amount:
        m = re.search(r"(\d+)\s*(?:veggie\s+)?(?:soft\s*gel|capsule|tablet)s?\b",
                       page_text[:3000], re.I)
        if m and 10 <= int(m.group(1)) <= 500:
            amount = int(m.group(1))
            log(f"    Count from page text: {amount}")

    # ── Dosage ─────────────────────────────────────────────────────
    dosage = extract_astaxanthin_dosage(title, page_text, page_text, log)

    return {"price": price, "amount": amount, "dosage": dosage}


# ── Cost calculation & formatting ──────────────────────────────────────────

def calc_costs(price, amount, dosage):
    if not all([price, amount, dosage]):
        return None, None, None, None
    theo_caps = TARGET_DOSE / dosage
    theo_cost = (theo_caps / amount) * price
    prac_caps = int(-(-TARGET_DOSE // dosage))  # ceil division
    prac_cost = (prac_caps / amount) * price
    return theo_cost, prac_cost, theo_caps, prac_caps

def fmt(val):
    if val is None:
        return "---"
    if isinstance(val, float):
        return f"{val:.2f}" if val == int(val) or val > 10 else f"{val:.1f}"
    return str(val)

def match_check(scraped, known):
    if scraped is None or known is None:
        return "---"
    if isinstance(scraped, (int, float)) and isinstance(known, (int, float)):
        if abs(scraped - known) < 0.01:
            return "YES"
        if abs(scraped - known) / max(known, 0.01) < 0.05:
            return "~YES"
        return "NO"
    return "YES" if scraped == known else "NO"


# ── Main ───────────────────────────────────────────────────────────────────

SEP  = "=" * 90
THIN = "-" * 90

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    terminal = sys.stderr
    original_stdout = sys.stdout

    terminal.write(f"\n  ASTAXANTHIN SCRAPER v2\n")
    terminal.write(f"  Scraping {len(PRODUCTS)} products...\n")
    terminal.write(f"  Output -> {OUTPUT_FILE}\n\n")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        sys.stdout = f
        session = requests.Session()

        print(SEP)
        print(f"  ASTAXANTHIN -- SCRAPE RESULTS v2")
        print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Target daily dose: {TARGET_DOSE}mg Astaxanthin")
        print(f"  Products: {len(PRODUCTS)}")
        print(SEP)

        all_results = []

        for i, product in enumerate(PRODUCTS):
            brand = product["brand"]
            strategy = product["strategy"]
            terminal.write(f"\r  [{i+1}/{len(PRODUCTS)}] {brand[:45]}...".ljust(60))
            terminal.flush()

            print(f"\n\n{SEP}")
            print(f"  [{i+1}] {brand}")
            print(f"  URL:      {product['url'][:100]}")
            print(f"  Strategy: {strategy}")
            print(THIN)

            def log(msg):
                print(msg)

            scraped = None
            if strategy == "shopify":
                scraped = extract_shopify(product, session, log)
            elif strategy == "dolphin":
                scraped = extract_dolphin(product, session, log)
            elif strategy == "iherb":
                scraped = extract_iherb(product, session, log)

            # Normalise to a list of (scraped_dict, variant_label) pairs
            if scraped is None:
                entries = [(None, None)]
            elif isinstance(scraped, list):
                entries = [(s, s.get("variant_label")) for s in scraped]
            else:
                entries = [(scraped, None)]

            for scraped_item, vlabel in entries:
                display_brand = f"{brand} ({vlabel})" if vlabel else brand

                k_amt, k_dos, k_pri = KNOWN.get(display_brand,
                                        KNOWN.get(brand, (None, None, None)))
                s_pri = scraped_item["price"]   if scraped_item else None
                s_amt = scraped_item["amount"]  if scraped_item else None
                s_dos = scraped_item["dosage"]  if scraped_item else None

                print(f"\n  -- COMPARISON -- {display_brand}")
                print(f"                   {'SCRAPED':>12s}   {'SPREADSHEET':>12s}   {'MATCH':>6s}")
                print(f"    Price (GBP):   {fmt(s_pri):>12s}   {fmt(k_pri):>12s}   {match_check(s_pri, k_pri)}")
                print(f"    Capsules:      {fmt(s_amt):>12s}   {fmt(k_amt):>12s}   {match_check(s_amt, k_amt)}")
                print(f"    Asta (mg/cap): {fmt(s_dos):>12s}   {fmt(k_dos):>12s}   {match_check(s_dos, k_dos)}")

                final_pri = s_pri if s_pri is not None else k_pri
                final_amt = s_amt if s_amt is not None else k_amt
                final_dos = s_dos if s_dos is not None else k_dos

                theo, prac, theo_caps, prac_caps = calc_costs(final_pri, final_amt, final_dos)

                src = "scraped" if all([s_pri, s_amt, s_dos]) else "mixed/fallback"
                print(f"\n  -- DAILY COST ({src}) --")
                if theo is not None:
                    print(f"    Theoretical: GBP {theo:.6f}/day  ({theo_caps:.1f} caps/day)")
                    print(f"    Practical:   GBP {prac:.6f}/day  ({prac_caps} caps/day)")
                else:
                    print(f"    !! Cannot calculate -- missing data")

                all_results.append({
                    "brand": display_brand, "s_pri": s_pri, "s_amt": s_amt, "s_dos": s_dos,
                    "f_pri": final_pri, "f_amt": final_amt, "f_dos": final_dos,
                    "theo": theo, "prac": prac, "prac_caps": prac_caps,
                    "strategy": strategy,
                })
            time.sleep(1.0)

        # ── RANKING ────────────────────────────────────────────────────
        print(f"\n\n{'#' * 90}")
        print(f"#  ASTAXANTHIN -- DAILY COST RANKING (practical)")
        print(f"#  Target: {TARGET_DOSE}mg Astaxanthin/day")
        print(f"{'#' * 90}\n")

        ranked = sorted([r for r in all_results if r["prac"] is not None], key=lambda r: r["prac"])

        print(f"  {'#':>3s}  {'Brand':<35s}  {'GBP/day':>10s}  {'Caps/d':>6s}  "
              f"{'Price':>8s}  {'Caps':>5s}  {'mg/cap':>6s}  {'Data':>10s}")
        print(f"  {'---':>3s}  {'---':<35s}  {'---':>10s}  {'---':>6s}  "
              f"{'---':>8s}  {'---':>5s}  {'---':>6s}  {'---':>10s}")

        for rank, r in enumerate(ranked, 1):
            flag = " <-- BEST" if rank == 1 else ""
            src = "LIVE"
            dos_str = f"{r['f_dos']:.1f}" if isinstance(r['f_dos'], float) else str(r['f_dos'])
            print(f"  {rank:>3d}  {r['brand']:<35s}  {r['prac']:>10.6f}  "
                  f"{r['prac_caps']:>6d}  "
                  f"{r['f_pri']:>8.2f}  {str(r['f_amt']):>5s}  "
                  f"{dos_str:>6s}  {src:>10s}{flag}")

        # ── ISSUES ─────────────────────────────────────────────────────
        print(f"\n\n{'#' * 90}")
        print(f"#  ISSUES & NOTES")
        print(f"{'#' * 90}\n")

        any_issues = False
        for r in all_results:
            issues = []
            if r["s_pri"] is None:
                issues.append("Price not scraped")
            if r["s_amt"] is None:
                issues.append("Capsule count not scraped")
            if r["s_dos"] is None:
                issues.append("Astaxanthin dosage not scraped")

            k_amt, k_dos, k_pri = KNOWN.get(r["brand"], (None, None, None))
            if r["s_pri"] and k_pri and abs(r["s_pri"] - k_pri) > 0.50:
                issues.append(f"Price changed: was GBP {k_pri}, now GBP {r['s_pri']}")
            if r["s_amt"] and k_amt and r["s_amt"] != k_amt:
                issues.append(f"Amount mismatch: scraped {r['s_amt']}, spreadsheet {k_amt}")
            if r["s_dos"] and k_dos and abs(r["s_dos"] - k_dos) > 1:
                issues.append(f"Dosage mismatch: scraped {r['s_dos']:.1f}mg, spreadsheet {k_dos}mg")

            if issues:
                any_issues = True
                print(f"  {r['brand']}")
                for iss in issues:
                    print(f"    !! {iss}")
                print()

        if not any_issues:
            print(f"  No issues -- all scraped values match spreadsheet!")

        print(f"\n  Report complete. {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    sys.stdout = original_stdout
    terminal.write(f"\r  Done! Report saved.".ljust(60) + "\n")
    terminal.write(f"  -> {OUTPUT_FILE}\n\n")


if __name__ == "__main__":
    main()
