"""
VEGAN OMEGA-3 -- FOCUSED SCRAPER v1
=====================================
Run:  python vegan_omega3_scraper.py

Adapted from vegan_omega3_test_scraper v4.
Tracks vegan omega-3 products (algal oil) for daily EPA cost comparison.

Strategies:
  - shopify:     WeightWorld, Time Health (Shopify JSON + page HTML)
  - magento:     Cytoplan (window.digitalData JS object)
  - vegetology:  Vegetology Opti3 (Craft CMS + Sprig v2 dynamic variants)
  - jsonld:      Troo Healthcare, Nature's Best (JSON-LD / meta / HTML)

Output -> C:\\Users\\morit\\Documents\\Sonstiges\\Dokumente\\vegan_omega3_scrape_results.txt
Requirements:  pip install requests beautifulsoup4 lxml cloudscraper
"""

import requests, json, re, time, sys, os, html as html_mod
from datetime import datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

OUTPUT_DIR  = r"C:\Users\morit\Documents\Sonstiges\Dokumente"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "vegan_omega3_scrape_results.txt")

TARGET_DOSE = 3000  # mg EPA per day (same as regular omega-3)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Spreadsheet reference values: (amount, dosage_mg_epa_per_cap, price_gbp)
# TODO: Fill these from your test scraper results
KNOWN = {
    "Cytoplan (Omega-3 Vegan)":          (60,  83,  30.99),   # ~166mg EPA per 2 caps
    "Troo Healthcare (Vegan Omega-3)":   (90,  200, 23.95),
    "WeightWorld (Vegan Omega-3)":       (120, 100, 19.99),
    "Vegetology (Opti3)":                (60,  100, 21.95),
    "Nature's Best (Vegan Omega-3)":     (60,  150, 17.95),
    "Time Health (Vegan Omega-3)":       (120, 165, 22.99),
}

