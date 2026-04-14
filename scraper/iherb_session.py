"""
iHerB SESSION MANAGER v4 — Stealth Playwright
===============================================
Shared across all scrapers.

PROBLEM:
  iHerb uses TWO layers of bot protection:
    1. Cloudflare — JS challenge on homepage (solved by Playwright)
    2. PerimeterX (HUMAN) — advanced browser fingerprinting on product pages
       Detects headless browsers via navigator.webdriver, plugin arrays,
       WebGL, chrome.runtime, etc.

SOLUTION:
  1. playwright-stealth patches hide all headless/automation signals
  2. Wait for PerimeterX challenge to resolve (the 403 page contains JS
     that auto-redirects once the browser passes fingerprint checks)
  3. Use networkidle to let all PX scripts complete
  4. Longer waits between pages to avoid rate-limit triggers

REQUIREMENTS:
  pip install playwright playwright-stealth
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
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False


# ── Config ─────────────────────────────────────────────────────────────────

IHERB_HOME = "https://uk.iherb.com/"
COURTESY_DELAY = 3.0  # seconds between navigations

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ── Module-level state ─────────────────────────────────────────────────────

_pw = None
_browser = None
_context = None
_page = None
_initialized = False
_init_failed = False
_last_nav_time = 0

_cs_session = None
_cs_initialized = False


# ── Playwright lifecycle ───────────────────────────────────────────────────

def _init_playwright(log):
    """Launch stealth Playwright, clear Cloudflare + PerimeterX on homepage."""
    global _pw, _browser, _context, _page, _initialized, _init_failed

    if not HAS_PLAYWRIGHT:
        log(f"    [iHerb] Playwright not installed")
        _init_failed = True
        return False

    try:
        log(f"    [iHerb] Launching Playwright (stealth={HAS_STEALTH})...")
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080",
            ]
        )
        _context = _browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="en-GB",
            timezone_id="Europe/London",
            # Realistic screen dimensions
            screen={"width": 1920, "height": 1080},
            color_scheme="light",
        )

        # Block unnecessary resources to speed up page loads
        def route_handler(route):
            if route.request.resource_type in ("image", "font", "media"):
                route.abort()
            else:
                route.continue_()
        _context.route("**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,mp4,webm}", route_handler)

        _page = _context.new_page()

        # Apply stealth patches BEFORE any navigation
        if HAS_STEALTH:
            stealth_sync(_page)
            log(f"    [iHerb] Stealth patches applied")
        else:
            # Manual stealth patches if playwright-stealth not available
            _page.add_init_script("""
                // Hide webdriver flag
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

                // Fake plugins array
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });

                // Fake languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-GB', 'en-US', 'en']
                });

                // Chrome runtime
                window.chrome = { runtime: {} };

                // Permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) =>
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters);
            """)
            log(f"    [iHerb] Manual stealth patches applied")

        # Navigate to homepage — clears Cloudflare
        log(f"    [iHerb] Navigating to homepage...")
        _page.goto(IHERB_HOME, wait_until="networkidle", timeout=45000)

        # Wait for Cloudflare + PerimeterX to settle
        log(f"    [iHerb] Waiting for protections to clear...")
        for i in range(20):
            time.sleep(1)
            cookies = _context.cookies()
            cookie_names = [c["name"] for c in cookies]

            has_cf = "cf_clearance" in cookie_names
            has_px = any(c.startswith("_px") for c in cookie_names)

            if has_cf and has_px:
                log(f"    [iHerb] Both CF and PX cookies obtained after {i+1}s")
                _initialized = True
                return True

            if has_cf and i > 5:
                log(f"    [iHerb] CF cleared after {i+1}s (PX={'yes' if has_px else 'pending'})")
                _initialized = True
                return True

            title = _page.title() or ""
            if i > 3 and "just a moment" not in title.lower() and len(title) > 10:
                log(f"    [iHerb] Page loaded after {i+1}s — CF={'yes' if has_cf else 'no'}, PX={'yes' if has_px else 'no'}")
                _initialized = True
                return True

        cookies = _context.cookies()
        cookie_names = [c["name"] for c in cookies]
        log(f"    [iHerb] Init timeout — cookies: {cookie_names[:10]}")
        _initialized = True
        return True

    except Exception as e:
        log(f"    [iHerb] Playwright init failed: {type(e).__name__}: {e}")
        _init_failed = True
        _cleanup()
        return False


def _cleanup():
    global _pw, _browser, _context, _page
    for obj in [_page, _context, _browser]:
        try:
            if obj:
                obj.close()
        except Exception:
            pass
    try:
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _page = _context = _browser = _pw = None


def _init_cloudscraper_fallback(log):
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
        _cs_session = None
    except Exception as e:
        log(f"    [iHerb] cloudscraper failed: {e}")
        _cs_session = None
    return False


# ── Product page fetching ──────────────────────────────────────────────────

def _fetch_with_playwright(url, log, max_retries=2):
    """
    Navigate to a product page. If we get a 403/challenge, wait for
    PerimeterX to resolve it (the 403 page runs JS that auto-clears).
    """
    global _last_nav_time

    # Courtesy delay
    elapsed = time.time() - _last_nav_time
    if _last_nav_time > 0 and elapsed < COURTESY_DELAY:
        time.sleep(COURTESY_DELAY - elapsed)

    for attempt in range(max_retries + 1):
        try:
            log(f"    [iHerb] Navigating to product page"
                + (f" (attempt {attempt+1})" if attempt > 0 else "") + "...")

            resp = _page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _last_nav_time = time.time()
            status = resp.status if resp else None

            # If 403 — this might be a PX challenge page.
            # Wait for PX JS to execute and potentially redirect/clear.
            if status == 403:
                log(f"    [iHerb] Got 403 — waiting for PX challenge to resolve...")
                # Wait up to 12 seconds for the page to change
                for wait_s in range(12):
                    time.sleep(1)
                    try:
                        # Check if URL changed (PX redirect)
                        current_url = _page.url
                        if current_url != url and "challenge" not in current_url:
                            log(f"    [iHerb] PX redirected to: {current_url[:80]}")

                        # Check page content
                        html = _page.content()
                        title = _page.title() or ""

                        # Success indicators
                        if len(html) > 50000 and "iherb" in title.lower():
                            log(f"    [iHerb] PX resolved after {wait_s+1}s! "
                                f"({len(html)} bytes, title: '{title[:50]}')")
                            return 200, html

                        # Check if product data appeared
                        if "product-title" in html or "ProductDetail" in html:
                            log(f"    [iHerb] Product content detected after {wait_s+1}s")
                            return 200, html

                    except Exception:
                        pass

                # PX didn't resolve — try clicking/interacting
                log(f"    [iHerb] PX didn't auto-resolve, checking final state...")

            # Get final page state
            time.sleep(1.5)
            html = _page.content()
            title = _page.title() or ""
            final_url = _page.url

            # Success check
            if html and len(html) > 50000:
                if "just a moment" not in title.lower():
                    log(f"    [iHerb] OK: {len(html)} bytes, title: '{title[:50]}'")
                    return 200, html

            # Still blocked
            if attempt < max_retries:
                wait = 5 + attempt * 3
                log(f"    [iHerb] Still blocked (HTTP {status}, {len(html) if html else 0} bytes), "
                    f"waiting {wait}s...")
                time.sleep(wait)
            else:
                log(f"    [iHerb] !! Failed after {max_retries+1} attempts "
                    f"(HTTP {status}, {len(html) if html else 0} bytes)")
                return status, html

        except Exception as e:
            log(f"    [iHerb] Error: {type(e).__name__}: {e}")
            if attempt < max_retries:
                time.sleep(5)
            else:
                return None, str(e)

    return None, "Max retries exceeded"


# ── Public API ─────────────────────────────────────────────────────────────

def fetch_iherb_page(url, log):
    """
    Fetch an iHerb product page.
    Returns (status_code, html_text) or (None, error_message).
    """
    # Strategy 1: Stealth Playwright
    if not _init_failed:
        if not _initialized:
            _init_playwright(log)

        if _initialized and _page:
            status, html = _fetch_with_playwright(url, log)
            if status == 200 and html and len(html) > 10000:
                return status, html

    # Strategy 2: cloudscraper
    if _init_cloudscraper_fallback(log):
        try:
            resp = _cs_session.get(url, timeout=30)
            if resp.status_code == 200 and len(resp.text) > 10000:
                lower = resp.text[:3000].lower()
                if "just a moment" not in lower:
                    log(f"    [iHerb] cloudscraper OK: {len(resp.text)} bytes")
                    return 200, resp.text
        except Exception as e:
            log(f"    [iHerb] cloudscraper error: {e}")

    return None, "All methods failed"


def reset_session():
    global _initialized, _init_failed, _last_nav_time
    global _cs_session, _cs_initialized
    _cleanup()
    _initialized = False
    _init_failed = False
    _last_nav_time = 0
    _cs_session = None
    _cs_initialized = False
