"""
Facebook Marketplace scraper using Playwright for browser automation.

Uses a multi-strategy approach:
1. Intercepts ALL GraphQL/API responses for structured data
2. Parses Relay prefetched stream cache from script tags
3. Extracts from rendered DOM by finding price elements and their card containers

Requires a one-time login to save cookies (run: python login.py).
"""

import asyncio
import json
import os
import random
import re
import logging
import shutil
import subprocess
from urllib.parse import quote

from playwright.async_api import async_playwright

from src.config import Config
from src.models import MarketplaceListing

logger = logging.getLogger(__name__)

FB_MARKETPLACE_URL = "https://www.facebook.com/marketplace"
COOKIES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fb_cookies.json")

_xvfb_process = None


def _ensure_display():
    """
    Ensure a DISPLAY is available for headed Chrome.

    On headless servers (no monitor), starts Xvfb virtual display.
    This makes Chrome run in full headed mode (bypassing headless detection)
    while rendering to a virtual framebuffer instead of a real screen.
    """
    global _xvfb_process

    # Already have a display (desktop environment or previous xvfb)
    if os.environ.get("DISPLAY"):
        return

    # Install xvfb if needed
    if not shutil.which("Xvfb"):
        logger.info("Installing xvfb for virtual display...")
        subprocess.run(
            ["apt-get", "install", "-y", "xvfb"],
            capture_output=True, timeout=60,
        )

    if not shutil.which("Xvfb"):
        logger.warning("Xvfb not available, falling back to headless mode")
        return

    # Start Xvfb on a free display
    for display_num in range(99, 110):
        display = f":{display_num}"
        try:
            _xvfb_process = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1920x1080x24", "-ac"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give it a moment to start
            import time
            time.sleep(0.5)
            if _xvfb_process.poll() is None:
                os.environ["DISPLAY"] = display
                logger.info(f"Started virtual display on {display}")
                return
        except Exception:
            continue

    logger.warning("Failed to start Xvfb, falling back to headless mode")


def _build_search_url(query: str = "", max_price: int = None) -> str:
    """Build a Facebook Marketplace search URL with filters."""
    url = f"{FB_MARKETPLACE_URL}/search/?"
    params = []
    if query:
        params.append(f"query={quote(query)}")
    if max_price is not None:
        params.append(f"maxPrice={max_price}")
    params.append("sortBy=creation_time_descend")
    params.append("exact=false")
    return url + "&".join(params)


async def _load_cookies(context) -> bool:
    """Load saved Facebook cookies into the browser context."""
    if not os.path.exists(COOKIES_FILE):
        logger.error(
            f"No cookies file found at {COOKIES_FILE}. "
            "Run 'python login.py' first to log into Facebook."
        )
        return False

    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        logger.info(f"Loaded {len(cookies)} cookies from {COOKIES_FILE}")
        return True
    except Exception as e:
        logger.error(f"Failed to load cookies: {e}")
        return False


async def _verify_login(page) -> bool:
    """Check if we're actually logged into Facebook."""
    try:
        await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        login_form = await page.query_selector('input[name="email"]')
        if login_form:
            logger.error("Cookies are expired. Run 'python login.py' again to re-login.")
            return False

        logger.info("Facebook login verified")
        return True
    except Exception as e:
        logger.error(f"Login verification failed: {e}")
        return False


async def _refresh_cookies(context) -> None:
    """
    Save refreshed cookies back to disk after a successful page load.

    Facebook rotates session tokens (especially 'xs') on each visit.
    By capturing the updated cookies and writing them back, the session
    stays alive across runs — no manual re-login needed as long as the
    scraper runs every few days.
    """
    try:
        all_cookies = await context.cookies(["https://www.facebook.com"])
        # Only keep the essential Facebook cookies
        fb_cookies = [
            c for c in all_cookies
            if c.get("domain", "").endswith("facebook.com")
            and c.get("name") in ("c_user", "xs", "datr", "fr", "sb", "wd")
        ]
        if not fb_cookies:
            return

        # Ensure we have the critical ones
        names = {c["name"] for c in fb_cookies}
        if "c_user" not in names or "xs" not in names:
            return

        # Convert to the simple format login.py uses (strip expires/session fields
        # that Playwright adds, so the file stays clean)
        clean_cookies = []
        for c in fb_cookies:
            clean_cookies.append({
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "secure": c.get("secure", True),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": c.get("sameSite", "None"),
            })

        with open(COOKIES_FILE, "w") as f:
            json.dump(clean_cookies, f, indent=2)
        logger.info(f"Refreshed {len(clean_cookies)} cookies to {COOKIES_FILE}")
    except Exception as e:
        logger.debug(f"Cookie refresh failed (non-fatal): {e}")


