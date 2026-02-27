"""
Facebook Marketplace scraper using Playwright for browser automation.

Intercepts Facebook's GraphQL API responses to extract structured listing
data, which is far more reliable than trying to parse the React-rendered DOM.

Requires a one-time login to save cookies (run: python login.py).
"""

import asyncio
import json
import os
import random
import re
import logging

from playwright.async_api import async_playwright

from src.config import Config
from src.models import MarketplaceListing

logger = logging.getLogger(__name__)

FB_MARKETPLACE_URL = "https://www.facebook.com/marketplace"
COOKIES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fb_cookies.json")


def _build_search_url(query: str = "", max_price: int = None) -> str:
    """Build a Facebook Marketplace search URL with filters."""
    url = f"{FB_MARKETPLACE_URL}/search/?"
    params = []
    if query:
        params.append(f"query={query}")
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

        # If we see a login form, we're not logged in
        login_form = await page.query_selector('input[name="email"]')
        if login_form:
            logger.error("Cookies are expired. Run 'python login.py' again to re-login.")
            return False

        logger.info("Facebook login verified")
        return True
    except Exception as e:
        logger.error(f"Login verification failed: {e}")
        return False


async def scrape_marketplace(
    queries: list[str],
    max_price: int = None,
    max_items_per_query: int = 50,
) -> list[MarketplaceListing]:
    """
    Scrape Facebook Marketplace for listings matching search queries.

    Intercepts Facebook's GraphQL API responses to get structured listing data.

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

    async with async_playwright() as p:
        # Use system chromium if available (avoids playwright browser version mismatch)
        launch_kwargs = {"headless": Config.HEADLESS}
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

        # Anti-detection args
        launch_kwargs["args"] = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ]
        browser = await p.chromium.launch(**launch_kwargs)

        user_agent = random.choice(Config.USER_AGENTS)
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        # Remove webdriver flag that Facebook checks for bot detection
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """)

        # Load saved cookies
        if not await _load_cookies(context):
            await browser.close()
            return []

        page = await context.new_page()

        # Verify we're actually logged in
        if not await _verify_login(page):
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

            # Random delay between queries to avoid detection
            await asyncio.sleep(random.uniform(2, 5))

        await browser.close()

    logger.info(f"Total marketplace listings found: {len(all_listings)}")
    return all_listings


def _extract_listings_from_graphql(data, seen_urls: set[str]) -> list[MarketplaceListing]:
    """
    Recursively extract marketplace listings from Facebook's GraphQL response.

    Facebook nests listing data deeply and the structure varies, so we
    search recursively for nodes that look like marketplace listings.
    """
    listings = []

    if isinstance(data, dict):
        # Check if this node is a marketplace listing
        listing = _try_parse_listing_node(data, seen_urls)
        if listing:
            listings.append(listing)
            return listings  # Don't recurse into a listing's own children

        # Recurse into all dict values
        for value in data.values():
            listings.extend(_extract_listings_from_graphql(value, seen_urls))

    elif isinstance(data, list):
        for item in data:
            listings.extend(_extract_listings_from_graphql(item, seen_urls))

    return listings


def _try_parse_listing_node(node: dict, seen_urls: set[str]) -> MarketplaceListing | None:
    """
    Try to parse a dict node as a marketplace listing.

    Facebook GraphQL responses contain listing data in various shapes.
    We look for common fields: id, listing_price, marketplace_listing_title, etc.
    """
    # Pattern 1: Direct listing node with marketplace_listing_title
    listing_title = (
        node.get("marketplace_listing_title")
        or node.get("listing_title")
        or node.get("name")
    )

    # Check for listing ID (indicates this is a listing node)
    listing_id = node.get("id") or node.get("listing_id")

    # Extract price from various locations
    price = _extract_graphql_price(node)

    # If we have title and price, this looks like a listing
    if listing_title and price is not None and price > 0:
        url = f"https://www.facebook.com/marketplace/item/{listing_id}" if listing_id else ""

        if url:
            clean_url = url.split("?")[0]
            if clean_url in seen_urls:
                return None
            seen_urls.add(clean_url)

        location = _extract_graphql_location(node)
        image_url = _extract_graphql_image(node)

        return MarketplaceListing(
            title=listing_title,
            price=price,
            url=url,
            location=location,
            image_url=image_url,
        )

    # Pattern 2: Node wrapped in "node" or "listing" key
    inner = node.get("node") or node.get("listing")
    if isinstance(inner, dict) and inner is not node:
        return _try_parse_listing_node(inner, seen_urls)

    return None


