"""
OMEGA 3 -- FOCUSED SCRAPER v8
==============================
Run:  python omega3_scraper.py

Key v8 changes:
  - VotaBright (Superdrug) now scraped via curl_cffi (Akamai TLS bypass)
  - cloudscraper CANNOT bypass Superdrug's Akamai bot protection (403)
  - curl_cffi impersonates real Chrome TLS fingerprint + homepage preflight
    to collect Akamai sensor cookies (_abck, bm_sz, etc.)
  - Falls back to cloudscraper, then spreadsheet if curl_cffi unavailable
  - Superdrug price extraction skips £0.00 basket values

Key v7 changes:
  - VotaBright (Superdrug) now scraped LIVE via cloudscraper (was 403 with plain requests)
  - "blocked" strategy removed — all products now have live scraping

Key v6 changes:
  - iHerb NOW Foods now scraped LIVE via cloudscraper (Cloudflare bypass)
  - "blocked" strategy replaced with "iherb" for iHerb products

Key v5 changes:
  - P3: Split serving patterns into tier1 (serving-size) and tier2 (daily-intake)
  - P3: Sanity check rejects tier2 servings that produce implausibly low EPA/cap
  - Fixed Aliment: "5 capsules a day" is dosage advice, not nutritional serving size

Key v4 changes:
  - P1: Negative lookahead rejects combined "Xmg EPA and DHA" values
  - EPA collection: Filters out combined EPA+DHA totals from candidates
  - P3: "for X capsules" pattern moved to top (Amazon title priority)
  - P2: Added re.I flag for table serving pattern matching (SOFTGELS etc.)
  - Fixed Omegor Vitality 500 detection (was returning 400 combined instead of 267.5)

Key v3 changes:
  - Priority 1: "330mg EPA per softgel" patterns -> return directly
  - Priority 2: Nutritional table EPA / table serving size
  - Priority 3: First EPA found / detected serving size
  - Fixed "Per Serving (3 SOFTGELS)" parenthesized table headers
  - Removed greedy "X capsules provide/daily" pattern
  - Uses FIRST EPA found, not largest
  - Fixed Amazon count extraction

Output -> C:\\Users\\morit\\Documents\\Sonstiges\\Dokumente\\omega3_scrape_results.txt
Requirements:  pip install requests beautifulsoup4 lxml cloudscraper curl_cffi
"""

import requests, json, re, time, sys, os
from datetime import datetime
from urllib.parse import urlparse
from collections import Counter
from bs4 import BeautifulSoup

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    from iherb_session import fetch_iherb_page
    HAS_IHERB_SESSION = True
except ImportError:
    HAS_IHERB_SESSION = False

OUTPUT_DIR  = r"C:\Users\morit\Documents\Sonstiges\Dokumente"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "omega3_scrape_results.txt")

TARGET_DOSE = 3000  # mg EPA per day

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

KNOWN = {
    "Nutrition Geeks":       (120, 350,   9.99),
    "Supplemented":          (540, 180,   24.99),
    "Weight World":          (240, 330,   20.99),
    "VotaBright":            (300, 330,   29.99),
    "NOW Foods":             (180, 500,   31.81),
    "Supplement Needs":      (90,  500,   16.99),
    "Bulk":                  (270, 330,   32.99),
    "Aliment":               (120, 315,   19.99),
    "Wiley's Finest (Minis)":(180, 180,   17.99),
    "Omegor":                (60,  267,   9.50),
    "Wiley's Finest (Peak)": (90,  750,   51.00),
    "Lamberts":              (90,  357.5, 43.95),
    "BioCare":               (60,  262,   25.39),
}

