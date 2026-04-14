"""
iHerB SESSION MANAGER v5 — cloudscraper-first
===============================================
Shared across all scrapers.

STRATEGY (based on real GitHub Actions results):
  cloudscraper with homepage preflight works reliably for iHerb product
  pages — it solved 7/8 products on the last run. Playwright works for
  the first page but triggers rate-limit 403s after that, and the retry
  waits burn ~3 minutes per product for nothing.

  Order:
    1. cloudscraper (fast, ~1-2s per page, proven reliable)
    2. Playwright (fallback — useful if cloudscraper stops working)

REQUIREMENTS:
  pip install cloudscraper
  Optional: pip install playwright && playwright install chromium --with-deps

Usage:
  from iherb_session import fetch_iherb_page
  status, html = fetch_iherb_page(url, log)
"""

import time

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

try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    try:
        from playwright_stealth import Stealth
        stealth_sync = lambda page: Stealth().apply_stealth_sync(page)
        HAS_STEALTH = True
    except ImportError:
        HAS_STEALTH = False


# ── Config ─────────────────────────────────────────────────────────────────

IHERB_HOME = "https://uk.iherb.com/"
COURTESY_DELAY = 2.0

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ── Module-level state ─────────────────────────────────────────────────────

_cs_session = None
_cs_ready = False
_cs_tried = False

_pw = None
_browser = None
_context = None
_page = None
_pw_ready = False
_pw_tried = False

_last_request_time = 0


# ── Strategy 1: cloudscraper ───────────────────────────────────────────────

def _init_cloudscraper(log):
    global _cs_session, _cs_ready, _cs_tried
    if _cs_tried:
        return _cs_ready
    _cs_tried = True

    if not HAS_CLOUDSCRAPER:
        log(f"    [iHerb] cloudscraper not installed")
        return False

    try:
        log(f"    [iHerb] Initialising cloudscraper session...")
        _cs_session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "linux", "desktop": True},
        )
        # Homepage preflight to establish session cookies
        log(f"    [iHerb] Homepage preflight...")
        resp = _cs_session.get(IHERB_HOME, timeout=25)
        if resp.status_code == 200 and "just a moment" not in resp.text[:3000].lower():
            _cs_ready = True
            log(f"    [iHerb] cloudscraper session ready")
            return True
        else:
            log(f"    [iHerb] cloudscraper homepage HTTP {resp.status_code}")
            _cs_session = None
    except Exception as e:
        log(f"    [iHerb] cloudscraper init failed: {e}")
        _cs_session = None

    return False


def _fetch_cloudscraper(url, log):
    """Fetch a product page via cloudscraper. Fast (~1-2s)."""
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if _last_request_time > 0 and elapsed < COURTESY_DELAY:
        time.sleep(COURTESY_DELAY - elapsed)

    try:
        resp = _cs_session.get(url, timeout=30)
        _last_request_time = time.time()

        if resp.status_code == 200 and len(resp.text) > 10000:
            lower = resp.text[:3000].lower()
            if "just a moment" not in lower:
                log(f"    [iHerb] cloudscraper OK: {len(resp.text)} bytes")
                return 200, resp.text

        log(f"    [iHerb] cloudscraper: HTTP {resp.status_code}, {len(resp.text)} bytes")
        return resp.status_code, resp.text

    except Exception as e:
        log(f"    [iHerb] cloudscraper error: {e}")
        return None, str(e)


# ── Strategy 2: Playwright (fallback) ──────────────────────────────────────

def _init_playwright(log):
    global _pw, _browser, _context, _page, _pw_ready, _pw_tried
    if _pw_tried:
        return _pw_ready
    _pw_tried = True

    if not HAS_PLAYWRIGHT:
        log(f"    [iHerb] Playwright not installed")
        return False

    try:
        log(f"    [iHerb] Launching Playwright fallback...")
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"]
        )
        _context = _browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="en-GB",
        )
        _page = _context.new_page()

        if HAS_STEALTH:
            stealth_sync(_page)
        else:
            _page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)

        log(f"    [iHerb] Playwright: navigating to homepage...")
        _page.goto(IHERB_HOME, wait_until="networkidle", timeout=40000)

        # Wait for Cloudflare to clear
        for i in range(12):
            time.sleep(1)
            title = _page.title() or ""
            if "just a moment" not in title.lower() and len(title) > 5:
                log(f"    [iHerb] Playwright homepage loaded after {i+1}s")
                _pw_ready = True
                return True

        _pw_ready = True  # try anyway
        return True

    except Exception as e:
        log(f"    [iHerb] Playwright init failed: {e}")
        _cleanup_playwright()
        return False


def _cleanup_playwright():
    global _pw, _browser, _context, _page
    for obj in [_page, _context, _browser]:
        try:
            if obj: obj.close()
        except: pass
    try:
        if _pw: _pw.stop()
    except: pass
    _page = _context = _browser = _pw = None


def _fetch_playwright(url, log):
    """Fetch a product page via Playwright. Slower but handles tougher protection."""
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if _last_request_time > 0 and elapsed < COURTESY_DELAY:
        time.sleep(COURTESY_DELAY - elapsed)

    try:
        log(f"    [iHerb] Playwright: navigating to product page...")
        resp = _page.goto(url, wait_until="domcontentloaded", timeout=25000)
        _last_request_time = time.time()
        time.sleep(2)

        html = _page.content()
        title = _page.title() or ""

        if html and len(html) > 50000 and "just a moment" not in title.lower():
            log(f"    [iHerb] Playwright OK: {len(html)} bytes")
            return 200, html

        status = resp.status if resp else None
        log(f"    [iHerb] Playwright: HTTP {status}, {len(html) if html else 0} bytes")
        return status, html

    except Exception as e:
        log(f"    [iHerb] Playwright error: {e}")
        return None, str(e)


# ── Public API ─────────────────────────────────────────────────────────────

def fetch_iherb_page(url, log):
    """
    Fetch an iHerb product page.
    Returns (status_code, html_text) or (None, error_message).
    """
    # Strategy 1: cloudscraper (fast, reliable)
    if _init_cloudscraper(log):
        status, html = _fetch_cloudscraper(url, log)
        if status == 200 and html and len(html) > 10000:
            return status, html

    # Strategy 2: Playwright (fallback)
    if _init_playwright(log):
        status, html = _fetch_playwright(url, log)
        if status == 200 and html and len(html) > 10000:
            return status, html

    return None, "All methods failed"


def reset_session():
    global _cs_session, _cs_ready, _cs_tried
    global _pw_ready, _pw_tried, _last_request_time
    _cleanup_playwright()
    _cs_session = None
    _cs_ready = False
    _cs_tried = False
    _pw_ready = False
    _pw_tried = False
    _last_request_time = 0
