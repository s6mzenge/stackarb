"""
AMAZON SESSION MANAGER — Playwright-based
===========================================
Shared across all scrapers.

WHY PLAYWRIGHT:
  1. Amazon renders prices via JavaScript — plain requests gets empty price elements
  2. Amazon CAPTCHAs datacenter IPs — Playwright with stealth patches looks like a real browser
  3. Cookie consent must be accepted before Amazon shows full content

STRATEGY:
  1. Launch stealth Playwright (headless Chromium)
  2. Navigate to amazon.co.uk → accept cookie consent
  3. For each product page: page.goto() → wait for price to render → return HTML
  4. Browser stays open across all Amazon products

  Fallback: plain requests (for when Amazon intermittently allows it)

REQUIREMENTS:
  pip install playwright
  playwright install chromium --with-deps
  Optional: pip install playwright-stealth

Usage:
  from amazon_session import fetch_amazon_page
  status, html = fetch_amazon_page(url, log)
"""

import time, re

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


# ── Config ─────────────────────────────────────────────────────────────────

AMAZON_HOME = "https://www.amazon.co.uk/"
COURTESY_DELAY = 3.0  # seconds between product page navigations

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


# ── Playwright lifecycle ───────────────────────────────────────────────────

def _init_playwright(log):
    """Launch stealth Playwright, visit Amazon homepage, accept cookies."""
    global _pw, _browser, _context, _page, _initialized, _init_failed

    if not HAS_PLAYWRIGHT:
        log(f"    [Amazon] Playwright not installed — using plain requests")
        _init_failed = True
        return False

    try:
        log(f"    [Amazon] Launching Playwright (stealth={HAS_STEALTH})...")
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--lang=en-GB",
            ]
        )
        _context = _browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="en-GB",
            timezone_id="Europe/London",
            screen={"width": 1920, "height": 1080},
            color_scheme="light",
        )

        _page = _context.new_page()

        # Apply stealth patches
        if HAS_STEALTH:
            stealth_sync(_page)
            log(f"    [Amazon] Stealth patches applied")
        else:
            _page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-GB', 'en-US', 'en']
                });
                window.chrome = { runtime: {} };
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) =>
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters);
            """)
            log(f"    [Amazon] Manual stealth patches applied")

        # Navigate to Amazon homepage
        log(f"    [Amazon] Navigating to homepage...")
        _page.goto(AMAZON_HOME, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        # Accept cookie consent (GDPR banner)
        _accept_cookies(log)

        # Check we're not CAPTCHA'd on homepage
        title = _page.title() or ""
        if "robot" in title.lower() or "captcha" in title.lower():
            log(f"    [Amazon] !! Homepage CAPTCHA detected")
            _init_failed = True
            _cleanup()
            return False

        log(f"    [Amazon] Homepage loaded: '{title[:50]}'")
        _initialized = True
        return True

    except Exception as e:
        log(f"    [Amazon] Playwright init failed: {type(e).__name__}: {e}")
        _init_failed = True
        _cleanup()
        return False


def _accept_cookies(log):
    """Click the Amazon cookie consent button if present."""
    try:
        # Amazon UK cookie consent button (various selectors)
        for selector in [
            "#sp-cc-accept",                    # Standard accept button
            "[data-action='sp-cc'][data-action-type='ACCEPT_ALL']",
            "input[name='accept']",             # Older style
            "#a-autoid-0-announce",             # Another variant
        ]:
            try:
                btn = _page.locator(selector)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    log(f"    [Amazon] Cookie consent accepted ({selector})")
                    time.sleep(1)
                    return
            except Exception:
                continue
        log(f"    [Amazon] No cookie consent banner found (may be pre-accepted)")
    except Exception as e:
        log(f"    [Amazon] Cookie consent handling: {e}")


def _cleanup():
    global _pw, _browser, _context, _page
    for obj in [_page, _context, _browser]:
        try:
            if obj: obj.close()
        except: pass
    try:
        if _pw: _pw.stop()
    except: pass
    _page = _context = _browser = _pw = None


# ── Product page fetching ──────────────────────────────────────────────────

def _is_captcha(page):
    """Check if the current page is a CAPTCHA challenge."""
    try:
        title = (page.title() or "").lower()
        url = page.url.lower()
        html_start = page.content()[:3000].lower()
        return (
            "robot" in title or
            "captcha" in title or
            "/errors/validateCaptcha" in url or
            "captcha" in html_start[:2000] or
            "type the characters" in html_start
        )
    except:
        return False


def _wait_for_price(page, log, timeout=8):
    """Wait for Amazon price elements to render (they're JS-loaded)."""
    for i in range(timeout):
        try:
            # Check for the main price element
            price_el = page.locator("span.a-price-whole").first
            if price_el.is_visible():
                return True
        except:
            pass

        try:
            # Check for "Currently unavailable" or "Out of stock"
            content = page.content()
            if "currently unavailable" in content.lower() or "out of stock" in content.lower():
                log(f"    [Amazon] Product unavailable/out of stock")
                return True  # page loaded, just no price
        except:
            pass

        time.sleep(1)

    return False


def fetch_amazon_page(url, log, max_retries=1):
    """
    Fetch an Amazon product page using Playwright.

    Returns (status_code, html_text) or (None, error_message).
    The HTML will have JS-rendered content (prices, dynamic elements).
    """
    global _last_nav_time

    # ── Strategy 1: Playwright ─────────────────────────────────────
    if not _init_failed:
        if not _initialized:
            _init_playwright(log)

        if _initialized and _page:
            # Courtesy delay
            elapsed = time.time() - _last_nav_time
            if _last_nav_time > 0 and elapsed < COURTESY_DELAY:
                time.sleep(COURTESY_DELAY - elapsed)

            for attempt in range(max_retries + 1):
                try:
                    log(f"    [Amazon] Navigating to product page"
                        + (f" (attempt {attempt+1})" if attempt > 0 else "") + "...")

                    resp = _page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    _last_nav_time = time.time()

                    # Check for CAPTCHA
                    time.sleep(1)
                    if _is_captcha(_page):
                        log(f"    [Amazon] CAPTCHA detected")
                        if attempt < max_retries:
                            time.sleep(5)
                            continue
                        else:
                            log(f"    [Amazon] !! CAPTCHA persisted after {max_retries+1} attempts")
                            break

                    # Wait for price to render
                    _wait_for_price(_page, log)

                    html = _page.content()
                    title = _page.title() or ""

                    if html and len(html) > 20000 and "Amazon" in title:
                        log(f"    [Amazon] OK: {len(html)} bytes, title: '{title[:60]}'")
                        return 200, html

                    log(f"    [Amazon] Unexpected page state (HTTP {resp.status if resp else '?'}, "
                        f"{len(html) if html else 0} bytes)")

                except Exception as e:
                    log(f"    [Amazon] Navigation error: {type(e).__name__}: {e}")
                    if attempt < max_retries:
                        time.sleep(3)

    # ── Strategy 2: plain requests fallback (existing behavior) ────
    # Don't implement here — let the scraper's own extract_amazon handle it
    # This means if Playwright fails, the scraper falls back to its existing
    # requests-based code and then to KNOWN values
    return None, "Playwright failed or unavailable"


def reset_session():
    global _initialized, _init_failed, _last_nav_time
    _cleanup()
    _initialized = False
    _init_failed = False
    _last_nav_time = 0