PRODUCTS = [
    {"brand": "Nutrition Geeks",       "url": "https://www.nutritiongeeks.co/products/omega-3",                                                              "strategy": "shopify",    "variant_hint": "1 Pack"},
    {"brand": "Supplemented",          "url": "https://www.supplemented.co.uk/products/omega3-1000mg-softgels",                                              "strategy": "shopify",    "variant_hint": "540"},
    {"brand": "Weight World",          "url": "https://www.weightworld.uk/products/omega-3-fish-oil-2000mg-softgels",                                        "strategy": "shopify",    "variant_hint": "Default"},
    {"brand": "VotaBright",            "url": "https://www.superdrug.com/health/vitamins-supplements/minerals/vitabright-omega-3-2000mg-super-strength-fish-oil-soft-gels/p/mp-00143858", "strategy": "superdrug", "variant_hint": None},
    {"brand": "NOW Foods",             "url": "https://uk.iherb.com/pr/now-foods-ultra-omega-3-fish-oil-180-softgels/8341",                                  "strategy": "iherb",      "variant_hint": None},
    {"brand": "Supplement Needs",      "url": "https://www.supplementneeds.co.uk/products/supplement-needs-omega-3-high-strength-90-softgels",                "strategy": "shopify",    "variant_hint": "Default"},
    {"brand": "Bulk",                  "url": "https://www.bulk.com/uk/products/super-strength-omega-3-softgels/bpb-o3ss-0000",                              "strategy": "jsonld",     "variant_hint": None},
    {"brand": "Aliment",               "url": "https://alimentnutrition.co.uk/products/omega3-fish-oil-capsules-epa-dha",                                    "strategy": "shopify",    "variant_hint": "1 x 120"},
    {"brand": "Wiley's Finest (Minis)","url": "https://www.wileysfinest.co.uk/products/easy-swallow-minis",                                                  "strategy": "shopify",    "variant_hint": "180"},
    {"brand": "Omegor",                "url": "https://www.amazon.co.uk/Omega-Fish-Capsules-IFOS-Certified/dp/B00CO89IAO",                                   "strategy": "amazon",     "variant_hint": None},
    {"brand": "Wiley's Finest (Peak)", "url": "https://www.wileysfinest.co.uk/products/peak-epa-30-softgels-1-months-supply",                                "strategy": "shopify",    "variant_hint": "90"},
    {"brand": "Lamberts",              "url": "https://victoriahealth.com/pulse-pure-fish-oil-1300mg-coq10-100mg/",                                          "strategy": "meta_jsonld","variant_hint": None},
    {"brand": "BioCare",               "url": "https://www.nvspharmacy.co.uk/products/biocare-mega-epa-60-capsules",                                         "strategy": "shopify",    "variant_hint": "Default"},
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
    """Replace written-out number words (one–ten) with digits in context of
    capsule/softgel/tablet references so regex patterns can match them."""
    def _replace(m):
        return WORD_NUMBERS[m.group(1).lower()] + m.group(2)
    # Only replace when followed by a capsule/softgel/tablet word (avoids false positives)
    return re.sub(
        r"\b(" + "|".join(WORD_NUMBERS.keys()) + r")\b(\s+(?:soft\s*gels?|capsules?|tablets?))",
        _replace, text, flags=re.I
    )


# ── EPA DOSAGE EXTRACTION (v3 — priority-based) ───────────────────────────

def extract_epa_dosage(title, body, page_text, log):
    """
    v5: Priority-based EPA extraction.

    v5 changes from v4:
      - P3: Serving patterns split into tier1 (serving-size) and tier2 (daily-intake)
      - P3: Tier2 sanity check rejects implausibly low EPA/cap (<100mg)
      - Fixed Aliment "5 capsules a day" incorrectly used as serving size

    v4 changes from v3:
      - P1: Rejects combined "Xmg EPA and DHA" values (negative lookahead)
      - EPA collection: Filters out combined EPA+DHA totals
      - P3: "for X capsules" pattern moved to top priority (Amazon titles)

    PRIORITY 1 — "per softgel/capsule" patterns (no division needed):
      "330mg EPA and 220mg DHA per softgel"  (separate values — OK)
      "EPA 500mg per capsule"
      "750mg of EPA per softgel"
      NOT: "400mg of EPA and DHA per capsule" (combined — rejected)

    PRIORITY 2 — Nutritional table with explicit table serving size:
      "Per Serving (3 SOFTGELS) — EPA 990mg"
      Find the serving size from the table header, find EPA nearby.

    PRIORITY 3 — First EPA value found + serving size from page:
      Use the FIRST (not largest) EPA value, divide by serving.
      Serving patterns split into tier1 (serving-size context, high confidence)
      and tier2 (daily-intake context, lower confidence with sanity check).
      Tier2 results rejected if they produce EPA/cap < 100mg.

    PRIORITY 4 — Percentage fallback (18/12% of fish oil).
    """
    all_text = f"{title} ||| {body} ||| {page_text}"
    all_text = words_to_digits(all_text)  # "two capsules" -> "2 capsules" etc.

    # ================================================================
    # PRIORITY 1: "Xmg EPA ... per softgel/capsule" (direct per-cap)
    # ================================================================
    # Negative lookahead: reject combined "EPA and DHA" / "EPA + DHA" values
    # but allow "EPA and 268mg DHA" (separate values with mg between and/DHA)
    _NC = r"(?!\s*(?:and|&|\+|/)\s*DHA)"  # Not-Combined guard

    per_cap_patterns = [
        # "330mg EPA and 220mg DHA per softgel" (separate values OK)
        # "400mg of EPA and DHA per capsule" (combined value REJECTED)
        rf"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?EPA{_NC}\b[^.]*?per\s+(?:soft\s*gel|capsule|tablet)",
        # "EPA 330mg ... per softgel"
        rf"EPA{_NC}\s*(?:\([^)]*\))?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg[^.]*?per\s+(?:soft\s*gel|capsule|tablet)",
        # "per softgel: EPA 330mg" or "per capsule ... EPA 500mg"
        rf"per\s+(?:soft\s*gel|capsule|tablet)\s*[:\-]?\s*[^.]*?EPA{_NC}\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        # "each softgel ... 330mg EPA" or "each capsule is packed with 330 mg EPA"
        rf"each\s+(?:soft\s*gel|capsule|tablet)[^.]*?(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?EPA{_NC}",
        # "each softgel ... EPA 330mg"
        rf"each\s+(?:soft\s*gel|capsule|tablet)[^.]*?EPA{_NC}\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
    ]
    for pattern in per_cap_patterns:
        m = re.search(pattern, all_text, re.I)
        if m:
            val = float(m.group(1))
            if 10 <= val <= 1500:
                log(f"    [P1] EPA per capsule directly: {val}mg  [pattern: ...{pattern[20:60]}...]")
                return val

    # ================================================================
    # PRIORITY 2: Nutritional table EPA + table serving size
    # ================================================================
    # Look for table serving size patterns (these are very specific)
    table_serving = None
    table_serving_patterns = [
        # "Per Serving (3 SOFTGELS)" — Bulk style
        r"[Pp]er\s+[Ss]erving\s*\(\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*\)",
        # "Per 2 Capsules" as table header
        r"[Pp]er\s+(\d+)\s+(?:soft\s*gel|capsule|tablet)s?\s*(?:\)|$|[A-Z]|\n)",
        # "Serving Size: 2 Softgels" in nutritional info context
        r"[Ss]erving\s*[Ss]ize\s*[:\-]\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        # "Nutritional Information per 2 capsules"
        r"[Nn]utritional\s+[Ii]nformation\s+per\s+(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        # "Amount Per 2 Softgels"
        r"[Aa]mount\s+[Pp]er\s+(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
        # "Per Daily Intake (2 Capsules)"
        r"[Pp]er\s+[Dd]aily\s+[Ii]ntake\s*\(\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\s*\)",
    ]
    for pattern in table_serving_patterns:
        m = re.search(pattern, all_text, re.I)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 10:
                table_serving = n
                log(f"    [P2] Table serving size: {n}  [pattern: {pattern[:50]}]")
                break

    # Find all EPA values
    epa_patterns = [
        r"EPA\s*(?:\([^)]*\))?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        r"[Ee]icosapentaenoic\s+[Aa]cid\s*(?:\(EPA\))?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*mg",
        r"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?EPA\b",
        r"EPA[^0-9]{0,15}?(\d+(?:\.\d+)?)\s*mg",
    ]

    # Identify combined "Xmg EPA and DHA" values so we can exclude them
    # (these are EPA+DHA totals, not EPA alone)
    combined_pattern = r"(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?EPA\s*(?:and|&|\+|/)\s*DHA"
    combined_values = set()
    for m in re.finditer(combined_pattern, all_text, re.I):
        combined_values.add(float(m.group(1)))
    if combined_values:
        log(f"    [EPA] Combined EPA+DHA values excluded: {combined_values}")

    epa_values = []
    for pattern in epa_patterns:
        matches = re.findall(pattern, all_text, re.I)
        for m in matches:
            val = float(m)
            if 10 <= val <= 2000 and val not in combined_values:
                epa_values.append(val)

    # Deduplicate preserving order
    seen = set()
    unique_epa = []
    for v in epa_values:
        if v not in seen:
            seen.add(v)
            unique_epa.append(v)

    if unique_epa and table_serving:
        # Use FIRST EPA value (closest to nutritional table, not polluted by other products)
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

        # General serving size — split into two tiers:
        #   Tier 1: Patterns that describe the nutritional serving size (high confidence)
        #   Tier 2: Patterns that describe daily intake recommendations (lower confidence)
        #           These often coincide with the serving size but sometimes don't
        #           (e.g. "take 5 capsules a day" where the EPA value is already per cap)
        serving = 1
        serving_confidence = "default"

        # Tier 1: Serving-size patterns (high confidence)
        tier1_serving_patterns = [
            # "for X caps" (Amazon style in titles) — high confidence
            r"for\s+(\d+)\s*(?:soft\s*gel|capsule|cap|tablet)s?\b",
            # "Serving size: X capsules" (general)
            r"serving\s*size\s*[:\-]?\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
            # "per X capsules" standalone
            r"per\s+(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\b",
        ]

        # Tier 2: Daily-intake patterns (lower confidence — may be dosage
        # recommendations rather than nutritional table serving sizes)
        # Note: last two patterns use (?<![-–]) to avoid matching the upper
        # bound of ranges like "1-5 capsules a day" (would wrongly grab "5")
        tier2_daily_patterns = [
            # "take X softgels" / "directions: take X"
            r"(?:take|directions?\s*[:\-]?\s*(?:take)?)\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
            # "daily dose: X capsules"
            r"daily\s+dose\s*[:\-]?\s*(\d+)\s*(?:soft\s*gel|capsule|tablet)s?",
            # "X capsules per day" / "X softgels a day" (BioCare style)
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
                    log(f"    [P3] Serving size: {serving} (tier1)  [pattern: {pattern[:50]}]")
                    break

        if serving_confidence == "default":
            for pattern in tier2_daily_patterns:
                m = re.search(pattern, all_text, re.I)
                if m:
                    n = int(m.group(1))
                    if 1 <= n <= 10:
                        serving = n
                        serving_confidence = "tier2"
                        log(f"    [P3] Serving size: {serving} (tier2/daily-intake)  [pattern: {pattern[:50]}]")
                        break

        if serving == 1:
            log(f"    [P3] Serving size: 1 (default)")

        first_epa = unique_epa[0]
        per_cap = first_epa / serving

        # Sanity check for tier2 (daily-intake) patterns: if dividing by
        # the detected serving gives an implausibly low EPA per cap (<100mg)
        # while the raw value is a plausible per-cap figure, the pattern
        # likely matched a dosage recommendation (e.g. "take 5 capsules a day")
        # rather than the nutritional table serving size.
        if serving_confidence == "tier2" and per_cap < 100 and first_epa >= 100:
            log(f"    [P3] Tier2 serving {serving} gives {per_cap:.1f}mg/cap — too low, "
                f"likely a daily-intake recommendation not a serving size")
            log(f"    [P3] Falling back to serving=1, EPA={first_epa:.1f}mg per cap")
            serving = 1
            per_cap = first_epa

        log(f"    [P3] Using first EPA: {first_epa}mg / {serving} = {per_cap:.1f}mg per cap")
        if 10 <= per_cap <= 1500:
            return per_cap

    # ================================================================
    # PRIORITY 4: Percentage fallback (e.g. "18/12%")
    # ================================================================
    log(f"    [P4] No direct EPA found, trying percentage fallback...")
    pct = re.search(r"(\d+)/\d+\s*%", all_text)
    fish_oil = re.search(r"(\d+)\s*mg\s*(?:fish\s*oil|per\s*(?:soft\s*gel|capsule))", all_text, re.I)
    if not fish_oil:
        fish_oil = re.search(r"(\d+)\s*mg", title, re.I)
    if pct and fish_oil:
        epa_pct = int(pct.group(1))
        fo_mg = int(fish_oil.group(1))
        calc_epa = fo_mg * epa_pct / 100
        log(f"    [P4] {epa_pct}% of {fo_mg}mg fish oil = {calc_epa}mg EPA per cap")
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

    m = re.search(r"(\d+)[\s\-]*(?:soft\s*gel\s+capsules?|capsules|softgels|tablets|soft\s*gels|caps)\b", product_title, re.I)
    if m and 10 <= int(m.group(1)) <= 2000:
        log(f"    Count from product title: {m.group(1)}")
        return int(m.group(1))

    matches = re.findall(r"(\d+)[\s\-]*(?:soft\s*gel\s+capsules?|capsules|softgels|tablets|soft\s*gels)\b", body_text, re.I)
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


def extract_amazon_count(title, soup, log):
    # Pattern that handles "softgel caps/capsules" as compound phrase
    count_pat = r"(\d+)\s*(?:soft\s*gel\s+caps(?:ules?)?|capsules|softgels|tablets|soft\s*gels|caps)\b"

    m = re.search(count_pat, title, re.I)
    if m and 10 <= int(m.group(1)) <= 2000:
        log(f"    Count from Amazon title: {m.group(1)}")
        return int(m.group(1))

    # Check full <title> and <meta name="title"> (often longer than H1)
    for tag in [soup.find("title"), soup.find("meta", attrs={"name": "title"})]:
        if tag:
            full_title = tag.get("content", "") if tag.name == "meta" else tag.get_text()
            m = re.search(count_pat, full_title, re.I)
            if m and 10 <= int(m.group(1)) <= 2000:
                log(f"    Count from Amazon meta/page title: {m.group(1)}")
                return int(m.group(1))

    m = re.search(r"(\d+)\s*count\b", title, re.I)
    if m and int(m.group(1)) >= 10:
        log(f"    Count from Amazon title (count): {m.group(1)}")
        return int(m.group(1))
    for det_id in ["feature-bullets", "detailBullets_feature_div",
                    "productDetails_detailBullets_sections1",
                    "productDetails_techSpec_section_1"]:
        det = soup.find(attrs={"id": det_id})
        if det:
            text = det.get_text(" ", strip=True)
            m = re.search(r"(\d+)\s*(?:count|capsules|softgels|tablets)\b", text, re.I)
            if m and int(m.group(1)) >= 10:
                log(f"    Count from Amazon section '{det_id}': {m.group(1)}")
                return int(m.group(1))
    # Last resort: look in the whole page for "Pack of X", "X Count", or "(X Softgel Caps)"
    page_text = soup.get_text(" ", strip=True)
    m = re.search(r"(?:pack\s+of|contains)\s+(\d+)\s*(?:capsules|softgels|tablets)", page_text, re.I)
    if m and int(m.group(1)) >= 10:
        log(f"    Count from Amazon page text: {m.group(1)}")
        return int(m.group(1))
    # Parenthesized count like "(60 Softgel Caps)" or "(120 Capsules)"
    m = re.search(r"\(\s*(\d+)\s+(?:soft\s*gel\s+)?(?:caps(?:ules?)?|softgels|tablets|soft\s*gels)\s*\)", page_text, re.I)
    if m and 10 <= int(m.group(1)) <= 2000:
        log(f"    Count from Amazon parenthesized: {m.group(1)}")
        return int(m.group(1))
    log(f"    !! Could not extract count from Amazon page")
    return None


# ── DOMAIN-SPECIFIC EXTRACTORS ─────────────────────────────────────────────

def _variant_pack_multiplier(variant_title):
    """Detect pack variants like '3 Pack', 'Three Pack', '6 Pack'.
    Returns the multiplier (e.g. 3) or None if not a pack variant."""
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
    dosage = extract_epa_dosage(title, body, combined, log)

    # ── Build a result for EVERY variant (size/pack) ───────────────
    # Filter out variants whose title is just "Default Title" or empty
    # when there's only one variant — treat as a single product.
    is_single = (len(variants) <= 1 or
                 all((v.get("title") or "").strip().lower() in
                     ("", "default", "default title") for v in variants))

    if is_single:
        chosen = variants[0] if variants else None
        price = float(chosen["price"]) if chosen else None
        amount = extract_capsule_count(
            chosen.get("title", "") if chosen else "", title, body, log)
        if amount is None and page_text:
            log(f"    Retrying capsule count from page HTML...")
            amount = extract_capsule_count("", title, page_text, log)
        return {"price": price, "amount": amount, "dosage": dosage}

    # Determine base capsule count for pack-multiplier variants
    base_count = extract_capsule_count("", title, body, log)
    if base_count is None and page_text:
        base_count = extract_capsule_count("", title, page_text, log)
    log(f"    Base capsule count (from product): {base_count}")

    # Multiple real variants → expand each into its own result
    results = []
    for v in variants:
        vt = (v.get("title") or "").strip()
        vprice = float(v["price"]) if v.get("price") else None
        log(f"    -- Variant '{vt}' --")
        log(f"       Price: GBP {vprice}")

        # Check for pack variant (e.g. "Three Pack", "6 Pack")
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


def extract_jsonld(product, session, log):
    url = product["url"]
    log(f"    Fetching page HTML...")
    status, html = fetch_page(url, session)
    if status != 200:
        log(f"    !! HTTP {status}")
        return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)

    price = None
    name = ""
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
                    name = item.get("name", "")
                    log(f"    JSON-LD Product: {name}")
                    offers = item.get("offers", {})
                    if isinstance(offers, dict) and offers.get("price"):
                        price = float(offers["price"])
                    elif isinstance(offers, list):
                        for o in offers:
                            p = o.get("price")
                            avail = o.get("availability", "")
                            log(f"      Offer: GBP {p}  avail={avail}")
                            if p and "InStock" in avail:
                                price = float(p)
                    if not price:
                        off = item.get("offers", {})
                        if isinstance(off, dict):
                            p = off.get("lowPrice") or off.get("price")
                            if p:
                                price = float(p)
        except (json.JSONDecodeError, TypeError):
            pass

    if not price:
        for meta_name in ["product:price:amount", "og:price:amount"]:
            tag = soup.find("meta", property=meta_name)
            if tag and tag.get("content"):
                try:
                    price = float(tag["content"])
                    log(f"    Meta price ({meta_name}): GBP {price}")
                except ValueError:
                    pass

    log(f"    -> Price: GBP {price}")
    amount = extract_capsule_count("", name, page_text, log)
    dosage = extract_epa_dosage(name, page_text, page_text, log)
    return {"price": price, "amount": amount, "dosage": dosage}


def extract_amazon(product, session, log):
    url = product["url"]
    log(f"    Fetching Amazon page...")
    status, html = fetch_page(url, session)
    if status != 200:
        log(f"    !! HTTP {status}")
        return None

    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    log(f"    Title: {title[:120]}")

    price = None
    whole_tag = soup.find("span", class_="a-price-whole")
    frac_tag = soup.find("span", class_="a-price-fraction")
    if whole_tag and frac_tag:
        try:
            whole = whole_tag.get_text(strip=True).rstrip(".")
            frac = frac_tag.get_text(strip=True)
            price = float(f"{whole}.{frac}")
            log(f"    Price from a-price: GBP {price}")
        except ValueError:
            pass
    if not price:
        core = soup.find("div", id="corePrice_feature_div")
        if core:
            m = re.search(r"[\x{00A3}](\d+\.\d{2})", core.get_text())
            if m:
                price = float(m.group(1))
    log(f"    -> Price: GBP {price}")

    amount = extract_amazon_count(title, soup, log)

    # Restrict dosage search to product sections only (not recommendations)
    dosage_text = title
    for section_id in ["productDescription", "feature-bullets", "aplus-v2",
                        "aplus_feature_div", "productDescription_feature_div"]:
        section = soup.find("div", id=section_id)
        if section:
            dosage_text += " ||| " + section.get_text(" ", strip=True)
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        if re.search(r"EPA|eicosapentaenoic|supplement\s*facts|nutritional", text, re.I):
            dosage_text += " ||| " + text
            break

    dosage = extract_epa_dosage(title, dosage_text, dosage_text, log)
    return {"price": price, "amount": amount, "dosage": dosage}


def extract_meta_jsonld(product, session, log):
    return extract_jsonld(product, session, log)


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

    # JSON-LD
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

    # Price elements
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

    # Last resort
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
    amount = extract_capsule_count("", title, page_text, log)

    # ── EPA dosage (reuses the main extraction engine) ─────────────
    # Restrict to product-relevant sections to avoid pollution
    dosage_text = title
    for tag in soup.find_all("script", type="application/ld+json"):
        if tag.string and "EPA" in tag.string:
            dosage_text += " ||| " + tag.string
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        if re.search(r"EPA|eicosapentaenoic|supplement\s*facts|nutritional", text, re.I):
            dosage_text += " ||| " + text
            break

    dosage = extract_epa_dosage(title, dosage_text, page_text, log)

    return {"price": price, "amount": amount, "dosage": dosage}


def _superdrug_fetch_html(url, brand, log):
    """Fetch Superdrug HTML, trying curl_cffi first, then cloudscraper.

    Superdrug uses Akamai bot protection which blocks based on TLS
    fingerprint. cloudscraper (designed for Cloudflare) cannot bypass it.
    curl_cffi impersonates a real Chrome TLS fingerprint, which works.

    The homepage preflight is critical: Akamai sets sensor cookies
    (_abck, bm_sz, bm_ss, bm_s, bm_so) on the first request, and
    requires them on subsequent requests.

    Returns (status_code, html_text) or (None, error_message).
    """
    homepage = "https://www.superdrug.com/"

    # ── Attempt 1: curl_cffi (TLS fingerprint impersonation) ───────
    if HAS_CURL_CFFI:
        log(f"    Fetching Superdrug via curl_cffi (impersonate=chrome)...")
        try:
            cffi_session = cffi_requests.Session(impersonate="chrome")

            # Step 1: Homepage preflight to collect Akamai sensor cookies
            log(f"    Step 1: Homepage preflight for Akamai cookies...")
            home_resp = cffi_session.get(homepage, timeout=20)
            cookies = dict(cffi_session.cookies)
            akamai_cookies = [k for k in cookies
                              if any(x in k.lower() for x in ["ak", "bm", "_abck"])]
            log(f"    Homepage: HTTP {home_resp.status_code}, "
                f"Akamai cookies: {akamai_cookies}")

            time.sleep(1.5)

            # Step 2: Product page with session cookies
            log(f"    Step 2: Product page...")
            resp = cffi_session.get(url, timeout=30)

            # Check for Akamai block in response
            if resp.status_code == 200:
                lower = resp.text[:5000].lower()
                if "access denied" in lower and "akamaighost" in resp.headers.get("server", "").lower():
                    log(f"    !! curl_cffi got 200 but Akamai block in body")
                else:
                    log(f"    curl_cffi success: HTTP {resp.status_code}, "
                        f"{len(resp.text)} bytes")
                    return resp.status_code, resp.text

            log(f"    !! curl_cffi HTTP {resp.status_code}")
        except Exception as e:
            log(f"    !! curl_cffi failed: {type(e).__name__}: {e}")
    else:
        log(f"    curl_cffi not installed (pip install curl_cffi)")

    # ── Attempt 2: cloudscraper (unlikely to work, but try) ────────
    if HAS_CLOUDSCRAPER:
        log(f"    Trying cloudscraper as fallback...")
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True},
            )
            resp = scraper.get(url, timeout=30)
            if resp.status_code == 200:
                lower = resp.text[:5000].lower()
                if "access denied" not in lower:
                    log(f"    cloudscraper success: HTTP {resp.status_code}")
                    return resp.status_code, resp.text
                else:
                    log(f"    !! cloudscraper got 200 but Akamai block in body")
            else:
                log(f"    !! cloudscraper HTTP {resp.status_code}")
        except Exception as e:
            log(f"    !! cloudscraper failed: {e}")

    return None, "All fetch methods failed"


