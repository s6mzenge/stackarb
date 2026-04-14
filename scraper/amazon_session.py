"""
AMAZON SESSION MANAGER v2 — Headed Chrome via Xvfb
====================================================
Shared across all scrapers.

KEY CHANGES FROM v1:
  1. HEADED MODE (not headless) + Xvfb virtual display
     - Eliminates ALL headless-specific detection signals
     - navigator.webdriver, CDP detection, rendering differences — all gone
     - Requires `xvfb-run python scraper/run_all.py` in the workflow

  2. SYSTEM CHROME (`channel="chrome"`)
     - Uses the real Google Chrome installed on the runner, not Playwright's
       bundled Chromium. Different binary signature.
     - GitHub Actions Ubuntu has Chrome pre-installed.

  3. HUMAN-LIKE INTERACTION on homepage
     - Random mouse movements, scrolling, small delays
     - Makes the session look like a real user before hitting product pages

  4. UK GEOLOCATION in browser context

  5. FIREFOX FALLBACK if Chrome gets blocked
     - Completely different browser fingerprint

REQUIREMENTS:
  Workflow: `xvfb-run python scraper/run_all.py`
  pip install playwright
  playwright install chromium --with-deps
"""

import time, re, random

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

AMAZON_HOME = "https://www.amazon.co.uk/"
COURTESY_DELAY = 3.5

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# London coordinates for geolocation
UK_GEO = {"latitude": 51.5074, "longitude": -0.1278}


# ── Module-level state ─────────────────────────────────────────────────────

_pw = None
_browser = None
_context = None
_page = None
_initialized = False
_init_failed = False
_last_nav_time = 0
_browser_type = None  # "chrome" or "firefox"


# ── Human-like interaction ─────────────────────────────────────────────────

def _human_interact(page, log):
    """Simulate human behavior on the page to build session trust."""
    try:
        # Random mouse movements
        for _ in range(3):
            x = random.randint(200, 1200)
            y = random.randint(200, 600)
            page.mouse.move(x, y)
            time.sleep(random.uniform(0.2, 0.5))

        # Scroll down a bit
        page.mouse.wheel(0, random.randint(200, 500))
        time.sleep(random.uniform(0.5, 1.0))

        # Scroll back up
        page.mouse.wheel(0, -random.randint(100, 300))
        time.sleep(random.uniform(0.3, 0.7))

        log(f"    [Amazon] Human interaction simulated")
    except Exception:
        pass


# ── Browser launch strategies ──────────────────────────────────────────────

def _try_chrome_headed(log):
    """Strategy 1: System Chrome in HEADED mode (best anti-detection)."""
    global _pw, _browser, _context, _page, _browser_type

    try:
        log(f"    [Amazon] Launching system Chrome (headed mode, Xvfb)...")
        _pw = sync_playwright().start()

        _browser = _pw.chromium.launch(
            headless=False,      # HEADED mode — Xvfb provides display
            channel="chrome",    # Use system Chrome, not bundled Chromium
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--start-maximized",
                "--lang=en-GB",
            ]
        )
        _browser_type = "chrome"
        return True

    except Exception as e:
        log(f"    [Amazon] Chrome headed launch failed: {type(e).__name__}: {e}")
        # Clean up partial state
        try:
            if _browser: _browser.close()
        except: pass
        _browser = None

        # Try headed Chromium (bundled) as fallback
        try:
            log(f"    [Amazon] Trying bundled Chromium (headed)...")
            _browser = _pw.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--window-size=1920,1080",
                    "--lang=en-GB",
                ]
            )
            _browser_type = "chromium"
            return True
        except Exception as e2:
            log(f"    [Amazon] Chromium headed also failed: {e2}")
            return False


def _try_firefox(log):
    """Strategy 2: Firefox — completely different fingerprint."""
    global _pw, _browser, _context, _page, _browser_type

    try:
        if not _pw:
            _pw = sync_playwright().start()

        log(f"    [Amazon] Trying Firefox (headed)...")
        _browser = _pw.firefox.launch(
            headless=False,
            args=["--width=1920", "--height=1080"]
        )
        _browser_type = "firefox"
        return True

    except Exception as e:
        log(f"    [Amazon] Firefox launch failed: {e}")
        return False


