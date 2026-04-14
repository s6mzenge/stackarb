"""
iHerB SESSION MANAGER v3 — Direct Playwright
==============================================
Shared across all scrapers.

STRATEGY:
  Use Playwright (headless Chromium) directly for ALL iHerb requests.
  No cookie transfer — the browser handles everything natively.

  1. Launch headless Chromium once (lazy, on first iHerb request)
  2. Navigate to homepage to clear Cloudflare challenge
  3. For each product page: page.goto() → return page HTML
  4. Browser stays open for all ~8 iHerb products across scrapers
  5. ~3s per page, ~30s total — well within workflow timeout

  Fallback: cloudscraper (may work for some pages)

REQUIREMENTS:
  pip install playwright
  playwright install chromium --with-deps

Usage:
  from iherb_session import fetch_iherb_page
  status, html = fetch_iherb_page(url, log)
"""

import time

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False


# ── Config ─────────────────────────────────────────────────────────────────

IHERB_HOME = "https://uk.iherb.com/"
COURTESY_DELAY = 2.0  # seconds between page navigations

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ── Module-level state ─────────────────────────────────────────────────────

_pw = None          # Playwright context manager
_browser = None     # Browser instance
_context = None     # Browser context (holds cookies)
_page = None        # Single page, reused for all requests
_initialized = False
_init_failed = False
_last_nav_time = 0

# Cloudscraper fallback
_cs_session = None
_cs_initialized = False


# ── Playwright lifecycle ───────────────────────────────────────────────────

def _init_playwright(log):
    """Launch Playwright, open homepage to clear Cloudflare."""
    global _pw, _browser, _context, _page, _initialized, _init_failed

    if not HAS_PLAYWRIGHT:
        log(f"    [iHerb] Playwright not installed")
        _init_failed = True
        return False

    try:
        log(f"    [iHerb] Launching Playwright (headless Chromium)...")
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        _context = _browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="en-GB",
            timezone_id="Europe/London",
        )
        _page = _context.new_page()

        # Navigate to homepage to clear Cloudflare challenge
        log(f"    [iHerb] Navigating to homepage...")
        _page.goto(IHERB_HOME, wait_until="domcontentloaded", timeout=30000)

        # Wait for Cloudflare to resolve
        for i in range(15):
            time.sleep(1)
            title = _page.title() or ""
            cookies = _context.cookies()
            cookie_names = [c["name"] for c in cookies]

            if "cf_clearance" in cookie_names:
                log(f"    [iHerb] cf_clearance obtained after {i+1}s")
                _initialized = True
                return True

            if i > 2 and "just a moment" not in title.lower() and len(title) > 5:
                log(f"    [iHerb] Page loaded after {i+1}s (title: '{title[:40]}')")
                _initialized = True
                return True

        # Even without cf_clearance, the browser context may work
        cookies = _context.cookies()
        cookie_names = [c["name"] for c in cookies]
        log(f"    [iHerb] Timeout but continuing — cookies: {cookie_names[:8]}")
        _initialized = True
        return True

    except Exception as e:
        log(f"    [iHerb] Playwright init failed: {type(e).__name__}: {e}")
        _init_failed = True
        _cleanup()
        return False


def _cleanup():
    """Close browser resources."""
    global _pw, _browser, _context, _page
    try:
        if _page:
            _page.close()
    except Exception:
        pass
    try:
        if _context:
            _context.close()
    except Exception:
        pass
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    try:
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _page = _context = _browser = _pw = None


def _init_cloudscraper_fallback(log):
    """Fallback for when Playwright is unavailable."""
    global _cs_session, _cs_initialized

    if _cs_initialized:
        return _cs_session is not None
    _cs_initialized = True

    if not HAS_CLOUDSCRAPER:
        return False

    try:
        log(f"    [iHerb] Trying cloudscraper fallback...")
        _cs_session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "linux", "desktop": True},
        )
        resp = _cs_session.get(IHERB_HOME, timeout=25)
        if resp.status_code == 200:
            log(f"    [iHerb] cloudscraper session ready")
            return True
        else:
            log(f"    [iHerb] cloudscraper homepage HTTP {resp.status_code}")
            _cs_session = None
    except Exception as e:
        log(f"    [iHerb] cloudscraper failed: {e}")
        _cs_session = None

    return False


# ── Public API ─────────────────────────────────────────────────────────────

def fetch_iherb_page(url, log, max_retries=1):
    """
    Fetch an iHerb product page.

    Primary: Playwright (real browser, always passes Cloudflare)
    Fallback: cloudscraper (may work for some pages)

    Returns (status_code, html_text) or (None, error_message).
    """
    global _last_nav_time

    # ── Strategy 1: Playwright ─────────────────────────────────────
    if not _init_failed:
        if not _initialized:
            _init_playwright(log)

        if _initialized and _page:
            # Courtesy delay between navigations
            elapsed = time.time() - _last_nav_time
            if _last_nav_time > 0 and elapsed < COURTESY_DELAY:
                time.sleep(COURTESY_DELAY - elapsed)

            for attempt in range(max_retries + 1):
                try:
                    log(f"    [iHerb] Navigating to product page...")
                    resp = _page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    _last_nav_time = time.time()

                    # Wait a moment for dynamic content
                    time.sleep(1.5)

                    status = resp.status if resp else None
                    html = _page.content()

                    # Check if we got real content
                    if status == 200 and html and len(html) > 10000:
                        title = _page.title() or ""
                        if "just a moment" not in title.lower():
                            log(f"    [iHerb] OK: {len(html)} bytes, title: '{title[:60]}'"
                                + (f" (attempt {attempt+1})" if attempt > 0 else ""))
                            return 200, html

                    # Got a challenge or error — wait and retry
                    if attempt < max_retries:
                        log(f"    [iHerb] Got HTTP {status}, waiting 5s before retry...")
                        time.sleep(5)
                    else:
                        log(f"    [iHerb] !! Playwright failed after {max_retries+1} attempts "
                            f"(HTTP {status}, {len(html) if html else 0} bytes)")

                except Exception as e:
                    log(f"    [iHerb] Navigation error: {type(e).__name__}: {e}")
                    if attempt < max_retries:
                        time.sleep(3)

    # ── Strategy 2: cloudscraper fallback ──────────────────────────
    if _init_cloudscraper_fallback(log):
        try:
            resp = _cs_session.get(url, timeout=30)
            if resp.status_code == 200:
                lower = resp.text[:3000].lower()
                if "just a moment" not in lower and len(resp.text) > 10000:
                    log(f"    [iHerb] cloudscraper OK: {len(resp.text)} bytes")
                    return 200, resp.text
            log(f"    [iHerb] cloudscraper: HTTP {resp.status_code}")
        except Exception as e:
            log(f"    [iHerb] cloudscraper error: {e}")

    return None, "All methods failed"


def reset_session():
    """Reset everything (for testing)."""
    global _initialized, _init_failed, _last_nav_time
    global _cs_session, _cs_initialized
    _cleanup()
    _initialized = False
    _init_failed = False
    _last_nav_time = 0
    _cs_session = None
    _cs_initialized = False
