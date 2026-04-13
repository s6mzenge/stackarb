"""
LYCOPENE -- FOCUSED SCRAPER v1
================================
Run:  python lycopene_scraper.py

Adapted from astaxanthin_scraper v2.
Lycopene dosage extraction is very similar to astaxanthin:
  - Usually "Xmg lycopene per softgel/capsule/tablet"
  - Or "Lycopene Xmg" in title / nutritional info
  - Serving sizes are almost always 1 capsule/tablet

Strategies:
  - shopify:  Supplemented, Ved Healthcare
  - ebay:     Supplemented (eBay DE listing)
  - iherb:    iHerb products (3) — via cloudscraper (Cloudflare bypass)

Output -> C:\\Users\\morit\\Documents\\Sonstiges\\Dokumente\\lycopene_scrape_results.txt
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

OUTPUT_DIR  = r"C:\Users\morit\Documents\Sonstiges\Dokumente"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "lycopene_scrape_results.txt")

TARGET_DOSE = 50  # mg Lycopene per day

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
    "Supplemented":              (540, 50, 33.99),
    "Supplemented (eBay 90)":    ( 90, 50,  7.99),
    "Supplemented (eBay 180)":   (180, 50, 11.99),
    "Ved":                       ( 90, 50, 10.49),
    "21st Century":              ( 60, 25, 10.54),
    "Nutricost":                 (120, 20, 17.39),
    "Swanson":                   ( 60, 20, 10.08),
}

PRODUCTS = [
    {"brand": "Supplemented",
     "url": "https://www.supplemented.co.uk/products/lycopene-50mg-capsules",
     "strategy": "shopify", "variant_hint": "540"},

    {"brand": "Supplemented (eBay 90)",
     "url": "https://www.ebay.co.uk/itm/174608766060",
     "strategy": "ebay", "variant_hint": None},

    {"brand": "Supplemented (eBay 180)",
     "url": "https://www.ebay.co.uk/itm/175263940550",
     "strategy": "ebay", "variant_hint": None},

    {"brand": "Ved",
     "url": "https://vedhealthcare.com/products/ved-lycopene-supplement-50mg-x-90-softgel",
     "strategy": "shopify", "variant_hint": "Default"},

    {"brand": "21st Century",
     "url": "https://uk.iherb.com/pr/21st-century-lycopene-25-mg-60-tablets/3359",
     "strategy": "iherb", "variant_hint": None},

    {"brand": "Nutricost",
     "url": "https://uk.iherb.com/pr/nutricost-lycopene-20-mg-120-softgels/136835",
     "strategy": "iherb", "variant_hint": None},

    {"brand": "Swanson",
     "url": "https://uk.iherb.com/pr/swanson-lycopene-20-mg-60-softgels/116298",
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


# ── LYCOPENE DOSAGE EXTRACTION ────────────────────────────────────────────

def extract_lycopene_dosage(title, body, page_text, log):
    """
    Extract lycopene dosage per capsule/tablet.

    Same priority system as astaxanthin — lycopene products almost always
    have 1 capsule/tablet = 1 serving, with dosage stated directly.

    PRIORITY 1 — "per softgel/capsule/tablet" patterns (direct per-cap):
      "50mg Lycopene per capsule"
      "Lycopene 25mg per tablet"

    PRIORITY 2 — Nutritional table with explicit serving size:
      "Per Serving (2 Tablets) — Lycopene 50mg"

    PRIORITY 3 — First lycopene value found + serving size from page:
      Use FIRST value, divide by detected serving.

    PRIORITY 4 — Title mg extraction (e.g. "Lycopene 50mg" in product name)
    """
    all_text = f"{title} ||| {body} ||| {page_text}"
    all_text = words_to_digits(all_text)

    # ================================================================
    # PRIORITY 1: "Xmg lycopene ... per softgel/capsule/tablet" (direct per-cap)
    # ================================================================
    per_cap_patterns = [
        r"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?lycopene[^.]*?per\s+(?:soft\s*gel|capsule|tablet)",
        r"lycopene\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg[^.]*?per\s+(?:soft\s*gel|capsule|tablet)",
        r"per\s+(?:soft\s*gel|capsule|tablet)\s*[:\-]?\s*[^.]*?lycopene\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        r"each\s+(?:soft\s*gel|capsule|tablet)[^.]*?(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?lycopene",
        r"each\s+(?:soft\s*gel|capsule|tablet)[^.]*?lycopene\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
    ]
    for pattern in per_cap_patterns:
        for m in re.finditer(pattern, all_text, re.I):
            val = float(m.group(1))
            if 1 <= val <= 200:
                preceding = all_text[max(0, m.start() - 30):m.start()].lower()
                if re.search(r"(?:less\s+than|under|below|fewer\s+than|up\s+to)\s*$", preceding):
                    log(f"    [P1] Skipping negated match: '{preceding[-25:].strip()}...{val}mg'")
                    continue
                log(f"    [P1] Lycopene per capsule directly: {val}mg")
                return val

    # ================================================================
    # PRIORITY 2: Nutritional table lycopene + table serving size
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

    lyc_patterns = [
        r"lycopene\s*(?:\([^)]*\))?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        r"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?lycopene\b",
        r"lycopene[^0-9]{0,20}?(\d+(?:\.\d+)?)\s*mg",
    ]

    lyc_values = []
    for pattern in lyc_patterns:
        matches = re.findall(pattern, all_text, re.I)
        for m in matches:
            val = float(m)
            if 1 <= val <= 200:
                lyc_values.append(val)

    seen = set()
    unique_lyc = []
    for v in lyc_values:
        if v not in seen:
            seen.add(v)
            unique_lyc.append(v)

    if unique_lyc and table_serving:
        first_lyc = unique_lyc[0]
        per_cap = first_lyc / table_serving
        log(f"    [P2] Lycopene values found: {unique_lyc}")
        log(f"    [P2] Using first: {first_lyc}mg / {table_serving} = {per_cap:.1f}mg per cap")
        if 1 <= per_cap <= 200:
            return per_cap

    # ================================================================
    # PRIORITY 3: First lycopene + general serving size
    # ================================================================
    if unique_lyc:
        log(f"    [P3] Lycopene values found: {unique_lyc}")

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

        first_lyc = unique_lyc[0]
        per_cap = first_lyc / serving

        if serving_confidence == "tier2" and per_cap < 5 and first_lyc >= 10:
            log(f"    [P3] Tier2 serving {serving} gives {per_cap:.1f}mg/cap — too low, "
                f"likely a daily-intake recommendation not a serving size")
            log(f"    [P3] Falling back to serving=1, lycopene={first_lyc:.1f}mg per cap")
            serving = 1
            per_cap = first_lyc

        log(f"    [P3] Using first: {first_lyc}mg / {serving} = {per_cap:.1f}mg per cap")
        if 1 <= per_cap <= 200:
            return per_cap

    # ================================================================
    # PRIORITY 4: Title mg extraction fallback
    # ================================================================
    log(f"    [P4] Trying title mg fallback...")
    m = re.search(r"(\d+(?:\.\d+)?)\s*mg", title, re.I)
    if m:
        val = float(m.group(1))
        if 1 <= val <= 200:
            log(f"    [P4] Title mg: {val}mg (assuming per capsule)")
            return val

    log(f"    !! Could not determine lycopene dosage")
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


# ── DOMAIN-SPECIFIC EXTRACTORS ─────────────────────────────────────────────

def _variant_pack_multiplier(variant_title):
    """Detect pack variants like '3 Pack', 'Three Pack', '6 Pack'."""
    _PACK_WORDS = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    m = re.search(r"(\d+)\s*[-\s]*packs?\b", variant_title, re.I)
    if m:
        return int(m.group(1))
    for word, num in _PACK_WORDS.items():
        if re.search(rf"\b{word}\s*[-\s]*packs?\b", variant_title, re.I):
            return num
    return None


def extract_shopify(product, session, log):
    url = product["url"]

    log(f"    Fetching Shopify JSON...")
    sj = fetch_shopify_json(url, session)
    if not sj or "product" not in sj:
        log(f"    !! Shopify JSON not available")
        return None

    pj = sj["product"]
    title = pj.get("title", "")
    body = re.sub(r"<[^>]+>", " ", pj.get("body_html", ""))
    variants = pj.get("variants", [])

    log(f"    Title: {title}")
    log(f"    Variants ({len(variants)}):")
    for v in variants:
        log(f"      {v.get('title','?'):35s}  GBP {v.get('price','?'):>8s}  SKU: {v.get('sku','?')}")

    # Fetch page HTML once (shared across variants) for dosage info
    log(f"    Fetching page HTML for nutritional info...")
    status, html = fetch_page(url, session)
    page_text = ""
    if status == 200:
        soup = BeautifulSoup(html, "lxml")
        page_text = soup.get_text(" ", strip=True)
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            page_text = og["content"] + " ||| " + page_text

    combined = f"{body} ||| {page_text}"
    dosage = extract_lycopene_dosage(title, body, combined, log)

    # ── Build a result for EVERY variant (size/pack) ───────────────
    is_single = (len(variants) <= 1 or
                 all((v.get("title") or "").strip().lower() in
                     ("", "default", "default title") for v in variants))

    if is_single:
        chosen = variants[0] if variants else None
        price = float(chosen["price"]) if chosen else None
        amount = extract_capsule_count(
            chosen.get("title", "") if chosen else "", title, body, log)
        return {"price": price, "amount": amount, "dosage": dosage}

    # Determine base capsule count for pack-multiplier variants
    base_count = extract_capsule_count("", title, body, log)
    if base_count is None and page_text:
        base_count = extract_capsule_count("", title, page_text, log)
    log(f"    Base capsule count (from product): {base_count}")

    results = []
    for v in variants:
        vt = (v.get("title") or "").strip()
        vprice = float(v["price"]) if v.get("price") else None
        log(f"    -- Variant '{vt}' --")
        log(f"       Price: GBP {vprice}")

        pack_mult = _variant_pack_multiplier(vt)
        if pack_mult and base_count:
            vamount = pack_mult * base_count
            log(f"       Pack variant: {pack_mult} x {base_count} = {vamount}")
        else:
            vamount = extract_capsule_count(vt, title, body, log)
            if vamount is None and page_text:
                vamount = extract_capsule_count(vt, title, page_text, log)

        log(f"       Amount: {vamount}")
        results.append({
            "price": vprice,
            "amount": vamount,
            "dosage": dosage,
            "variant_label": vt,
        })

    return results if results else None


def extract_ebay(product, session, log):
    """eBay listing — HTML scraping with JSON-LD fallback.

    eBay listings often contain structured data in JSON-LD and the title
    typically includes capsule count and dosage. Price is in various elements.
    eBay can be finicky with bot detection, so we fall back to spreadsheet.
    """
    url = product["url"]
    brand = product["brand"]

    # Strip tracking params for cleaner request
    clean_url = url.split("?")[0]
    log(f"    Fetching eBay page...")
    status, html = fetch_page(clean_url, session)
    if status != 200:
        log(f"    !! HTTP {status}")
        # Try with cloudscraper
        if HAS_CLOUDSCRAPER:
            log(f"    Trying cloudscraper...")
            try:
                scraper = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "desktop": True},
                )
                resp = scraper.get(clean_url, timeout=30)
                status = resp.status_code
                html = resp.text
            except Exception as e:
                log(f"    !! cloudscraper failed: {e}")
                status = None

        if status != 200:
            log(f"    Falling back to spreadsheet values")
            if brand in KNOWN:
                amt, dos, pri = KNOWN[brand]
                return {"price": pri, "amount": amt, "dosage": dos}
            return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    log(f"    Title: {title[:120]}")

    # Check for CAPTCHA / bot detection
    lower = html[:5000].lower()
    if any(kw in lower for kw in ["captcha", "robot", "verify yourself",
                                    "security measure"]):
        log(f"    !! Bot detection triggered — falling back to spreadsheet")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    # ── Price extraction ───────────────────────────────────────────
    price = None

    # JSON-LD (eBay often includes Product structured data)
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
                        log(f"    Price from JSON-LD: {price}")
                    elif isinstance(offers, list):
                        for o in offers:
                            if o.get("price"):
                                price = float(o["price"])
                                log(f"    Price from JSON-LD (list): {price}")
                                break
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # eBay price elements (various selectors used across eBay regions)
    if not price:
        for selector in [
            {"class_": re.compile(r"x-price-primary", re.I)},
            {"class_": re.compile(r"mainPrice", re.I)},
            {"id": "prcIsum"},
            {"itemprop": "price"},
        ]:
            els = soup.find_all(["span", "div"], **selector)
            for el in els:
                text = el.get_text(strip=True)
                # Match GBP, EUR, or plain currency patterns
                m = re.search(r"(?:GBP|EUR|£|€)\s*(\d+[.,]\d{2})", text)
                if not m:
                    m = re.search(r"(\d+[.,]\d{2})\s*(?:GBP|EUR|£|€)", text)
                if m:
                    val = float(m.group(1).replace(",", "."))
                    if 1 < val < 500:
                        price = val
                        log(f"    Price from element: {price}")
                        break
            if price:
                break

    # Fallback: first price in page text
    if not price:
        for pattern in [r"£(\d+\.\d{2})", r"EUR\s*(\d+[.,]\d{2})", r"€(\d+[.,]\d{2})"]:
            m = re.search(pattern, page_text[:8000])
            if m:
                val = float(m.group(1).replace(",", "."))
                if 2 < val < 500:
                    price = val
                    log(f"    Price from page text: {price}")
                    break

    log(f"    -> Price: GBP {price}")

    # ── Capsule count ──────────────────────────────────────────────
    amount = None
    m = re.search(r"(\d+)\s*(?:veggie\s+)?(?:soft\s*gel|capsule|tablet|count)s?\b", title, re.I)
    if m and 10 <= int(m.group(1)) <= 2000:
        amount = int(m.group(1))
        log(f"    Count from title: {amount}")
    if not amount:
        amount = extract_capsule_count("", title, page_text[:5000], log)

    # ── Dosage ─────────────────────────────────────────────────────
    dosage = extract_lycopene_dosage(title, page_text[:5000], page_text, log)

    # ── Multi-buy tier extraction ──────────────────────────────────
    # eBay "UP TO 20% OFF WITH MULTI-BUY" listings show tiered pricing:
    #   "Buy 1 £7.99 each", "Buy 2 £7.19 each", "4 or more for £6.39 each"
    # Each tier is a different value proposition, so expand them like variants.
    multibuy_tiers = []

    # Pattern 1: "Buy N £X.XX each"  /  "Buy N — £X.XX each"
    for m in re.finditer(
        r"Buy\s+(\d+)\s*[—–\-]?\s*£(\d+\.?\d{0,2})\s*each", page_text, re.I
    ):
        qty, unit_price = int(m.group(1)), float(m.group(2))
        if 1 <= qty <= 20 and 0.50 < unit_price < 500:
            multibuy_tiers.append((qty, unit_price))

    # Pattern 2: "N or more for £X.XX each"
    for m in re.finditer(
        r"(\d+)\s+or\s+more\s+(?:for\s+)?£(\d+\.?\d{0,2})\s*each", page_text, re.I
    ):
        qty, unit_price = int(m.group(1)), float(m.group(2))
        if 1 <= qty <= 20 and 0.50 < unit_price < 500:
            multibuy_tiers.append((qty, unit_price))

    # Deduplicate by qty, keep first seen
    seen_qty = set()
    unique_tiers = []
    for qty, unit_price in multibuy_tiers:
        if qty not in seen_qty:
            seen_qty.add(qty)
            unique_tiers.append((qty, unit_price))

    if unique_tiers:
        unique_tiers.sort(key=lambda t: t[0])
        log(f"    Multi-buy tiers found: {unique_tiers}")

        results = []
        for qty, unit_price in unique_tiers:
            label = f"Buy {qty}" if qty < 4 else f"Buy {qty}+"
            log(f"      {label}: £{unit_price:.2f}/unit x {qty} = £{unit_price*qty:.2f} for {qty*amount if amount else '?'} caps")
            results.append({
                "price": unit_price,
                "amount": amount,
                "dosage": dosage,
                "variant_label": label,
            })
        return results

    return {"price": price, "amount": amount, "dosage": dosage}


def extract_iherb(product, session, log):
    """iHerb — uses cloudscraper to bypass Cloudflare protection."""
    url = product["url"]
    brand = product["brand"]

    if not HAS_CLOUDSCRAPER:
        log(f"    !! cloudscraper not installed — falling back to spreadsheet")
        log(f"    !! Install with: pip install cloudscraper")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    log(f"    Fetching iHerb page via cloudscraper...")
    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True},
        )
        resp = scraper.get(url, timeout=30)
        status = resp.status_code
        html = resp.text
    except Exception as e:
        log(f"    !! cloudscraper failed: {e}")
        log(f"    Falling back to spreadsheet values")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    if status != 200:
        log(f"    !! HTTP {status} — falling back to spreadsheet")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    # Check for Cloudflare challenge page
    lower = html[:5000].lower()
    if any(kw in lower for kw in ["captcha", "challenge", "cf-browser-verification",
                                    "_cf_chl", "just a moment"]):
        log(f"    !! Cloudflare challenge not bypassed — falling back to spreadsheet")
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
    dosage = extract_lycopene_dosage(title, page_text, page_text, log)

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

    terminal.write(f"\n  LYCOPENE SCRAPER v1\n")
    terminal.write(f"  Scraping {len(PRODUCTS)} products...\n")
    terminal.write(f"  Output -> {OUTPUT_FILE}\n\n")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        sys.stdout = f
        session = requests.Session()

        print(SEP)
        print(f"  LYCOPENE -- SCRAPE RESULTS v1")
        print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Target daily dose: {TARGET_DOSE}mg Lycopene")
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
            elif strategy == "ebay":
                scraped = extract_ebay(product, session, log)
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
                print(f"                    {'SCRAPED':>12s}   {'SPREADSHEET':>12s}   {'MATCH':>6s}")
                print(f"    Price (GBP):    {fmt(s_pri):>12s}   {fmt(k_pri):>12s}   {match_check(s_pri, k_pri)}")
                print(f"    Capsules:       {fmt(s_amt):>12s}   {fmt(k_amt):>12s}   {match_check(s_amt, k_amt)}")
                print(f"    Lyco (mg/cap):  {fmt(s_dos):>12s}   {fmt(k_dos):>12s}   {match_check(s_dos, k_dos)}")

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
        print(f"#  LYCOPENE -- DAILY COST RANKING (practical)")
        print(f"#  Target: {TARGET_DOSE}mg Lycopene/day")
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
                issues.append("Lycopene dosage not scraped")

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