def _setup_context_and_navigate(log):
    """Create browser context, apply stealth, navigate to homepage."""
    global _context, _page

    context_opts = {
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-GB",
        "timezone_id": "Europe/London",
        "geolocation": UK_GEO,
        "permissions": ["geolocation"],
        "screen": {"width": 1920, "height": 1080},
        "color_scheme": "light",
    }
    # Only set user_agent for Chromium (Firefox has its own realistic one)
    if _browser_type != "firefox":
        context_opts["user_agent"] = USER_AGENT

    _context = _browser.new_context(**context_opts)
    _page = _context.new_page()

    # Apply stealth patches (Chromium only — Firefox doesn't need them)
    if _browser_type != "firefox":
        if HAS_STEALTH:
            stealth_sync(_page)
            log(f"    [Amazon] playwright-stealth patches applied")
        else:
            _page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-GB', 'en-US', 'en']
                });
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
            """)
            log(f"    [Amazon] Manual stealth patches applied")

    # Navigate to homepage
    log(f"    [Amazon] Navigating to homepage ({_browser_type})...")
    _page.goto(AMAZON_HOME, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    # Check for CAPTCHA on homepage
    title = _page.title() or ""
    if len(title) < 15 or "robot" in title.lower():
        log(f"    [Amazon] Homepage blocked: '{title}'")
        return False

    log(f"    [Amazon] Homepage loaded: '{title[:55]}'")

    # Accept cookie consent
    _accept_cookies(log)

    # Human interaction to build trust
    _human_interact(_page, log)

    # Set UK delivery location
    _set_uk_location(log)

    return True


# ── Init orchestrator ──────────────────────────────────────────────────────

def _init_playwright(log):
    """Try browsers in order: Chrome headed → Chromium headed → Firefox."""
    global _initialized, _init_failed

    if not HAS_PLAYWRIGHT:
        log(f"    [Amazon] Playwright not installed")
        _init_failed = True
        return False

    # Strategy 1: Chrome/Chromium headed
    if _try_chrome_headed(log):
        if _setup_context_and_navigate(log):
            _initialized = True
            return True
        # Homepage blocked with Chrome — try Firefox
        _cleanup()

    # Strategy 2: Firefox
    if _try_firefox(log):
        if _setup_context_and_navigate(log):
            _initialized = True
            return True
        _cleanup()

    # Strategy 3: Last resort — headless Chromium
    try:
        log(f"    [Amazon] Last resort: headless Chromium...")
        if not _pw:
            _pw = sync_playwright().start()
        global _browser, _browser_type
        _browser = _pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"]
        )
        _browser_type = "chromium-headless"
        if _setup_context_and_navigate(log):
            _initialized = True
            return True
    except Exception as e:
        log(f"    [Amazon] Headless fallback failed: {e}")

    _init_failed = True
    _cleanup()
    return False


# ── Cookie consent & location ──────────────────────────────────────────────

def _accept_cookies(log):
    try:
        for sel in ["#sp-cc-accept",
                     "[data-action='sp-cc'][data-action-type='ACCEPT_ALL']",
                     "input[name='accept']"]:
            try:
                btn = _page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    log(f"    [Amazon] Cookie consent accepted")
                    time.sleep(1)
                    return
            except: continue
    except: pass


def _set_uk_location(log):
    try:
        deliver = _page.locator("#glow-ingress-block, #nav-global-location-popover-link")
        if deliver.count() == 0 or not deliver.first.is_visible():
            log(f"    [Amazon] No delivery widget found")
            return

        deliver.first.click()
        time.sleep(1.5)

        inp = _page.locator("#GLUXZipUpdateInput")
        if inp.count() > 0 and inp.first.is_visible():
            inp.first.fill("")
            inp.first.type("SW1A 1AA", delay=80)
            time.sleep(0.5)

            for sel in ["#GLUXZipUpdate",
                        "[data-action='GLUXPostalInputAction']"]:
                try:
                    btn = _page.locator(sel)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click()
                        log(f"    [Amazon] UK postcode set (SW1A 1AA)")
                        time.sleep(2)
                        try:
                            close = _page.locator("#GLUXConfirmClose, .a-popover-footer button")
                            if close.count() > 0 and close.first.is_visible():
                                close.first.click()
                                time.sleep(0.5)
                        except: pass
                        return
                except: continue
    except Exception as e:
        log(f"    [Amazon] Location: {type(e).__name__}: {e}")


# ── Cleanup ────────────────────────────────────────────────────────────────

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


# ── Page fetching ──────────────────────────────────────────────────────────

def _is_captcha(page):
    try:
        title = (page.title() or "").lower()
        url = page.url.lower()
        html = page.content()[:3000].lower()
        return ("robot" in title or "captcha" in title or
                "validatecaptcha" in url or
                "captcha" in html[:2000] or
                "type the characters" in html)
    except:
        return False


def _wait_for_price(page, log, timeout=8):
    for i in range(timeout):
        try:
            if page.locator("span.a-price-whole").first.is_visible():
                return True
        except: pass
        try:
            content = page.content()
            if "currently unavailable" in content.lower():
                return True
        except: pass
        time.sleep(1)
    return False


def fetch_amazon_page(url, log, max_retries=1):
    """
    Fetch an Amazon product page.
    Returns (status_code, html_text) or (None, error_message).
    """
    global _last_nav_time

    if not _init_failed:
        if not _initialized:
            _init_playwright(log)

        if _initialized and _page:
            elapsed = time.time() - _last_nav_time
            if _last_nav_time > 0 and elapsed < COURTESY_DELAY:
                time.sleep(COURTESY_DELAY - elapsed)

            for attempt in range(max_retries + 1):
                try:
                    log(f"    [Amazon] Navigating to product page"
                        + (f" (attempt {attempt+1})" if attempt > 0 else "")
                        + f" [{_browser_type}]...")

                    resp = _page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    _last_nav_time = time.time()
                    time.sleep(1)

                    if _is_captcha(_page):
                        log(f"    [Amazon] CAPTCHA detected")
                        if attempt < max_retries:
                            time.sleep(5)
                            continue
                        break

                    _wait_for_price(_page, log)
                    html = _page.content()
                    title = _page.title() or ""

                    if html and len(html) > 20000 and "Amazon" in title:
                        log(f"    [Amazon] OK: {len(html)} bytes, title: '{title[:55]}'")
                        return 200, html

                except Exception as e:
                    log(f"    [Amazon] Error: {type(e).__name__}: {e}")
                    if attempt < max_retries:
                        time.sleep(3)

    return None, "Playwright failed or unavailable"


def reset_session():
    global _initialized, _init_failed, _last_nav_time
    _cleanup()
    _initialized = False
    _init_failed = False
    _last_nav_time = 0