async def _is_error_page(page) -> bool:
    """Check if the current page is Facebook's generic error page."""
    try:
        title = await page.title()
        if title.strip().lower() == "error":
            return True
        # Also check for the "Sorry, something went wrong" text
        content = await page.text_content("body")
        if content and "sorry, something went wrong" in content.lower():
            return True
    except Exception:
        pass
    return False


async def _warmup_marketplace(page) -> bool:
    """
    Navigate to the Marketplace main page before searching.

    Facebook is more likely to serve Marketplace if you arrive organically
    (from the main site) rather than hitting a search URL cold.
    Returns True if Marketplace loaded successfully.
    """
    try:
        logger.info("Warming up: visiting Marketplace main page...")
        await page.goto(
            "https://www.facebook.com/marketplace/",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        await asyncio.sleep(random.uniform(2, 4))

        if await _is_error_page(page):
            logger.warning("Marketplace main page returned error, retrying via feed link...")
            # Try clicking the Marketplace link from the feed (more organic)
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            mp_link = await page.query_selector('a[href*="/marketplace"]')
            if mp_link:
                await mp_link.click()
                await asyncio.sleep(3)
                if await _is_error_page(page):
                    return False
            else:
                return False

        logger.info("Marketplace page loaded successfully")
        await _dismiss_popups(page)
        return True
    except Exception as e:
        logger.error(f"Marketplace warm-up failed: {e}")
        return False


async def _search_via_search_box(page, query: str, max_price: int) -> bool:
    """
    Type the search query into Marketplace's search box instead of navigating
    to a direct URL. This is more human-like and less likely to be blocked.

    Returns True if navigation succeeded (doesn't guarantee results).
    """
    try:
        # Look for the Marketplace search input
        search_input = await page.query_selector(
            'input[placeholder*="Search Marketplace"], '
            'input[aria-label*="Search Marketplace"], '
            'input[placeholder*="search"], '
            'input[type="search"]'
        )
        if not search_input:
            logger.debug("No search box found on Marketplace page")
            return False

        await search_input.click()
        await asyncio.sleep(random.uniform(0.5, 1))

        # Clear existing text and type the query with human-like delays
        await search_input.fill("")
        for char in query:
            await search_input.type(char, delay=random.uniform(50, 120))
        await asyncio.sleep(random.uniform(0.5, 1))

        # Press Enter to search
        await search_input.press("Enter")
        await asyncio.sleep(random.uniform(3, 5))

        if await _is_error_page(page):
            return False

        logger.info("Search via search box succeeded")
        return True
    except Exception as e:
        logger.debug(f"Search box approach failed: {e}")
        return False


async def scrape_marketplace(
    queries: list[str],
    max_price: int = None,
    max_items_per_query: int = 50,
) -> list[MarketplaceListing]:
    """
    Scrape Facebook Marketplace for listings matching search queries.

    Args:
        queries: List of search terms to look for.
        max_price: Maximum price filter (defaults to config value).
        max_items_per_query: Max listings to collect per search query.

    Returns:
        List of MarketplaceListing objects.
    """
    if max_price is None:
        max_price = Config.FB_MAX_PRICE

    all_listings: list[MarketplaceListing] = []
    seen_urls: set[str] = set()

    # Ensure virtual display is available on headless servers
    _ensure_display()

    async with async_playwright() as p:
        # IMPORTANT: Use headless=False with xvfb virtual display.
        # Facebook detects headless Chrome and refuses to render listings.
        # Running headed mode behind a virtual display is indistinguishable
        # from a real browser with a real monitor.
        launch_kwargs = {"headless": False}

        chromium_path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
        if not chromium_path:
            for candidate in [
                "/root/.cache/ms-playwright/chromium-1194/chrome-linux/chrome",
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/usr/bin/google-chrome-stable",
            ]:
                if os.path.exists(candidate):
                    chromium_path = candidate
                    break
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
            logger.info(f"Using chromium at: {chromium_path}")

        launch_kwargs["args"] = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
        ]
        browser = await p.chromium.launch(**launch_kwargs)

        context = await browser.new_context(
            user_agent=random.choice(Config.USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        # Anti-fingerprinting: make the browser look completely normal
        await context.add_init_script("""
            // Remove webdriver flag
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            // Normal language settings
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            // Real-looking plugins array
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const plugins = [
                        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
                        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                        {name: 'Native Client', filename: 'internal-nacl-plugin'},
                    ];
                    plugins.length = 3;
                    return plugins;
                }
            });
            // Override permissions query
            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (params) => (
                params.name === 'notifications' ?
                    Promise.resolve({state: Notification.permission}) :
                    origQuery(params)
            );
            // Chrome runtime
            window.chrome = { runtime: {} };
        """)

        if not await _load_cookies(context):
            await browser.close()
            return []

        page = await context.new_page()

        if not await _verify_login(page):
            await browser.close()
            return []

        # Save refreshed cookies — Facebook rotates session tokens on each visit.
        # This keeps the session alive across runs without manual re-login.
        await _refresh_cookies(context)

        # Warm up: visit Marketplace main page first (like a real user).
        # Going directly to search URLs from datacenter IPs often triggers
        # Facebook's "something went wrong" error page.
        marketplace_ok = await _warmup_marketplace(page)
        if not marketplace_ok:
            logger.error(
                "Facebook Marketplace is not accessible from this IP/account. "
                "This usually means Facebook is blocking datacenter IPs. "
                "Consider using a residential proxy."
            )
            await browser.close()
            return []

        for query in queries:
            try:
                listings = await _scrape_query(
                    page, query, max_price, max_items_per_query, seen_urls
                )
                all_listings.extend(listings)
                logger.info(f"Found {len(listings)} listings for '{query}'")
            except Exception as e:
                logger.error(f"Error scraping query '{query}': {e}")

            await asyncio.sleep(random.uniform(2, 5))

        await browser.close()

    logger.info(f"Total marketplace listings found: {len(all_listings)}")
    return all_listings


async def _scrape_query(
    page,
    query: str,
    max_price: int,
    max_items: int,
    seen_urls: set[str],
) -> list[MarketplaceListing]:
    """Scrape a single search query from Facebook Marketplace."""
    url = _build_search_url(query=query, max_price=max_price)
    logger.info(f"Scraping: {url}")

    # Capture ALL API responses (not filtered by keywords)
    captured_api_texts: list[str] = []

    async def _handle_response(response):
        req_url = response.url
        if "/api/graphql" not in req_url:
            return
        try:
            if response.status == 200:
                text = await response.text()
                if text and len(text) > 100:
                    captured_api_texts.append(text)
        except Exception:
            pass

    page.on("response", _handle_response)

    try:
        # Try direct URL first
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        await asyncio.sleep(random.uniform(3, 5))

        # If direct URL hit an error page, fall back to search box
        if await _is_error_page(page):
            logger.warning("Direct search URL returned error page, trying search box...")
            # Navigate back to Marketplace main page
            await page.goto(
                "https://www.facebook.com/marketplace/",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await asyncio.sleep(random.uniform(2, 3))
            if not await _search_via_search_box(page, query, max_price):
                logger.error(f"Both search methods failed for '{query}'")
                await _save_debug_info(page, query)
                return []

        await _dismiss_popups(page)
        await asyncio.sleep(1)

        # Scroll a few times to load more content
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await asyncio.sleep(random.uniform(1.5, 3))

        # Give time for final API responses
        await asyncio.sleep(2)

        listings: list[MarketplaceListing] = []

        # Strategy 1: Parse Relay data from script tags (SSR data)
        relay_listings = await _extract_from_relay_scripts(page, seen_urls)
        if relay_listings:
            logger.info(f"Relay script extraction found {len(relay_listings)} listings")
            listings.extend(relay_listings)

        # Strategy 2: Parse captured GraphQL API responses
        if not listings:
            for text in captured_api_texts:
                api_listings = _parse_api_response_text(text, seen_urls)
                listings.extend(api_listings)
            if listings:
                logger.info(f"API response extraction found {len(listings)} listings")

        # Strategy 3: Extract from rendered DOM
        if not listings:
            dom_listings = await _extract_from_rendered_dom(page, seen_urls)
            if dom_listings:
                logger.info(f"DOM extraction found {len(dom_listings)} listings")
                listings.extend(dom_listings)

        if not listings:
            await _save_debug_info(page, query)

    finally:
        page.remove_listener("response", _handle_response)

    return listings[:max_items]


async def _extract_from_relay_scripts(page, seen_urls: set[str]) -> list[MarketplaceListing]:
    """
    Extract listing data from Facebook's Relay prefetched stream cache.

    Facebook embeds search results in <script type="application/json"> tags
    using their Relay data format. We search through ALL script tags for
    any JSON that contains price-like values and marketplace-related data.
    """
    listings = []

    try:
        # Extract all JSON script content that might contain listing data
        raw_data_list = await page.evaluate("""
            () => {
                const scripts = document.querySelectorAll('script[type="application/json"]');
                const results = [];
                for (const script of scripts) {
                    try {
                        const text = script.textContent;
                        if (!text) continue;
                        // Only include scripts that have price-like patterns or marketplace refs
                        if (text.includes('$') || text.includes('amount') ||
                            text.includes('marketplace') || text.includes('Marketplace') ||
                            text.includes('listing') || text.includes('Listing')) {
                            const parsed = JSON.parse(text);
                            results.push(parsed);
                        }
                    } catch(e) {}
                }
                return results;
            }
        """)

        for data in (raw_data_list or []):
            found = _deep_extract_listings(data, seen_urls)
            listings.extend(found)

    except Exception as e:
        logger.debug(f"Relay script extraction error: {e}")

    return listings


def _parse_api_response_text(text: str, seen_urls: set[str]) -> list[MarketplaceListing]:
    """Parse a raw API response text that may contain multiple JSON lines."""
    listings = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            found = _deep_extract_listings(data, seen_urls)
            listings.extend(found)
        except json.JSONDecodeError:
            continue
    return listings


def _deep_extract_listings(data, seen_urls: set[str], depth=0) -> list[MarketplaceListing]:
    """
    Recursively search through arbitrarily nested data for listing-like objects.

    A listing is identified by having:
    - A numeric ID (the listing ID)
    - A price (in any format: amount, formatted_amount, text with $)
    - A title/name string

    Facebook's Relay format nests data deeply and uses various key names,
    so we try many combinations.
    """
    if depth > 20:
        return []

    listings = []

    if isinstance(data, dict):
        listing = _try_extract_single_listing(data, seen_urls)
        if listing:
            return [listing]

        for value in data.values():
            listings.extend(_deep_extract_listings(value, seen_urls, depth + 1))

    elif isinstance(data, list):
        for item in data:
            listings.extend(_deep_extract_listings(item, seen_urls, depth + 1))

    return listings


def _try_extract_single_listing(node: dict, seen_urls: set[str]) -> MarketplaceListing | None:
    """
    Check if a dict node represents a marketplace listing.

    Tries many possible Facebook field name patterns.
    """
    if not isinstance(node, dict):
        return None

    # --- Extract title ---
    title = None
    for key in [
        "marketplace_listing_title", "listing_title",
        "marketplace_listing_name",
    ]:
        if key in node and isinstance(node[key], str) and len(node[key]) > 2:
            title = node[key]
            break

    # Also check 'name' but only if it looks like a product title (not a person)
    if not title and "name" in node and isinstance(node["name"], str):
        name = node["name"]
        # Must have other listing-like fields to use 'name'
        has_price_field = any(k in node for k in [
            "listing_price", "price", "formatted_price",
            "price_amount", "current_price",
        ])
        has_listing_field = any(k in node for k in [
            "listing_id", "marketplace_listing_title",
            "primary_listing_photo", "listing_photos",
            "delivery_types", "marketplace_listing_category_id",
            "marketplace_listing_seller", "is_live",
            "creation_time", "custom_title",
        ])
        if has_price_field or has_listing_field:
            title = name

    # Check nested: node.listing.title or node.node.title
    if not title:
        for wrapper_key in ["node", "listing", "target"]:
            inner = node.get(wrapper_key)
            if isinstance(inner, dict) and inner is not node:
                result = _try_extract_single_listing(inner, seen_urls)
                if result:
                    return result

    if not title:
        return None

    # --- Extract price ---
    price = _extract_price_from_node(node)
    if price is None or price <= 0:
        return None

    # --- Extract ID and build URL ---
    listing_id = None
    for key in ["id", "listing_id", "marketplace_listing_id", "pk"]:
        val = node.get(key)
        if val is not None:
            listing_id = str(val)
            break

    if listing_id:
        url = f"https://www.facebook.com/marketplace/item/{listing_id}"
        clean_url = url.split("?")[0]
        if clean_url in seen_urls:
            return None
        seen_urls.add(clean_url)
    else:
        url = ""

    # --- Extract location ---
    location = _extract_location_from_node(node)

    # --- Extract image ---
    image_url = _extract_image_from_node(node)

    return MarketplaceListing(
        title=title,
        price=price,
        url=url,
        location=location,
        image_url=image_url,
    )


def _extract_price_from_node(node: dict) -> float | None:
    """Try every known way Facebook encodes prices."""
    # Direct price fields
    for price_key in ["listing_price", "price", "current_price", "price_amount"]:
        val = node.get(price_key)
        if val is None:
            continue

        if isinstance(val, dict):
            # Try formatted_amount, text, amount
            for text_key in ["formatted_amount", "text", "formatted_amount_with_offset_and_symbol"]:
                text_val = val.get(text_key, "")
                if text_val:
                    parsed = _parse_price_string(str(text_val))
                    if parsed is not None:
                        return parsed
            # Try numeric amount
            amount = val.get("amount")
            if amount is not None:
                try:
                    v = float(amount)
                    return v / 100 if v > 100000 else v
                except (ValueError, TypeError):
                    pass
            # Try currency_amount
            amount = val.get("currency_amount")
            if amount is not None:
                try:
                    return float(amount)
                except (ValueError, TypeError):
                    pass

        elif isinstance(val, (int, float)):
            v = float(val)
            if v > 0:
                return v

        elif isinstance(val, str):
            parsed = _parse_price_string(val)
            if parsed is not None:
                return parsed

    # Try formatted_price, price_text
    for key in ["formatted_price", "price_text"]:
        val = node.get(key)
        if val:
            if isinstance(val, dict):
                val = val.get("text", "") or val.get("formatted_amount", "")
            parsed = _parse_price_string(str(val))
            if parsed is not None:
                return parsed

    return None


def _parse_price_string(s: str) -> float | None:
    """Parse a price from a string like '$30', '$1,500.00', '150'."""
    match = re.search(r"\$?([\d,]+(?:\.\d{2})?)", s)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _extract_location_from_node(node: dict) -> str:
    """Extract location from a listing node."""
    # Try location.reverse_geocode
    loc = node.get("location")
    if isinstance(loc, dict):
        rg = loc.get("reverse_geocode")
        if isinstance(rg, dict):
            city = rg.get("city", "")
            state = rg.get("state", "")
            if city:
                return f"{city}, {state}" if state else city

    # Try location_text
    lt = node.get("location_text")
    if isinstance(lt, dict):
        return lt.get("text", "")
    if isinstance(lt, str):
        return lt

    # Try marketplace_listing_seller.location
    seller = node.get("marketplace_listing_seller")
    if isinstance(seller, dict):
        sloc = seller.get("location")
        if isinstance(sloc, dict):
            rg = sloc.get("reverse_geocode", {})
            return rg.get("city", "")

    return ""


def _extract_image_from_node(node: dict) -> str:
    """Extract primary image URL from a listing node."""
    # Try primary_listing_photo, primary_photo, curratedPhoto
    for photo_key in ["primary_listing_photo", "primary_photo", "photo", "curated_photo"]:
        photo = node.get(photo_key)
        if isinstance(photo, dict):
            img = photo.get("image") or photo.get("photo") or photo
            if isinstance(img, dict):
                uri = img.get("uri") or img.get("url") or img.get("src", "")
                if uri:
                    return uri

    # Try listing_photos, photos array
    for photos_key in ["listing_photos", "photos"]:
        photos = node.get(photos_key)
        if isinstance(photos, list) and photos:
            first = photos[0]
            if isinstance(first, dict):
                img = first.get("image") or first
                if isinstance(img, dict):
                    return img.get("uri") or img.get("url", "")

    # Try image directly
    img = node.get("image")
    if isinstance(img, dict):
        return img.get("uri") or img.get("url", "")

    return ""


async def _extract_from_rendered_dom(page, seen_urls: set[str]) -> list[MarketplaceListing]:
    """
    Extract listings from the rendered page by finding price elements
    and walking up the DOM to find their containing card.

    This works regardless of Facebook's class names or href patterns.
    """
    listings = []

    try:
        card_data = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Find ALL text nodes that show a price ($XX)
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    {
                        acceptNode: (node) => {
                            const text = node.textContent.trim();
                            if (/^\\$\\d/.test(text) && text.length < 20) {
                                return NodeFilter.FILTER_ACCEPT;
                            }
                            return NodeFilter.FILTER_REJECT;
                        }
                    }
                );

                const priceNodes = [];
                while (walker.nextNode()) {
                    priceNodes.push(walker.currentNode);
                }

                for (const priceNode of priceNodes) {
                    // Walk up to find the card container
                    // A card is typically a div that's roughly 200-500px wide
                    let card = priceNode.parentElement;
                    let foundCard = null;
                    let attempts = 0;

                    while (card && attempts < 15) {
                        const rect = card.getBoundingClientRect();
                        // A listing card is typically between 150-600px wide
                        if (rect.width > 150 && rect.width < 600 && rect.height > 100) {
                            // Check if this card has an image (listing cards always have images)
                            const img = card.querySelector('img');
                            if (img) {
                                foundCard = card;
                                break;
                            }
                        }
                        card = card.parentElement;
                        attempts++;
                    }

                    if (!foundCard) continue;

                    // Deduplicate by card element
                    const cardId = foundCard.getAttribute('data-debug-id') ||
                                   foundCard.className.substring(0, 50) + foundCard.getBoundingClientRect().top;
                    if (seen.has(cardId)) continue;
                    seen.add(cardId);

                    // Extract data from the card
                    const text = foundCard.innerText || '';
                    const img = foundCard.querySelector('img');
                    const imgSrc = img ? (img.src || '') : '';

                    // Find any link in or around the card
                    let href = '';
                    const link = foundCard.querySelector('a[href]') || foundCard.closest('a[href]');
                    if (link) {
                        href = link.href || link.getAttribute('href') || '';
                    }

                    // Also check parent links
                    if (!href) {
                        let parent = foundCard.parentElement;
                        for (let i = 0; i < 5 && parent; i++) {
                            if (parent.tagName === 'A' && parent.href) {
                                href = parent.href;
                                break;
                            }
                            const parentLink = parent.querySelector('a[href]');
                            if (parentLink) {
                                href = parentLink.href || '';
                                break;
                            }
                            parent = parent.parentElement;
                        }
                    }

                    results.push({
                        text: text.substring(0, 500),
                        href: href,
                        imgSrc: imgSrc,
                        priceText: priceNode.textContent.trim(),
                    });
                }

                return results;
            }
        """)

        for item in (card_data or []):
            try:
                price_text = item.get("priceText", "")
                price = _parse_price_string(price_text)
                if price is None or price <= 0:
                    continue

                href = item.get("href", "")
                if href and href.startswith("/"):
                    href = f"https://www.facebook.com{href}"
                clean_url = href.split("?")[0] if href else ""

                if clean_url:
                    if clean_url in seen_urls:
                        continue
                    seen_urls.add(clean_url)

                text_content = item.get("text", "")
                lines = [l.strip() for l in text_content.split("\n") if l.strip()]

                title = _extract_dom_title(lines)
                location = _extract_dom_location(lines)

                listing = MarketplaceListing(
                    title=title,
                    price=price,
                    url=clean_url,
                    location=location,
                    image_url=item.get("imgSrc", ""),
                )
                listings.append(listing)

            except Exception as e:
                logger.debug(f"Error parsing DOM card: {e}")

    except Exception as e:
        logger.debug(f"Rendered DOM extraction failed: {e}")

    return listings


async def _dismiss_popups(page) -> None:
    """Dismiss common Facebook popups that block scraping."""
    try:
        cookie_btn = await page.query_selector('[data-cookiebanner="accept_button"]')
        if cookie_btn:
            await cookie_btn.click()
            await asyncio.sleep(0.5)
    except Exception:
        pass

    try:
        dialogs = await page.query_selector_all('[role="dialog"]')
        for dialog in dialogs:
            try:
                text = await dialog.inner_text()
                if "log in" in text.lower() or "sign up" in text.lower():
                    close_btn = await dialog.query_selector('[aria-label="Close"]')
                    if close_btn:
                        await close_btn.click()
                        await asyncio.sleep(0.5)
            except Exception:
                pass
    except Exception:
        pass


async def _save_debug_info(page, query: str) -> None:
    """Save screenshot and HTML when no listings are found, for debugging."""
    debug_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug")
    os.makedirs(debug_dir, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")

    try:
        current_url = page.url
        title = await page.title()
        logger.warning(f"Debug: Current URL: {current_url}")
        logger.warning(f"Debug: Page title: {title}")
    except Exception:
        pass

    try:
        screenshot_path = os.path.join(debug_dir, f"{slug}_screenshot.png")
        await page.screenshot(path=screenshot_path, full_page=True)
        logger.warning(f"Debug screenshot saved: {screenshot_path}")
    except Exception as e:
        logger.debug(f"Failed to save screenshot: {e}")

    try:
        html_path = os.path.join(debug_dir, f"{slug}_page.html")
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.warning(f"Debug HTML saved: {html_path}")
    except Exception as e:
        logger.debug(f"Failed to save HTML: {e}")

    # Check for rendered price elements
    try:
        price_info = await page.evaluate("""
            () => {
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT,
                    { acceptNode: (n) => /^\\$\\d/.test(n.textContent.trim()) ?
                        NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT }
                );
                const prices = [];
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    const parent = node.parentElement;
                    prices.push({
                        price: node.textContent.trim(),
                        parentTag: parent ? parent.tagName : 'none',
                        parentClass: parent ? parent.className.substring(0, 50) : '',
                        visible: parent ? parent.offsetParent !== null : false,
                    });
                }
                return {count: prices.length, samples: prices.slice(0, 10)};
            }
        """)
        logger.warning(f"Debug: Found {price_info['count']} rendered price elements")
        for p in price_info.get('samples', []):
            logger.warning(f"  {p['price']} in <{p['parentTag']}> visible={p['visible']} class={p['parentClass']}")
    except Exception as e:
        logger.debug(f"Failed to check prices: {e}")


def _extract_dom_title(lines: list[str]) -> str:
    """Extract the item title from listing text lines."""
    for line in lines:
        if "$" not in line and len(line) > 3:
            return line
    return lines[0] if lines else "Unknown Item"


def _extract_dom_location(lines: list[str]) -> str:
    """Extract location from listing text lines."""
    for line in reversed(lines):
        if "$" not in line and len(line) > 2:
            return line
    return ""