def _extract_graphql_price(node: dict) -> float | None:
    """Extract price from a GraphQL listing node."""
    # Try listing_price.formatted_amount ("$150")
    listing_price = node.get("listing_price") or {}
    if isinstance(listing_price, dict):
        formatted = listing_price.get("formatted_amount") or listing_price.get("text", "")
        if formatted:
            match = re.search(r"[\d,]+(?:\.\d{2})?", formatted)
            if match:
                try:
                    return float(match.group().replace(",", ""))
                except ValueError:
                    pass
        # Try amount field (in cents or dollars)
        amount = listing_price.get("amount")
        if amount is not None:
            try:
                val = float(amount)
                # Facebook sometimes uses cents
                return val / 100 if val > 100000 else val
            except (ValueError, TypeError):
                pass

    # Try price directly on node
    price_val = node.get("price")
    if isinstance(price_val, (int, float)) and price_val > 0:
        return float(price_val)
    if isinstance(price_val, str):
        match = re.search(r"[\d,]+(?:\.\d{2})?", price_val)
        if match:
            try:
                return float(match.group().replace(",", ""))
            except ValueError:
                pass

    # Try formatted_price
    formatted = node.get("formatted_price") or node.get("price_text", "")
    if formatted:
        match = re.search(r"[\d,]+(?:\.\d{2})?", str(formatted))
        if match:
            try:
                return float(match.group().replace(",", ""))
            except ValueError:
                pass

    return None


def _extract_graphql_location(node: dict) -> str:
    """Extract location from a GraphQL listing node."""
    # Try location.reverse_geocode.city
    loc = node.get("location") or {}
    if isinstance(loc, dict):
        city = loc.get("reverse_geocode", {}).get("city", "")
        if city:
            state = loc.get("reverse_geocode", {}).get("state", "")
            return f"{city}, {state}" if state else city

    # Try marketplace_listing_seller.location
    seller = node.get("marketplace_listing_seller") or {}
    if isinstance(seller, dict):
        loc_name = seller.get("location", {})
        if isinstance(loc_name, dict):
            return loc_name.get("reverse_geocode", {}).get("city", "")

    # Try location_text
    return node.get("location_text", {}).get("text", "") if isinstance(node.get("location_text"), dict) else str(node.get("location_text", "") or "")


def _extract_graphql_image(node: dict) -> str:
    """Extract primary image URL from a GraphQL listing node."""
    # Try primary_listing_photo
    photo = node.get("primary_listing_photo") or node.get("primary_photo") or {}
    if isinstance(photo, dict):
        img = photo.get("image") or photo
        if isinstance(img, dict):
            return img.get("uri", "") or img.get("url", "")

    # Try listing_photos array
    photos = node.get("listing_photos") or node.get("photos") or []
    if isinstance(photos, list) and photos:
        first = photos[0]
        if isinstance(first, dict):
            img = first.get("image") or first
            if isinstance(img, dict):
                return img.get("uri", "") or img.get("url", "")

    # Try image directly
    img = node.get("image") or {}
    if isinstance(img, dict):
        return img.get("uri", "") or img.get("url", "")

    return ""


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

    # Collect GraphQL responses containing listing data
    captured_responses: list[dict] = []

    async def _handle_response(response):
        """Intercept API responses that contain marketplace listing data."""
        req_url = response.url
        # Facebook uses /api/graphql/ for all data fetching
        if "/api/graphql" not in req_url and "/graphql" not in req_url:
            return
        try:
            if response.status == 200:
                text = await response.text()
                # Facebook sometimes returns multiple JSON objects separated by newlines
                for line in text.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        # Only keep responses that look like they contain listings
                        text_check = json.dumps(data)
                        if "marketplace_listing_title" in text_check or "listing_price" in text_check or "marketplace_search" in text_check:
                            captured_responses.append(data)
                            logger.debug(f"Captured GraphQL response with marketplace data ({len(line)} bytes)")
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.debug(f"Error reading response: {e}")

    # Set up response interception BEFORE navigating
    page.on("response", _handle_response)

    try:
        # Navigate to the search page
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Wait for content to load
        await asyncio.sleep(random.uniform(3, 5))

        # Close any popups
        await _dismiss_popups(page)
        await asyncio.sleep(1)

        # Scroll to trigger more data loading
        listings: list[MarketplaceListing] = []
        scroll_attempts = 0
        max_scrolls = 10

        while len(listings) < max_items and scroll_attempts < max_scrolls:
            # Parse listings from captured GraphQL responses
            for resp_data in captured_responses:
                new_listings = _extract_listings_from_graphql(resp_data, seen_urls)
                listings.extend(new_listings)

            # Clear processed responses
            captured_responses.clear()

            if len(listings) >= max_items:
                break

            # Scroll down to trigger lazy loading of more results
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await asyncio.sleep(random.uniform(1.5, 3))
            scroll_attempts += 1

        # Process any remaining captured responses
        for resp_data in captured_responses:
            new_listings = _extract_listings_from_graphql(resp_data, seen_urls)
            listings.extend(new_listings)

        # Fallback: try DOM-based extraction if GraphQL interception got nothing
        if not listings:
            logger.debug("GraphQL interception found no listings, trying DOM extraction")
            listings = await _parse_listings_from_dom(page, seen_urls)

        # Debug: if still no listings, save page info
        if not listings:
            await _save_debug_info(page, query)

    finally:
        # Clean up the response handler
        page.remove_listener("response", _handle_response)

    return listings[:max_items]


