"""
iHerB SESSION MANAGER v2 — Playwright + curl_cffi hybrid
==========================================================
Shared across all scrapers.

STRATEGY:
  1. Launch Playwright (headless Chromium) → navigate to iHerb homepage
  2. Wait for Cloudflare JS challenge to resolve (real browser = always passes)
  3. Extract cf_clearance + all cookies from the browser context
  4. Transfer cookies to a curl_cffi session (same User-Agent, same IP)
  5. Use the fast curl_cffi session for all product page requests

  Fallback chain:
    Playwright+curl_cffi → Playwright+requests → curl_cffi alone → cloudscraper

WHY THIS WORKS:
  - cf_clearance cookie is tied to IP + User-Agent
  - Playwright and curl_cffi run on the same GitHub Actions runner (same IP)
  - We use the same User-Agent string in both
  - Once we have cf_clearance, Cloudflare trusts subsequent requests

REQUIREMENTS:
  pip install playwright curl_cffi
  playwright install chromium --with-deps

Usage:
  from iherb_session import fetch_iherb_page
  status, html = fetch_iherb_page(url, log)
"""

import time, re, os

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

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

import requests as plain_requests


# ── Config ─────────────────────────────────────────────────────────────────

IHERB_HOME = "https://uk.iherb.com/"

# Must be identical in Playwright and the HTTP session
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": IHERB_HOME,
}

# ── Module-level state ─────────────────────────────────────────────────────

_session = None           # HTTP session with clearance cookies
_session_type = None      # "playwright_cffi", "playwright_requests", "curl_cffi", "cloudscraper"
_initialized = False
_last_request_time = 0
COURTESY_DELAY = 2.0      # seconds between requests


# ── Playwright cookie acquisition ──────────────────────────────────────────

def _acquire_cookies_playwright(log):
    """
    Use a real headless browser to clear Cloudflare's JS challenge.
    Returns a list of cookie dicts or None on failure.
    """
    if not HAS_PLAYWRIGHT:
        log(f"    [iHerb] Playwright not available")
        return None

    log(f"    [iHerb] Launching Playwright (headless Chromium)...")
    cookies = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-GB",
                timezone_id="Europe/London",
            )
            page = context.new_page()

            log(f"    [iHerb] Navigating to {IHERB_HOME}...")
            page.goto(IHERB_HOME, wait_until="domcontentloaded", timeout=30000)

            # Wait for Cloudflare challenge to resolve
            # cf_clearance cookie appears once the challenge is passed
            log(f"    [iHerb] Waiting for Cloudflare challenge to clear...")
            for i in range(20):  # up to 20 seconds
                time.sleep(1)
                current_cookies = context.cookies()
                cookie_names = [c["name"] for c in current_cookies]

                if "cf_clearance" in cookie_names:
                    log(f"    [iHerb] cf_clearance obtained after {i+1}s")
                    cookies = current_cookies
                    break

                # Check if page has loaded (no challenge)
                title = page.title()
                if i > 3 and title and "just a moment" not in title.lower():
                    log(f"    [iHerb] Page loaded (title: '{title[:50]}') after {i+1}s")
                    cookies = current_cookies
                    break

            if cookies is None:
                # Last attempt: grab whatever cookies we have
                cookies = context.cookies()
                cookie_names = [c["name"] for c in cookies]
                log(f"    [iHerb] Timeout — cookies obtained: {cookie_names}")

            browser.close()

    except Exception as e:
        log(f"    [iHerb] Playwright error: {type(e).__name__}: {e}")
        return None

    if cookies:
        cookie_names = [c["name"] for c in cookies]
        has_clearance = "cf_clearance" in cookie_names
        log(f"    [iHerb] Cookies: {cookie_names}")
        log(f"    [iHerb] cf_clearance: {'YES' if has_clearance else 'NO'}")
        return cookies

    return None


def _build_cookie_header(cookies, domain="iherb.com"):
    """Convert Playwright cookie list to a cookie header string and a dict."""
    cookie_dict = {}
    for c in cookies:
        # Only include cookies for the iherb domain
        if domain in c.get("domain", ""):
            cookie_dict[c["name"]] = c["value"]
    return cookie_dict


# ── Session initialisation strategies ──────────────────────────────────────

def _init_playwright_cffi(log):
    """Strategy 1: Playwright cookies + curl_cffi session."""
    global _session, _session_type

    cookies = _acquire_cookies_playwright(log)
    if not cookies:
        return False

    if not HAS_CURL_CFFI:
        log(f"    [iHerb] curl_cffi not available for cookie transfer")
        return False

    cookie_dict = _build_cookie_header(cookies)
    if not cookie_dict:
        log(f"    [iHerb] No usable cookies extracted")
        return False

    log(f"    [iHerb] Transferring {len(cookie_dict)} cookies to curl_cffi session...")
    try:
        sess = cffi_requests.Session(impersonate="chrome")
        # Set cookies on the session
        for name, value in cookie_dict.items():
            sess.cookies.set(name, value, domain=".iherb.com")

        # Verify with a test request
        log(f"    [iHerb] Verifying session with homepage request...")
        resp = sess.get(IHERB_HOME, timeout=20, headers=HEADERS)

        if resp.status_code == 200 and "just a moment" not in resp.text[:3000].lower():
            log(f"    [iHerb] Session verified! HTTP {resp.status_code}")
            _session = sess
            _session_type = "playwright_cffi"
            return True
        else:
            log(f"    [iHerb] Verification failed: HTTP {resp.status_code}")

    except Exception as e:
        log(f"    [iHerb] curl_cffi session setup failed: {e}")

    return False