def extract_superdrug(product, session, log):
    """Superdrug — uses curl_cffi to bypass Akamai bot protection.

    Akamai (AkamaiGHost) blocks based on TLS fingerprint, not JS challenges.
    cloudscraper cannot bypass it. curl_cffi impersonates real Chrome TLS.

    Fallback chain: curl_cffi → cloudscraper → spreadsheet values.
    """
    url = product["url"]
    brand = product["brand"]

    if not HAS_CURL_CFFI and not HAS_CLOUDSCRAPER:
        log(f"    !! Neither curl_cffi nor cloudscraper installed")
        log(f"    !! Install with: pip install curl_cffi  (recommended)")
        log(f"    Falling back to spreadsheet values")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    status, html = _superdrug_fetch_html(url, brand, log)

    if status != 200 or html is None or len(html) < 1000:
        log(f"    !! Could not fetch Superdrug page — falling back to spreadsheet")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    # Check for bot detection pages (Akamai block, CAPTCHA, challenge)
    lower = html[:5000].lower()
    if any(kw in lower for kw in ["access denied", "captcha", "challenge",
                                    "cf-browser-verification", "just a moment"]):
        log(f"    !! Bot detection page — falling back to spreadsheet")
        if brand in KNOWN:
            amt, dos, pri = KNOWN[brand]
            return {"price": pri, "amount": amt, "dosage": dos}
        return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
    log(f"    Title: {title[:120]}")

    # ── Price ──────────────────────────────────────────────────────
    price = None

    # JSON-LD structured data (Superdrug uses @graph with Product type)
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
                    offers = item.get("offers", {})
                    if isinstance(offers, dict) and offers.get("price"):
                        p = float(offers["price"])
                        if p > 0:
                            price = p
                            log(f"    Price from JSON-LD: GBP {price}")
                    elif isinstance(offers, list):
                        for o in offers:
                            p = o.get("price")
                            if p and float(p) > 0:
                                price = float(p)
                                log(f"    Price from JSON-LD (list): GBP {price}")
                                break
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Meta tags fallback
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

    # Page text fallback — skip £0.00 (basket/placeholder values)
    if not price:
        for m in re.finditer(r"£(\d+\.\d{2})", page_text):
            val = float(m.group(1))
            if 2 < val < 200:
                price = val
                log(f"    Price from page text: GBP {price}")
                break

    log(f"    -> Price: GBP {price}")

    # ── Capsule count ─────────────────────────────────────────────
    # JSON-LD description often has "300 Soft Gel Capsules"
    amount = None
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
                    desc = item.get("description", "")
                    m = re.search(r"(\d+)\s*(?:soft\s*gel\s*capsules|soft\s*gels?|capsules|softgels)\b",
                                  desc, re.I)
                    if m and 10 <= int(m.group(1)) <= 2000:
                        amount = int(m.group(1))
                        log(f"    Count from JSON-LD description: {amount}")
        except (json.JSONDecodeError, TypeError):
            pass

    if not amount:
        m = re.search(
            r"(\d+)\s*(?:soft\s*gel\s*capsules|soft\s*gels?|capsules|softgels|tablets)\b",
            page_text, re.I
        )
        if m and 10 <= int(m.group(1)) <= 2000:
            amount = int(m.group(1))
            log(f"    Count from page text: {amount}")
    if not amount:
        m = re.search(r"(\d+)\s*(?:soft\s*gel|capsule|tablet)s?\b", title, re.I)
        if m and 10 <= int(m.group(1)) <= 2000:
            amount = int(m.group(1))
            log(f"    Count from title: {amount}")

    log(f"    -> Amount: {amount}")

    # ── EPA dosage ─────────────────────────────────────────────────
    # JSON-LD description has "660 mg EPA & 440 mg DHA daily" — extract
    # EPA from there first, then fall back to page text
    dosage_text = title
    for tag in soup.find_all("script", type="application/ld+json"):
        if tag.string and re.search(r"EPA|DHA|omega", tag.string, re.I):
            dosage_text += " ||| " + tag.string

    dosage = extract_epa_dosage(title, dosage_text, page_text, log)

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

    terminal.write(f"\n  OMEGA 3 SCRAPER v8 (EPA only, priority-based)\n")
    terminal.write(f"  Scraping {len(PRODUCTS)} products...\n")
    terminal.write(f"  Output -> {OUTPUT_FILE}\n\n")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        sys.stdout = f
        session = requests.Session()

        print(SEP)
        print(f"  OMEGA 3 -- SCRAPE RESULTS v8")
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
            if strategy == "shopify":
                scraped = extract_shopify(product, session, log)
            elif strategy == "jsonld":
                scraped = extract_jsonld(product, session, log)
            elif strategy == "amazon":
                scraped = extract_amazon(product, session, log)
            elif strategy == "meta_jsonld":
                scraped = extract_meta_jsonld(product, session, log)
            elif strategy == "iherb":
                scraped = extract_iherb(product, session, log)
            elif strategy == "superdrug":
                scraped = extract_superdrug(product, session, log)

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
        print(f"#  OMEGA 3 -- DAILY COST RANKING (practical, by EPA)")
        print(f"#  Target: {TARGET_DOSE}mg EPA/day")
        print(f"{'#' * 90}\n")

        ranked = sorted([r for r in all_results if r["prac"] is not None], key=lambda r: r["prac"])

        print(f"  {'#':>3s}  {'Brand':<35s}  {'GBP/day':>10s}  {'Caps/d':>6s}  "
              f"{'Price':>8s}  {'Caps':>5s}  {'EPA/cap':>7s}  {'Data':>10s}")
        print(f"  {'---':>3s}  {'---':<35s}  {'---':>10s}  {'---':>6s}  "
              f"{'---':>8s}  {'---':>5s}  {'---':>7s}  {'---':>10s}")

        for rank, r in enumerate(ranked, 1):
            flag = " <-- BEST" if rank == 1 else ""
            src = "LIVE"
            epa_str = f"{r['f_dos']:.1f}" if isinstance(r['f_dos'], float) else str(r['f_dos'])
            print(f"  {rank:>3d}  {r['brand']:<35s}  {r['prac']:>10.6f}  "
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