async def _parse_listings_from_dom(page, seen_urls: set[str]) -> list[MarketplaceListing]:
    """
    Fallback: extract listings from the DOM.

    Tries to find listing data embedded in script tags or visible elements.
    """
    listings = []

    # Strategy A: Extract from __comet_data or relay store in script tags
    try:
        script_data = await page.evaluate("""
            () => {
                // Try to find listing data in Facebook's inline data stores
                const scripts = document.querySelectorAll('script[type="application/json"]');
                const results = [];
                for (const script of scripts) {
                    try {
                        const text = script.textContent;
                        if (text && (text.includes('marketplace_listing_title') || text.includes('listing_price'))) {
                            results.push(JSON.parse(text));
                        }
                    } catch(e) {}
                }
                // Also try window.__comet_data for SSR data
                if (typeof __comet_data !== 'undefined') {
                    try { results.push(__comet_data); } catch(e) {}
                }
                return results;
            }
        """)
        for data in (script_data or []):
            new_listings = _extract_listings_from_graphql(data, seen_urls)
            listings.extend(new_listings)
        if listings:
            logger.debug(f"Script tag extraction found {len(listings)} listings")
            return listings
    except Exception as e:
        logger.debug(f"Script extraction failed: {e}")

    # Strategy B: Extract from visible DOM elements with price patterns
    try:
        card_data = await page.evaluate("""
            () => {
                const results = [];
                // Look for any anchor with marketplace href patterns
                const allLinks = document.querySelectorAll('a[href]');
                for (const link of allLinks) {
                    const href = link.href || link.getAttribute('href') || '';
                    // Match marketplace item links or generic item links
                    if (href.includes('/marketplace/item/') || href.includes('/item/')) {
                        const text = link.innerText || '';
                        const img = link.querySelector('img');
                        const imgSrc = img ? (img.src || '') : '';
                        results.push({href, text, imgSrc});
                    }
                }
                // Also look for divs with item data
                if (results.length === 0) {
                    const divs = document.querySelectorAll('div[class]');
                    for (const div of divs) {
                        const text = div.innerText || '';
                        if (!/\\$\\d/.test(text)) continue;
                        if (text.length > 500 || text.length < 5) continue;
                        const link = div.closest('a') || div.querySelector('a');
                        if (!link) continue;
                        const href = link.href || '';
                        if (!href) continue;
                        const img = div.querySelector('img');
                        const imgSrc = img ? (img.src || '') : '';
                        results.push({href, text, imgSrc});
                    }
                }
                return results;
            }
        """)
        for item in (card_data or []):
            try:
                href = item.get("href", "")
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.facebook.com{href}"
                clean_url = href.split("?")[0]
                if clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)

                text_content = item.get("text", "")
                lines = [l.strip() for l in text_content.split("\n") if l.strip()]
                price = _extract_dom_price(lines)
                title = _extract_dom_title(lines)
                location = _extract_dom_location(lines)

                if price is None or price <= 0:
                    continue

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
        logger.debug(f"DOM extraction failed: {e}")

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

    # Check for marketplace data in various selectors
    selectors_to_try = [
        'a[href*="/marketplace/item/"]',
        'a[href*="marketplace"]',
        'a[href*="/item/"]',
        'script[type="application/json"]',
    ]
    for sel in selectors_to_try:
        try:
            elements = await page.query_selector_all(sel)
            logger.warning(f"Debug selector '{sel}' found {len(elements)} elements")
        except Exception:
            pass

    # Check for any embedded listing data in scripts
    try:
        has_data = await page.evaluate("""
            () => {
                const html = document.documentElement.innerHTML;
                return {
                    has_listing_title: html.includes('marketplace_listing_title'),
                    has_listing_price: html.includes('listing_price'),
                    has_search_results: html.includes('marketplace_search'),
                    has_item_id: html.includes('marketplace/item'),
                    total_scripts: document.querySelectorAll('script').length,
                    json_scripts: document.querySelectorAll('script[type="application/json"]').length,
                };
            }
        """)
        logger.warning(f"Debug page data indicators: {has_data}")
    except Exception as e:
        logger.debug(f"Failed to check page data: {e}")


# DOM-based price/title/location extraction (fallback)

def _extract_dom_price(lines: list[str]) -> float | None:
    """Extract price from listing text lines."""
    for line in lines:
        match = re.search(r"\$[\d,]+(?:\.\d{2})?", line)
        if match:
            price_str = match.group().replace("$", "").replace(",", "")
            try:
                return float(price_str)
            except ValueError:
                continue
    return None


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