PRODUCTS = [
    {"brand": "Cytoplan (Omega-3 Vegan)",
     "url": "https://www.cytoplan.co.uk/omega-3-vegan",
     "strategy": "magento", "variant_hint": None},

    {"brand": "Troo Healthcare (Vegan Omega-3)",
     "url": "https://www.troohealthcare.com/vegan-omega-3-algal-oil-90-softgels-epa-dha",
     "strategy": "v_jsonld", "variant_hint": None},

    {"brand": "WeightWorld (Vegan Omega-3)",
     "url": "https://www.weightworld.uk/products/vegan-omega-3-softgels",
     "strategy": "v_shopify", "variant_hint": "Default"},

    {"brand": "Vegetology (Opti3)",
     "url": "https://www.vegetology.com/supplements/omega-3",
     "strategy": "vegetology", "variant_hint": None},

    {"brand": "Nature's Best (Vegan Omega-3)",
     "url": "https://www.naturesbest.co.uk/vegan-omega-3s/vegan-omega-3-capsules-with-dha-and-epa/",
     "strategy": "v_jsonld", "variant_hint": None},

    {"brand": "Time Health (Vegan Omega-3)",
     "url": "https://timehealth.co.uk/products/vegan-omega3?selling_plan=690644287817&variant=52860775825737",
     "strategy": "v_shopify", "variant_hint": "Default"},
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


# ── EPA DOSAGE EXTRACTION ─────────────────────────────────────────────────

def extract_epa_dosage(title, body, page_text, log):
    """
    Extract EPA dosage per capsule — same priority system as omega3_scraper.

    PRIORITY 1 — "per softgel/capsule" patterns (direct per-cap)
    PRIORITY 2 — Nutritional table EPA + table serving size
    PRIORITY 3 — First EPA value found + serving size from page
    PRIORITY 4 — Percentage fallback (X% of algae oil)
    """
    all_text = f"{title} ||| {body} ||| {page_text}"
    all_text = words_to_digits(all_text)

    # ================================================================
    # PRIORITY 1: "Xmg EPA ... per softgel/capsule" (direct per-cap)
    # ================================================================
    _NC = r"(?!\s*(?:and|&|\+|/)\s*DHA)"

    per_cap_patterns = [
        rf"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?EPA{_NC}\b[^.]*?per\s+(?:soft\s*gel|capsule|tablet)",
        rf"EPA{_NC}\s*(?:\([^)]*\))?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg[^.]*?per\s+(?:soft\s*gel|capsule|tablet)",
        rf"per\s+(?:soft\s*gel|capsule|tablet)\s*[:\-]?\s*[^.]*?EPA{_NC}\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        rf"each\s+(?:soft\s*gel|capsule|tablet)[^.]*?(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?EPA{_NC}",
        rf"each\s+(?:soft\s*gel|capsule|tablet)[^.]*?EPA{_NC}\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
    ]
    for pattern in per_cap_patterns:
        m = re.search(pattern, all_text, re.I)
        if m:
            val = float(m.group(1))
            if 10 <= val <= 1500:
                log(f"    [P1] EPA per capsule directly: {val}mg")
                return val

    # ================================================================
    # PRIORITY 2: Nutritional table EPA + table serving size
    # ================================================================
    table_serving = None
    table_serving_patterns = [
        r"[Pp]er\s+[Ss]erving\s*\(\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*\)",
        r"[Pp]er\s+(\d+)\s+(?:soft\s*gel|capsule|tablet)s?\s*(?:\)|$|[A-Z]|\n)",
        r"[Ss]erving\s*[Ss]ize\s*[:\-]\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        r"[Nn]utritional\s+[Ii]nformation\s+per\s+(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        r"[Aa]mount\s+[Pp]er\s+(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        r"[Pp]er\s+[Dd]aily\s+[Ii]ntake\s*\(\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*\)",
        # Magento nutritional tables
        r"(\d+)\s+capsules?\s+will\s+provide",
        r"(\d+)\s+capsules?\s+provide\s+\d+\s*mg",
    ]
    for pattern in table_serving_patterns:
        m = re.search(pattern, all_text, re.I)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 10:
                table_serving = n
                log(f"    [P2] Table serving size: {n}")
                break

    # Identify combined "Xmg EPA and DHA" values to exclude
    combined_pattern = r"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?EPA\s*(?:and|&|\+|/)\s*DHA"
    combined_values = set()
    for m in re.finditer(combined_pattern, all_text, re.I):
        combined_values.add(float(m.group(1)))
    if combined_values:
        log(f"    [EPA] Combined EPA+DHA values excluded: {combined_values}")

    epa_patterns = [
        r"EPA\s*(?:\([^)]*\))?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        r"[Ee]icosapentaenoic\s+[Aa]cid\s*(?:\(EPA\))?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        r"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?EPA\b",
        r"EPA[^0-9]{0,15}?(\d+(?:\.\d+)?)\s*mg",
    ]

    epa_values = []
    for pattern in epa_patterns:
        matches = re.findall(pattern, all_text, re.I)
        for m in matches:
            val = float(m)
            if 10 <= val <= 2000 and val not in combined_values:
                epa_values.append(val)

    seen = set()
    unique_epa = []
    for v in epa_values:
        if v not in seen:
            seen.add(v)
            unique_epa.append(v)

    if unique_epa and table_serving:
        first_epa = unique_epa[0]
        per_cap = first_epa / table_serving
        log(f"    [P2] EPA values found: {unique_epa}")
        log(f"    [P2] Using first EPA: {first_epa}mg / {table_serving} = {per_cap:.1f}mg per cap")
        if 10 <= per_cap <= 1500:
            return per_cap

    # ================================================================
    # PRIORITY 3: First EPA + general serving size
    # ================================================================
    if unique_epa:
        log(f"    [P3] EPA values found: {unique_epa}")

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
            r"(?<![-\u2013])(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*per\s*day",
            r"(?<![-\u2013])(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*a\s*day",
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

        first_epa = unique_epa[0]
        per_cap = first_epa / serving

        if serving_confidence == "tier2" and per_cap < 100 and first_epa >= 100:
            log(f"    [P3] Tier2 serving {serving} gives {per_cap:.1f}mg/cap — too low, fallback to serving=1")
            serving = 1
            per_cap = first_epa

        log(f"    [P3] Using first EPA: {first_epa}mg / {serving} = {per_cap:.1f}mg per cap")
        if 10 <= per_cap <= 1500:
            return per_cap

    # ================================================================
    # PRIORITY 4: Percentage fallback
    # ================================================================
    log(f"    [P4] Trying percentage fallback...")
    pct = re.search(r"(\d+)/\d+\s*%", all_text)
    oil = re.search(r"(\d+)\s*mg\s*(?:fish\s*oil|algae?\s*oil|per\s*(?:soft\s*gel|capsule))", all_text, re.I)
    if not oil:
        oil = re.search(r"(\d+)\s*mg", title, re.I)
    if pct and oil:
        epa_pct = int(pct.group(1))
        oil_mg = int(oil.group(1))
        calc_epa = oil_mg * epa_pct / 100
        log(f"    [P4] {epa_pct}% of {oil_mg}mg = {calc_epa}mg EPA per cap")
        if 10 <= calc_epa <= 1500:
            return calc_epa

    log(f"    !! Could not determine EPA dosage")
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

    count_patterns = [
        r"(\d+)[\s\-]*(?:soft\s*gel\s+capsules?|capsules|softgels|tablets|soft\s*gels|caps)\b",
        r"(\d+)'s\b",  # Magento style: "60's"
    ]
    for pat in count_patterns:
        m = re.search(pat, product_title, re.I)
        if m and 10 <= int(m.group(1)) <= 2000:
            log(f"    Count from product title: {m.group(1)}")
            return int(m.group(1))

    for pat in count_patterns:
        matches = re.findall(pat, body_text, re.I)
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


# ── JSON extraction helper ─────────────────────────────────────────────────

def _extract_json_object(text, start):
    """Extract a JSON object starting at position `start` using brace counting."""
    i = start
    while i < len(text) and text[i] in ' \t\n\r':
        i += 1
    if i >= len(text) or text[i] != '{':
        return None
    start = i

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                json_str = text[start:i + 1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    return None
    return None


# ── Variant pack multiplier ────────────────────────────────────────────────

def _variant_pack_multiplier(variant_title):
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


# ── STRATEGY: SHOPIFY ──────────────────────────────────────────────────────

def _shopify_html_fallback(url, session, log, dosage_extractor):
    """Fallback when Shopify JSON API is blocked — scrape HTML directly."""
    log(f"    Trying HTML fallback...")
    status, html = fetch_page(url, session)
    if status != 200:
        log(f"    !! HTML fallback failed: HTTP {status}")
        return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
    log(f"    Title (HTML): {title[:120]}")

    # Price from JSON-LD → meta tags → page text
    price = None
    name = title
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
                    name = item.get("name", name)
                    log(f"    JSON-LD Product: {name[:100]}")
                    offers = item.get("offers", {})
                    if isinstance(offers, dict) and offers.get("price"):
                        p = float(offers["price"])
                        if p > 0:
                            price = p
                    elif isinstance(offers, list):
                        for o in offers:
                            p = o.get("price")
                            if p and float(p) > 0:
                                price = float(p)
                                break
                    if not price:
                        off = offers if isinstance(offers, dict) else {}
                        p = off.get("lowPrice") or off.get("price")
                        if p and float(p) > 0:
                            price = float(p)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        if price:
            log(f"    Price from JSON-LD: GBP {price}")
            break

    if not price:
        for prop in ["product:price:amount", "og:price:amount"]:
            tag = soup.find("meta", property=prop)
            if tag and tag.get("content"):
                try:
                    p = float(tag["content"])
                    if p > 0:
                        price = p
                        log(f"    Price from meta {prop}: GBP {price}")
                except ValueError:
                    pass

    if not price:
        for m_price in re.finditer(r"£(\d+\.\d{2})", page_text[:8000]):
            val = float(m_price.group(1))
            if 2 < val < 200:
                price = val
                log(f"    Price from page text: GBP {price}")
                break

    log(f"    -> Price: GBP {price}")
    amount = extract_capsule_count("", name, page_text, log)
    dosage = dosage_extractor(name, page_text, page_text, log)
    return {"price": price, "amount": amount, "dosage": dosage}


def extract_shopify(product, session, log):
    url = product["url"]

    log(f"    Fetching Shopify JSON...")
    sj = fetch_shopify_json(url, session)
    if not sj or "product" not in sj:
        log(f"    !! Shopify JSON not available — trying HTML fallback")
        return _shopify_html_fallback(url, session, log, extract_epa_dosage)

    pj = sj["product"]
    title = pj.get("title", "")
    body = re.sub(r"<[^>]+>", " ", pj.get("body_html", "") or "")
    variants = pj.get("variants", [])

    log(f"    Title: {title}")
    log(f"    Variants ({len(variants)}):")
    for v in variants:
        log(f"      {v.get('title','?'):35s}  GBP {v.get('price','?'):>8s}  SKU: {v.get('sku','?')}")

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
    dosage = extract_epa_dosage(title, body, combined, log)

    is_single = (len(variants) <= 1 or
                 all((v.get("title") or "").strip().lower() in
                     ("", "default", "default title") for v in variants))

    if is_single:
        chosen = variants[0] if variants else None
        price = float(chosen["price"]) if chosen else None
        amount = extract_capsule_count(
            chosen.get("title", "") if chosen else "", title, body, log)
        if amount is None and page_text:
            amount = extract_capsule_count("", title, page_text, log)
        return {"price": price, "amount": amount, "dosage": dosage}

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


# ── STRATEGY: JSON-LD / META / GENERIC HTML ────────────────────────────────

def extract_jsonld(product, session, log):
    """Generic HTML strategy — JSON-LD + meta tags + page text scraping."""
    url = product["url"]
    log(f"    Fetching page HTML...")
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

    # Price from JSON-LD
    price = None
    name = title
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string)
            items = [data]
            if isinstance(data, dict) and "@graph" in data:
                items = data["@graph"] if isinstance(data["@graph"], list) else [data["@graph"]]
            if isinstance(data, list):
                items = data
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "@graph" in item:
                    sub = item["@graph"]
                    items.extend(sub if isinstance(sub, list) else [sub])
                    continue
                if "Product" in str(item.get("@type", "")):
                    name = item.get("name", name)
                    log(f"    JSON-LD Product: {name[:100]}")
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
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Meta tags fallback
    if not price:
        for prop in ["product:price:amount", "og:price:amount"]:
            tag = soup.find("meta", property=prop)
            if tag and tag.get("content"):
                try:
                    price = float(tag["content"])
                    log(f"    Meta price ({prop}): GBP {price}")
                except ValueError:
                    pass

    # Page text fallback
    if not price:
        for m in re.finditer(r"£(\d+\.\d{2})", page_text[:8000]):
            val = float(m.group(1))
            if 2 < val < 200:
                price = val
                log(f"    Price from page text: GBP {price}")
                break

    log(f"    -> Price: GBP {price}")
    amount = extract_capsule_count("", name, page_text, log)
    dosage = extract_epa_dosage(name, page_text, page_text, log)
    return {"price": price, "amount": amount, "dosage": dosage}


# ── STRATEGY: MAGENTO (Cytoplan — digitalData) ────────────────────────────

def extract_magento(product, session, log):
    """Magento strategy — extract product data from window.digitalData.

    Cytoplan uses Magento 2 with the W3C Digital Data Layer extension.
    Product data (name, price, variants, description) is embedded in a
    <script> block as `window.digitalData = ({ ... });`

    EPA is extracted from the nutritional info table (div#nutritional.information).
    Variants come from linkedProduct[], filtering out subscriptions (SKU prefix "SB").
    """
    url = product["url"]
    log(f"    Fetching Magento page...")
    status, html = fetch_page(url, session)
    if status != 200:
        log(f"    !! HTTP {status}")
        return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)

    # Extract digitalData
    dd = None
    m = re.search(r"window\.digitalData\s*=\s*\(\s*(\{.*?\})\s*\)\s*;", html, re.DOTALL)
    if not m:
        m = re.search(r"window\.digitalData\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
    if m:
        try:
            dd = json.loads(m.group(1))
            log(f"    digitalData parsed successfully")
        except json.JSONDecodeError as e:
            log(f"    !! digitalData JSON parse error: {e}")

    if not dd or "product" not in dd or not dd["product"]:
        log(f"    !! No product data in digitalData")
        return None

    main_product = dd["product"][0]
    pi = main_product.get("productInfo", {})
    main_name = pi.get("productName", "")
    main_price_obj = main_product.get("price", {})
    main_price = main_price_obj.get("basePrice")

    log(f"    Product: {main_name}")
    log(f"    Price: GBP {main_price}")

    # EPA from nutritional info table
    description = pi.get("description", "")
    nutri_text = ""
    nutri_div = soup.find("div", id="nutritional.information")
    if nutri_div:
        nutri_text = nutri_div.get_text(" ", strip=True)
        log(f"    Found nutritional table ({len(nutri_text)} chars)")

    dosage_text = f"{main_name} ||| {description} ||| {nutri_text}"
    dosage = extract_epa_dosage(main_name, description, dosage_text, log)

    # Variants from linkedProduct (filter out subscriptions)
    linked = main_product.get("linkedProduct", [])
    log(f"    Linked variants: {len(linked)}")

    variants = []
    for lp in linked:
        lpi = lp.get("productInfo", {})
        lp_price_obj = lp.get("price", {})
        lp_name = lpi.get("productName", "")
        lp_sku = lpi.get("sku", "")
        lp_price = lp_price_obj.get("basePrice")
        lp_attrs = lpi.get("attributes", {})
        lp_size_text = lp_attrs.get("size_quantity_text", "")
        lp_type = lp.get("category", {}).get("productType", "")

        log(f"      Variant: {lp_name:45s}  SKU: {lp_sku:12s}  Price: {lp_price}  Size: {lp_size_text}")

        is_subscription = ("subscription" in lp_size_text.lower() or
                          "subscription" in lp_name.lower() or
                          lp_sku.upper().startswith("SB"))
        if is_subscription:
            log(f"        -> Skipping (subscription)")
            continue
        if lp_type != "simple":
            log(f"        -> Skipping (not simple product)")
            continue

        amount = None
        if lp_size_text:
            m = re.search(r"(\d+)\s*(?:capsules?|softgels?|tablets?)", lp_size_text, re.I)
            if m and 10 <= int(m.group(1)) <= 2000:
                amount = int(m.group(1))
                log(f"        Count from size_text: {amount}")
        if not amount:
            m = re.search(r"\((\d+)\s*capsules?\)", lp_name, re.I)
            if m and 10 <= int(m.group(1)) <= 2000:
                amount = int(m.group(1))

        variants.append({
            "price": lp_price,
            "amount": amount,
            "dosage": dosage,
            "variant_label": f"{lp_name} [{lp_sku}]",
        })

    if variants:
        return variants

    # No linked variants — use main product
    main_size_text = pi.get("attributes", {}).get("size_quantity_text", "")
    amount = None
    if main_size_text:
        m = re.search(r"(\d+)\s*(?:capsules?|softgels?|tablets?)", main_size_text, re.I)
        if m and 10 <= int(m.group(1)) <= 2000:
            amount = int(m.group(1))
    if not amount:
        amount = extract_capsule_count("", main_name, page_text, log)

    return {"price": main_price, "amount": amount, "dosage": dosage}


# ── STRATEGY: VEGETOLOGY (Craft CMS + Sprig v2) ───────────────────────────

def extract_vegetology(product, session, log):
    """Vegetology — Craft CMS with Sprig v2 dynamic variants.

    The variant area (price, capsule count) is loaded via Sprig/HTMX.
    We parse Sprig configs from data-hx-vals attributes, find the variants
    component, and call the Sprig endpoint to get rendered variant HTML.
    EPA/DHA come from the nutritional facts table in the main page HTML.
    """
    url = product["url"]
    log(f"    Fetching Vegetology page...")
    status, html = fetch_page(url, session)
    if status != 200:
        log(f"    !! HTTP {status}")
        return None

    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
    log(f"    Title: {title[:120]}")

    # ── Sprig v2: Fetch variant data (price + capsule count) ───────
    sprig_html = ""

    sprig_url = None
    m = re.search(r'data-hx-get="([^"]*sprig-core[^"]*)"', html)
    if not m:
        m = re.search(r"data-hx-get='([^']*sprig-core[^']*)'", html)
    if m:
        sprig_url = m.group(1)

    variant_config = None
    for m in re.finditer(r'data-hx-vals="([^"]*)"', html):
        raw = m.group(1)
        try:
            decoded = html_mod.unescape(raw)
            outer = json.loads(decoded)
            config_val = outer.get("sprig:config", "")
            if not config_val:
                continue
            hex_match = re.match(r'^([0-9a-f]{64})(.*)', config_val)
            if not hex_match:
                continue
            inner = json.loads(hex_match.group(2))
            template = inner.get("template", "")
            if any(k in template.lower() for k in ("variant", "product-detail")):
                variant_config = outer
                log(f"    Found Sprig variants component: {template}")
                break
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    if sprig_url and variant_config:
        log(f"    Calling Sprig endpoint for variants...")
        try:
            sprig_headers = dict(HEADERS)
            sprig_headers["HX-Request"] = "true"
            sprig_headers["HX-Trigger"] = "load"
            sprig_headers["Accept"] = "text/html"
            sprig_headers["Referer"] = url

            resp = session.get(sprig_url, params=variant_config,
                             headers=sprig_headers, timeout=20)
            if resp.status_code == 200 and len(resp.text) > 100:
                sprig_html = resp.text
                log(f"    Sprig response: {len(sprig_html)} bytes")
        except Exception as e:
            log(f"    Sprig call failed: {e}")

    # ── Price ──────────────────────────────────────────────────────
    price = None

    if sprig_html:
        sprig_text = BeautifulSoup(sprig_html, "lxml").get_text(" ", strip=True)
        for m in re.finditer(r"\xa3(\d+\.\d{2})", sprig_text):
            val = float(m.group(1))
            if 2 < val < 200:
                price = val
                log(f"    Price from Sprig text: GBP {price}")
                break
        if not price:
            for m in re.finditer(r"(\d+\.\d{2})", sprig_html[:5000]):
                val = float(m.group(1))
                if 5 < val < 200:
                    price = val
                    log(f"    Price from Sprig HTML: GBP {price}")
                    break

    if not price:
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string)
                items = [data]
                if isinstance(data, dict) and "@graph" in data:
                    g = data["@graph"]
                    items = g if isinstance(g, list) else [g]
                for item in items:
                    if isinstance(item, dict) and "Product" in str(item.get("@type", "")):
                        offers = item.get("offers", {})
                        p = offers.get("price") if isinstance(offers, dict) else None
                        if p and float(p) > 0:
                            price = float(p)
                            log(f"    Price from JSON-LD: GBP {price}")
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

    if not price:
        for prop in ["product:price:amount", "og:price:amount"]:
            tag = soup.find("meta", property=prop)
            if tag and tag.get("content"):
                try:
                    p = float(tag["content"])
                    if p > 0:
                        price = p
                        log(f"    Price from meta {prop}: GBP {price}")
                except ValueError:
                    pass

    if not price:
        m = re.search(r'"price"\s*:\s*"(\d+\.\d{2})"', html)
        if m and 2 < float(m.group(1)) < 200:
            price = float(m.group(1))
            log(f"    Price from raw HTML key: GBP {price}")

    log(f"    -> Price: GBP {price}")

    # ── Capsule count ──────────────────────────────────────────────
    amount = None

    if sprig_html:
        sprig_full = BeautifulSoup(sprig_html, "lxml").get_text(" ", strip=True)
        m = re.search(r"(\d+)\s+(?:Capsules?|Softgels?|Tablets?)\b", sprig_full, re.I)
        if m and 10 <= int(m.group(1)) <= 2000:
            amount = int(m.group(1))
            log(f"    Count from Sprig text: {amount}")

    if not amount and sprig_html:
        day_supply = None
        m = re.search(r'data-supply="(\d+)"', sprig_html)
        if m:
            day_supply = int(m.group(1))
        if not day_supply:
            m = re.search(r"(\d+)\s+day\s+supply", sprig_html, re.I)
            if m:
                day_supply = int(m.group(1))
        if day_supply:
            how_to_take = soup.find("section", id="ql-how-to-take")
            dose_text = how_to_take.get_text(" ", strip=True) if how_to_take else ""
            m = re.search(r"(?:take\s+)?(\d+)\s+capsules?\s+per\s+day", dose_text, re.I)
            if m:
                caps_per_day = int(m.group(1))
                amount = caps_per_day * day_supply
                log(f"    Count: {caps_per_day} caps/day x {day_supply} days = {amount}")

    if not amount:
        product_detail = soup.find("div", {"data-name": "product-detail"})
        if product_detail:
            m = re.search(r"(\d+)\s+(?:Capsules?|Softgels?|Tablets?)\b",
                          product_detail.get_text(" ", strip=True), re.I)
            if m and 10 <= int(m.group(1)) <= 2000:
                amount = int(m.group(1))
                log(f"    Count from product-detail: {amount}")

    log(f"    -> Amount: {amount}")

    # ── EPA from nutritional facts table ───────────────────────────
    nutri_section = soup.find("section", id="ql-nutritional-facts")
    nutri_text = ""
    if nutri_section:
        tables = nutri_section.find_all("table")
        if tables:
            nutri_text = tables[0].get_text(" ", strip=True)
        else:
            nutri_text = nutri_section.get_text(" ", strip=True)
    else:
        for table in soup.find_all("table"):
            text = table.get_text(" ", strip=True)
            if re.search(r"EPA|eicosapentaenoic", text, re.I):
                nutri_text = text
                break

    if not nutri_text:
        desc_parts = []
        for sid in ["ql-nutritional-facts", "ql-ingredients",
                     "ql-how-to-take", "ql-product-information"]:
            sec = soup.find("section", id=sid)
            if sec:
                desc_parts.append(sec.get_text(" ", strip=True))
        nutri_text = " ||| ".join(desc_parts) if desc_parts else ""

    product_detail = soup.find("div", {"data-name": "product-detail"})
    desc_text = ""
    if product_detail:
        prose = product_detail.find("div", class_=re.compile(r"prose"))
        if prose:
            desc_text = prose.get_text(" ", strip=True)

    combined_nutri = f"{title} ||| {desc_text} ||| {nutri_text}"
    dosage = extract_epa_dosage(title, combined_nutri, combined_nutri, log)

    return {"price": price, "amount": amount, "dosage": dosage}


# ── Cost calculation & formatting ──────────────────────────────────────────

def calc_costs(price, amount, dosage):
    if not all([price, amount, dosage]):
        return None, None, None, None
    theo_caps = TARGET_DOSE / dosage
    theo_cost = (theo_caps / amount) * price
    prac_caps = int(-(-TARGET_DOSE // dosage))
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

    terminal.write(f"\n  VEGAN OMEGA-3 SCRAPER v1\n")
    terminal.write(f"  Scraping {len(PRODUCTS)} products...\n")
    terminal.write(f"  Output -> {OUTPUT_FILE}\n\n")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        sys.stdout = f
        session = requests.Session()

        print(SEP)
        print(f"  VEGAN OMEGA-3 -- SCRAPE RESULTS v1")
        print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Target daily dose: {TARGET_DOSE}mg EPA (Eicosapentaenoic Acid)")
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
            if strategy == "v_shopify":
                scraped = extract_shopify(product, session, log)
            elif strategy == "magento":
                scraped = extract_magento(product, session, log)
            elif strategy == "vegetology":
                scraped = extract_vegetology(product, session, log)
            elif strategy == "v_jsonld":
                scraped = extract_jsonld(product, session, log)

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
                print(f"    EPA (mg/cap):  {fmt(s_dos):>12s}   {fmt(k_dos):>12s}   {match_check(s_dos, k_dos)}")

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
        print(f"#  VEGAN OMEGA-3 -- DAILY COST RANKING (practical, by EPA)")
        print(f"#  Target: {TARGET_DOSE}mg EPA/day")
        print(f"{'#' * 90}\n")

        ranked = sorted([r for r in all_results if r["prac"] is not None], key=lambda r: r["prac"])

        print(f"  {'#':>3s}  {'Brand':<40s}  {'GBP/day':>10s}  {'Caps/d':>6s}  "
              f"{'Price':>8s}  {'Caps':>5s}  {'EPA/cap':>7s}  {'Data':>10s}")
        print(f"  {'---':>3s}  {'---':<40s}  {'---':>10s}  {'---':>6s}  "
              f"{'---':>8s}  {'---':>5s}  {'---':>7s}  {'---':>10s}")

        for rank, r in enumerate(ranked, 1):
            flag = " <-- BEST" if rank == 1 else ""
            src = "LIVE"
            epa_str = f"{r['f_dos']:.1f}" if isinstance(r['f_dos'], float) else str(r['f_dos'])
            print(f"  {rank:>3d}  {r['brand']:<40s}  {r['prac']:>10.6f}  "
                  f"{r['prac_caps']:>6d}  "
                  f"{r['f_pri']:>8.2f}  {str(r['f_amt']):>5s}  "
                  f"{epa_str:>7s}  {src:>10s}{flag}")

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
                issues.append("EPA dosage not scraped")

            k_amt, k_dos, k_pri = KNOWN.get(r["brand"], (None, None, None))
            if r["s_pri"] and k_pri and abs(r["s_pri"] - k_pri) > 0.50:
                issues.append(f"Price changed: was GBP {k_pri}, now GBP {r['s_pri']}")
            if r["s_amt"] and k_amt and r["s_amt"] != k_amt:
                issues.append(f"Amount mismatch: scraped {r['s_amt']}, spreadsheet {k_amt}")
            if r["s_dos"] and k_dos and abs(r["s_dos"] - k_dos) > 5:
                issues.append(f"EPA mismatch: scraped {r['s_dos']:.1f}mg, spreadsheet {k_dos}mg")

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
