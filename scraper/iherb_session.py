"""
iHerB SESSION MANAGER — shared across all scrapers
====================================================
Provides a persistent session for scraping iHerb products.

Key techniques (borrowed from the Superdrug Akamai bypass):
  1. curl_cffi with Chrome TLS fingerprint impersonation
  2. One-time homepage preflight to collect Cloudflare cookies
     (__cf_bm, cf_clearance, etc.)
  3. Persistent session — cookies carry across all product page requests
  4. Retry with backoff on 403/503
  5. Realistic headers (Referer, Accept, sec-ch-ua)
  6. Falls back to cloudscraper if curl_cffi unavailable

Usage from any scraper:
    from iherb_session import fetch_iherb_page
    status, html = fetch_iherb_page(url, log)
"""

import time, re

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False


# ── Module-level persistent session ────────────────────────────────────────

_session = None
_session_type = None  # "curl_cffi" or "cloudscraper"
_initialized = False
_last_request_time = 0  # timestamp of last request
COURTESY_DELAY = 2.0    # seconds between requests to avoid rate limits

IHERB_HOME = "https://uk.iherb.com/"

REALISTIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24", "Google Chrome";v="125"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Different impersonation profiles to try on retry
IMPERSONATE_PROFILES = ["chrome", "chrome110", "chrome120"]


def _init_curl_cffi_session(log):
    """Create a curl_cffi session and do a homepage preflight."""
    global _session, _session_type, _initialized

    for profile in IMPERSONATE_PROFILES:
        try:
            log(f"    [iHerb] Initialising curl_cffi session (profile={profile})...")
            sess = cffi_requests.Session(impersonate=profile)

            # Homepage preflight — collect Cloudflare cookies
            log(f"    [iHerb] Homepage preflight: {IHERB_HOME}")
            home_resp = sess.get(IHERB_HOME, timeout=25)

            cookies = dict(sess.cookies)
            cf_cookies = [k for k in cookies if any(
                x in k.lower() for x in ["cf_", "__cf", "bm_", "_abck"]
            )]
            log(f"    [iHerb] Homepage HTTP {home_resp.status_code}, "
                f"cookies: {cf_cookies or list(cookies.keys())[:5]}")

            # Check we didn't get a challenge page
            if home_resp.status_code == 200:
                lower = home_resp.text[:3000].lower()
                if "just a moment" not in lower and "_cf_chl" not in lower:
                    _session = sess
                    _session_type = "curl_cffi"
                    _initialized = True
                    log(f"    [iHerb] Session ready (curl_cffi/{profile})")
                    return True
                else:
                    log(f"    [iHerb] Homepage returned challenge page with profile={profile}")
            else:
                log(f"    [iHerb] Homepage HTTP {home_resp.status_code} with profile={profile}")

        except Exception as e:
            log(f"    [iHerb] curl_cffi init failed ({profile}): {type(e).__name__}: {e}")

    return False


def _init_cloudscraper_session(log):
    """Fallback: create a cloudscraper session with homepage preflight."""
    global _session, _session_type, _initialized

    try:
        log(f"    [iHerb] Falling back to cloudscraper session...")
        sess = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True},
        )

        # Homepage preflight
        log(f"    [iHerb] Homepage preflight via cloudscraper...")
        home_resp = sess.get(IHERB_HOME, timeout=25)

        if home_resp.status_code == 200:
            lower = home_resp.text[:3000].lower()
            if "just a moment" not in lower:
                _session = sess
                _session_type = "cloudscraper"
                _initialized = True
                log(f"    [iHerb] Session ready (cloudscraper)")
                return True
            else:
                log(f"    [iHerb] cloudscraper homepage got challenge page")
        else:
            log(f"    [iHerb] cloudscraper homepage HTTP {home_resp.status_code}")

    except Exception as e:
        log(f"    [iHerb] cloudscraper init failed: {e}")

    return False


def _ensure_session(log):
    """Lazily initialise the shared session on first use."""
    global _initialized
    if _initialized:
        return _session is not None

    # Try curl_cffi first (better TLS fingerprint), then cloudscraper
    if HAS_CURL_CFFI and _init_curl_cffi_session(log):
        return True

    if HAS_CLOUDSCRAPER and _init_cloudscraper_session(log):
        return True

    log(f"    [iHerb] !! All session init methods failed")
    _initialized = True  # Don't retry init on every call
    return False


def _is_blocked(status, html):
    """Check if the response is a Cloudflare block/challenge."""
    if status in (403, 503):
        return True
    if html and len(html) < 20000:
        lower = html[:5000].lower()
        if any(kw in lower for kw in [
            "captcha", "challenge", "cf-browser-verification",
            "_cf_chl", "just a moment", "checking your browser",
            "access denied", "ray id"
        ]):
            return True
    return False


def fetch_iherb_page(url, log, max_retries=3):
    """
    Fetch an iHerb product page using the shared persistent session.

    Returns (status_code, html_text) or (None, error_message).

    Retry strategy:
      - On 403/503 or challenge page: wait and retry up to max_retries times
      - Delays: 3s, 6s, 10s (increasing backoff)
      - Between successful calls: 1.5s courtesy delay
    """
    if not _ensure_session(log):
        return None, "No iHerb session available"

    global _last_request_time

    # Courtesy delay between requests to avoid rate limiting
    elapsed = time.time() - _last_request_time
    if _last_request_time > 0 and elapsed < COURTESY_DELAY:
        wait = COURTESY_DELAY - elapsed
        time.sleep(wait)

    delays = [3, 6, 10]
    headers_for_product = {
        **REALISTIC_HEADERS,
        "Referer": IHERB_HOME,
        "Sec-Fetch-Site": "same-origin",
    }

    for attempt in range(max_retries + 1):
        try:
            if _session_type == "curl_cffi":
                resp = _session.get(url, timeout=30, headers=headers_for_product)
            else:
                resp = _session.get(url, timeout=30, headers=headers_for_product)

            status = resp.status_code
            html = resp.text

            if not _is_blocked(status, html):
                _last_request_time = time.time()
                log(f"    [iHerb] OK: HTTP {status}, {len(html)} bytes"
                    + (f" (attempt {attempt+1})" if attempt > 0 else ""))
                return status, html

            # Blocked — retry with backoff
            if attempt < max_retries:
                delay = delays[min(attempt, len(delays) - 1)]
                log(f"    [iHerb] Blocked (HTTP {status}), "
                    f"retrying in {delay}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(delay)
            else:
                log(f"    [iHerb] !! Blocked after {max_retries+1} attempts (HTTP {status})")
                return status, html

        except Exception as e:
            if attempt < max_retries:
                delay = delays[min(attempt, len(delays) - 1)]
                log(f"    [iHerb] Request error: {type(e).__name__}: {e}, "
                    f"retrying in {delay}s...")
                time.sleep(delay)
            else:
                log(f"    [iHerb] !! Failed after {max_retries+1} attempts: {e}")
                return None, str(e)

    return None, "Max retries exceeded"


def reset_session():
    """Reset the session (e.g. between scraper modules if needed)."""
    global _session, _session_type, _initialized
    _session = None
    _session_type = None
    _initialized = False
