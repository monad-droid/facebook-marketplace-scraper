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
        browser = await p.chromium.launch(headless=Config.HEADLESS)
        context = await browser.new_context(
            user_agent=random.choice(Config.USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

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

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(random.uniform(3, 5))

    # Close any popups/dialogs that Facebook likes to show
    await _dismiss_popups(page)

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

    return listings[:max_items]


async def _dismiss_popups(page) -> None:
    """Dismiss common Facebook popups that block scraping."""
    try:
        # "Log in" dialog close button
        close_buttons = await page.query_selector_all('[aria-label="Close"]')
        for btn in close_buttons:
            try:
                await btn.click()
                await asyncio.sleep(0.5)
            except Exception:
                pass

        # Cookie consent
        cookie_btn = await page.query_selector('[data-cookiebanner="accept_button"]')
        if cookie_btn:
            await cookie_btn.click()
            await asyncio.sleep(0.5)
    except Exception:
        pass


async def _parse_listings(page, seen_urls: set[str]) -> list[MarketplaceListing]:
    """Parse listing cards from the current page state."""
    listings = []

    # Facebook Marketplace listing cards — target the link elements
    # that contain price and title information
    cards = await page.query_selector_all(
        'a[href*="/marketplace/item/"]'
    )

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
