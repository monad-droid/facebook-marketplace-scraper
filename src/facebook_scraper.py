"""
Facebook Marketplace scraper using Playwright for browser automation.

Scrapes listings under a given price threshold from Facebook Marketplace.
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

    Requires cookies from a prior login (run login.py first).

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
            # Try common locations for pre-installed chromium
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

        # Anti-detection: add args to make headless chrome look more normal
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

    # Use networkidle to wait for React/JS to finish rendering content
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception:
        # Fallback if networkidle times out (Facebook streams data)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(random.uniform(3, 5))

    # Close any popups/dialogs that Facebook likes to show
    await _dismiss_popups(page)

    # Wait a moment for any popup dismissal to settle
    await asyncio.sleep(1)

    # Scroll to load more listings
    listings: list[MarketplaceListing] = []
    scroll_attempts = 0
    max_scrolls = 10
    last_count = 0
    stale_scrolls = 0

    while len(listings) < max_items and scroll_attempts < max_scrolls:
        # Parse listings currently visible on page
        new_listings = await _parse_listings(page, seen_urls)
        listings.extend(new_listings)

        # Check if we're still finding new listings
        if len(listings) == last_count:
            stale_scrolls += 1
            if stale_scrolls >= 3:
                break  # No new listings after 3 scrolls, stop
        else:
            stale_scrolls = 0
        last_count = len(listings)

        # Scroll down to trigger lazy loading
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await asyncio.sleep(random.uniform(1.5, 3))
        scroll_attempts += 1

    # Debug: if no listings found, save page info for troubleshooting
    if not listings:
        await _save_debug_info(page, query)

    return listings[:max_items]


async def _save_debug_info(page, query: str) -> None:
    """Save screenshot and HTML when no listings are found, for debugging."""
    debug_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug")
    os.makedirs(debug_dir, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")

    # Log current URL and title (detect redirects)
    try:
        current_url = page.url
        title = await page.title()
        logger.warning(f"Debug: Current URL: {current_url}")
        logger.warning(f"Debug: Page title: {title}")
    except Exception:
        pass

    # Save screenshot
    try:
        screenshot_path = os.path.join(debug_dir, f"{slug}_screenshot.png")
        await page.screenshot(path=screenshot_path, full_page=True)
        logger.warning(f"Debug screenshot saved: {screenshot_path}")
    except Exception as e:
        logger.debug(f"Failed to save screenshot: {e}")

    # Save page HTML
    try:
        html_path = os.path.join(debug_dir, f"{slug}_page.html")
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.warning(f"Debug HTML saved: {html_path}")
    except Exception as e:
        logger.debug(f"Failed to save HTML: {e}")

    # Log what selectors find
    selectors_to_try = [
        'a[href*="/marketplace/item/"]',
        'a[href*="marketplace"]',
        'div[class*="marketplace"]',
        'a[href*="/item/"]',
        '[data-testid]',
    ]
    for sel in selectors_to_try:
        try:
            elements = await page.query_selector_all(sel)
            logger.warning(f"Debug selector '{sel}' found {len(elements)} elements")
        except Exception:
            pass

    # Log all unique href patterns on the page
    try:
        hrefs = await page.evaluate("""
            () => {
                const links = document.querySelectorAll('a[href]');
                const patterns = new Set();
                for (const link of links) {
                    const href = link.getAttribute('href');
                    if (href && href.includes('marketplace')) {
                        patterns.add(href.substring(0, 80));
                    }
                }
                return [...patterns].slice(0, 20);
            }
        """)
        if hrefs:
            logger.warning(f"Debug marketplace hrefs found: {hrefs}")
        else:
            logger.warning("Debug: No marketplace hrefs found on page at all")
    except Exception as e:
        logger.debug(f"Failed to extract hrefs: {e}")


async def _dismiss_popups(page) -> None:
    """Dismiss common Facebook popups that block scraping."""
    try:
        # Cookie consent
        cookie_btn = await page.query_selector('[data-cookiebanner="accept_button"]')
        if cookie_btn:
            await cookie_btn.click()
            await asyncio.sleep(0.5)
    except Exception:
        pass

    try:
        # "Login" overlay that sometimes appears even when logged in
        # Only close dialogs that look like login prompts, not other UI
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


async def _parse_listings(page, seen_urls: set[str]) -> list[MarketplaceListing]:
    """Parse listing cards from the current page state."""
    listings = []

    # Strategy 1: Direct link selector (classic approach)
    cards = await page.query_selector_all('a[href*="/marketplace/item/"]')

    if not cards:
        # Strategy 2: Facebook sometimes uses encoded URLs or different patterns
        cards = await page.query_selector_all('a[href*="marketplace/item"]')

    if not cards:
        # Strategy 3: Extract via JavaScript - more reliable for dynamic content
        # Facebook's React app may not have traditional href attributes visible
        # to querySelector but the data is in the DOM
        try:
            card_data = await page.evaluate("""
                () => {
                    const results = [];
                    // Find all links on page and filter for marketplace items
                    const allLinks = document.querySelectorAll('a[href]');
                    for (const link of allLinks) {
                        const href = link.href || link.getAttribute('href') || '';
                        if (href.includes('/marketplace/item/') || href.includes('marketplace/item')) {
                            const text = link.innerText || '';
                            const img = link.querySelector('img');
                            const imgSrc = img ? (img.src || img.getAttribute('src') || '') : '';
                            results.push({href, text, imgSrc});
                        }
                    }
                    return results;
                }
            """)
            if card_data:
                logger.debug(f"JS extraction found {len(card_data)} marketplace links")
                for item in card_data:
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
                        price = _extract_price(lines)
                        title = _extract_title(lines)
                        location = _extract_location(lines)

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
                        logger.debug(f"Error parsing JS-extracted card: {e}")
                return listings
        except Exception as e:
            logger.debug(f"JS extraction failed: {e}")

    if not cards:
        # Strategy 4: Look for listing-like containers with price patterns
        # Facebook uses div-based cards with nested links
        try:
            card_data = await page.evaluate("""
                () => {
                    const results = [];
                    // Look for any element that contains a $ price and a link
                    const allElements = document.querySelectorAll('div');
                    for (const el of allElements) {
                        const text = el.innerText || '';
                        // Must contain a price
                        if (!/\\$\\d/.test(text)) continue;
                        // Must have a marketplace link somewhere inside
                        const link = el.querySelector('a[href*="marketplace"]') || el.querySelector('a[href*="/item/"]');
                        if (!link) continue;
                        const href = link.href || link.getAttribute('href') || '';
                        if (!href.includes('/item/') && !href.includes('/marketplace/')) continue;
                        // Must be a reasonably sized card (not the whole page)
                        if (text.length > 500 || text.length < 5) continue;
                        const img = el.querySelector('img');
                        const imgSrc = img ? (img.src || '') : '';
                        results.push({href, text, imgSrc});
                    }
                    return results;
                }
            """)
            if card_data:
                logger.debug(f"Div-based extraction found {len(card_data)} potential cards")
                for item in card_data:
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
                        price = _extract_price(lines)
                        title = _extract_title(lines)
                        location = _extract_location(lines)

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
                        logger.debug(f"Error parsing div card: {e}")
                return listings
        except Exception as e:
            logger.debug(f"Div extraction failed: {e}")

    # Process cards found by Strategy 1 or 2
    for card in cards:
        try:
            href = await card.get_attribute("href")
            if not href:
                continue

            # Normalize URL
            if href.startswith("/"):
                href = f"https://www.facebook.com{href}"

            # Remove query params for dedup
            clean_url = href.split("?")[0]
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            # Extract text content from the card
            text_content = await card.inner_text()
            lines = [line.strip() for line in text_content.split("\n") if line.strip()]

            price = _extract_price(lines)
            title = _extract_title(lines)
            location = _extract_location(lines)

            if price is None or price <= 0:
                continue

            # Extract image if available
            img = await card.query_selector("img")
            image_url = ""
            if img:
                image_url = await img.get_attribute("src") or ""

            listing = MarketplaceListing(
                title=title,
                price=price,
                url=clean_url,
                location=location,
                image_url=image_url,
            )
            listings.append(listing)

        except Exception as e:
            logger.debug(f"Error parsing listing card: {e}")
            continue

    return listings


def _extract_price(lines: list[str]) -> float | None:
    """Extract price from listing text lines."""
    for line in lines:
        # Match patterns like "$100", "$1,500", "$50.00"
        match = re.search(r"\$[\d,]+(?:\.\d{2})?", line)
        if match:
            price_str = match.group().replace("$", "").replace(",", "")
            try:
                return float(price_str)
            except ValueError:
                continue
    return None


def _extract_title(lines: list[str]) -> str:
    """Extract the item title from listing text lines."""
    # Title is usually the first non-price, non-location line
    for line in lines:
        if "$" not in line and len(line) > 3:
            return line
    return lines[0] if lines else "Unknown Item"


def _extract_location(lines: list[str]) -> str:
    """Extract location from listing text lines."""
    # Location is typically one of the last lines
    for line in reversed(lines):
        if "$" not in line and len(line) > 2:
            return line
    return ""