def _init_playwright_requests(log):
    """Strategy 2: Playwright cookies + plain requests session."""
    global _session, _session_type

    cookies = _acquire_cookies_playwright(log)
    if not cookies:
        return False

    cookie_dict = _build_cookie_header(cookies)
    if not cookie_dict:
        return False

    log(f"    [iHerb] Transferring {len(cookie_dict)} cookies to requests session...")
    try:
        sess = plain_requests.Session()
        sess.headers.update(HEADERS)
        for name, value in cookie_dict.items():
            sess.cookies.set(name, value, domain=".iherb.com")

        resp = sess.get(IHERB_HOME, timeout=20)
        if resp.status_code == 200 and "just a moment" not in resp.text[:3000].lower():
            log(f"    [iHerb] Session verified (plain requests)! HTTP {resp.status_code}")
            _session = sess
            _session_type = "playwright_requests"
            return True
        else:
            log(f"    [iHerb] Verification failed: HTTP {resp.status_code}")

    except Exception as e:
        log(f"    [iHerb] requests session setup failed: {e}")

    return False


def _init_curl_cffi_solo(log):
    """Strategy 3: curl_cffi alone (no Playwright). May work for some pages."""
    global _session, _session_type

    if not HAS_CURL_CFFI:
        return False

    profiles = ["chrome110", "chrome120", "chrome"]
    for profile in profiles:
        try:
            log(f"    [iHerb] Trying curl_cffi solo (profile={profile})...")
            sess = cffi_requests.Session(impersonate=profile)
            resp = sess.get(IHERB_HOME, timeout=25, headers=HEADERS)

            if resp.status_code == 200 and "just a moment" not in resp.text[:3000].lower():
                _session = sess
                _session_type = "curl_cffi"
                log(f"    [iHerb] curl_cffi solo session ready ({profile})")
                return True
        except Exception as e:
            log(f"    [iHerb] curl_cffi {profile} failed: {e}")

    return False


def _init_cloudscraper(log):
    """Strategy 4: cloudscraper with homepage preflight."""
    global _session, _session_type

    if not HAS_CLOUDSCRAPER:
        return False

    try:
        log(f"    [iHerb] Trying cloudscraper...")
        sess = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "linux", "desktop": True},
        )
        resp = sess.get(IHERB_HOME, timeout=25)

        if resp.status_code == 200 and "just a moment" not in resp.text[:3000].lower():
            _session = sess
            _session_type = "cloudscraper"
            log(f"    [iHerb] cloudscraper session ready")
            return True
        else:
            log(f"    [iHerb] cloudscraper homepage HTTP {resp.status_code}")
    except Exception as e:
        log(f"    [iHerb] cloudscraper failed: {e}")

    return False


# ── Session management ─────────────────────────────────────────────────────

def _ensure_session(log):
    """Lazily initialise the shared session on first use."""
    global _initialized
    if _initialized:
        return _session is not None

    log(f"    [iHerb] Initialising session...")
    log(f"    [iHerb] Available: Playwright={HAS_PLAYWRIGHT}, "
        f"curl_cffi={HAS_CURL_CFFI}, cloudscraper={HAS_CLOUDSCRAPER}")

    # Strategy 1: Playwright + curl_cffi (best)
    if HAS_PLAYWRIGHT and HAS_CURL_CFFI:
        if _init_playwright_cffi(log):
            _initialized = True
            return True

    # Strategy 2: Playwright + plain requests
    if HAS_PLAYWRIGHT:
        if _init_playwright_requests(log):
            _initialized = True
            return True

    # Strategy 3: curl_cffi solo (may work for some pages)
    if _init_curl_cffi_solo(log):
        _initialized = True
        return True

    # Strategy 4: cloudscraper
    if _init_cloudscraper(log):
        _initialized = True
        return True

    log(f"    [iHerb] !! All session init methods failed")
    _initialized = True
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


# ── Public API ─────────────────────────────────────────────────────────────

def fetch_iherb_page(url, log, max_retries=2):
    """
    Fetch an iHerb product page using the shared session.

    Returns (status_code, html_text) or (None, error_message).
    """
    if not _ensure_session(log):
        return None, "No iHerb session available"

    global _last_request_time

    # Courtesy delay between requests
    elapsed = time.time() - _last_request_time
    if _last_request_time > 0 and elapsed < COURTESY_DELAY:
        wait = COURTESY_DELAY - elapsed
        time.sleep(wait)

    delays = [4, 8]

    for attempt in range(max_retries + 1):
        try:
            if _session_type in ("playwright_cffi", "curl_cffi"):
                resp = _session.get(url, timeout=30, headers=HEADERS)
            else:
                resp = _session.get(url, timeout=30)

            status = resp.status_code
            html = resp.text
            _last_request_time = time.time()

            if not _is_blocked(status, html):
                log(f"    [iHerb] OK: HTTP {status}, {len(html)} bytes"
                    + (f" (attempt {attempt+1})" if attempt > 0 else ""))
                return status, html

            if attempt < max_retries:
                delay = delays[min(attempt, len(delays) - 1)]
                log(f"    [iHerb] Blocked (HTTP {status}), "
                    f"retrying in {delay}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(delay)
            else:
                log(f"    [iHerb] !! Blocked after {max_retries+1} attempts (HTTP {status})")
                return status, html

        except Exception as e:
            _last_request_time = time.time()
            if attempt < max_retries:
                delay = delays[min(attempt, len(delays) - 1)]
                log(f"    [iHerb] Error: {type(e).__name__}: {e}, retrying in {delay}s...")
                time.sleep(delay)
            else:
                log(f"    [iHerb] !! Failed after {max_retries+1} attempts: {e}")
                return None, str(e)

    return None, "Max retries exceeded"


def reset_session():
    """Reset the session."""
    global _session, _session_type, _initialized, _last_request_time
    _session = None
    _session_type = None
    _initialized = False
    _last_request_time = 0
