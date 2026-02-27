"""
eBay sold listings checker.

Searches eBay's completed/sold listings to find what items actually sold for
in the past N days. This gives us real market data for pricing.
"""

import re
import random
import logging
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from src.config import Config
from src.models import EbaySoldItem

logger = logging.getLogger(__name__)

EBAY_SOLD_URL = "https://www.ebay.com/sch/i.html"


def search_ebay_sold(query: str, days: int = None) -> list[EbaySoldItem]:
    """
    Search eBay for recently sold/completed listings.

    Args:
        query: Search term to look up on eBay.
        days: Only include items sold within this many days.

    Returns:
        List of EbaySoldItem with actual sale prices.
    """
    if days is None:
        days = Config.EBAY_SOLD_DAYS

    params = {
        "_nkw": query,
        "LH_Sold": "1",        # Sold listings only
        "LH_Complete": "1",    # Completed listings
        "_sop": "13",          # Sort by end date: recent first
        "rt": "nc",
        "_ipg": "60",          # Results per page
    }

    headers = {
        "User-Agent": random.choice(Config.USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        response = requests.get(EBAY_SOLD_URL, params=params, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"eBay request failed for '{query}': {e}")
        return []

    soup = BeautifulSoup(response.text, "lxml")
    items = _parse_sold_listings(soup, days)

    logger.info(f"Found {len(items)} sold eBay items for '{query}' in last {days} days")
    return items


def _parse_sold_listings(soup: BeautifulSoup, days: int) -> list[EbaySoldItem]:
    """Parse sold listings from eBay search results page."""
    items = []
    cutoff_date = datetime.now() - timedelta(days=days)

    # eBay search results are in <li> tags with class s-item
    result_cards = soup.select("li.s-item")

    for card in result_cards:
        try:
            item = _parse_single_listing(card, cutoff_date)
            if item is not None:
                items.append(item)
        except Exception as e:
            logger.debug(f"Error parsing eBay listing: {e}")
            continue

    return items


def _parse_single_listing(card, cutoff_date: datetime) -> EbaySoldItem | None:
    """Parse a single eBay sold listing card."""
    # Title
    title_el = card.select_one(".s-item__title")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)

    # Skip "Shop on eBay" promotional cards
    if title.lower().startswith("shop on ebay"):
        return None

    # Sold price
    price_el = card.select_one(".s-item__price")
    if not price_el:
        return None
    sold_price = _parse_price(price_el.get_text(strip=True))
    if sold_price is None:
        return None

    # Sold date
    sold_date_el = card.select_one(".s-item__title--tagblock .POSITIVE")
    if not sold_date_el:
        # Also check for ended date text
        sold_date_el = card.select_one(".s-item__ended-date")

    sold_date_str = ""
    if sold_date_el:
        sold_date_str = sold_date_el.get_text(strip=True)
        parsed_date = _parse_sold_date(sold_date_str)
        if parsed_date and parsed_date < cutoff_date:
            return None  # Too old

    # Shipping cost
    shipping_el = card.select_one(".s-item__shipping, .s-item__freeXDays")
    shipping_cost = 0.0
    if shipping_el:
        shipping_text = shipping_el.get_text(strip=True).lower()
        if "free" in shipping_text:
            shipping_cost = 0.0
        else:
            shipping_cost = _parse_price(shipping_text) or 0.0

    # URL
    link_el = card.select_one("a.s-item__link")
    url = link_el.get("href", "") if link_el else ""

    return EbaySoldItem(
        title=title,
        sold_price=sold_price,
        sold_date=sold_date_str,
        shipping_cost=shipping_cost,
        url=url,
    )


def _parse_price(text: str) -> float | None:
    """Extract a numeric price from text like '$149.99' or '$1,200.00'."""
    # Handle price ranges like "$100.00 to $200.00" — take the lower bound
    match = re.search(r"\$[\d,]+(?:\.\d{2})?", text)
    if match:
        price_str = match.group().replace("$", "").replace(",", "")
        try:
            return float(price_str)
        except ValueError:
            return None
    return None


def _parse_sold_date(text: str) -> datetime | None:
    """Parse sold date text like 'Sold  Jan 15, 2024'."""
    # Remove the "Sold" prefix
    cleaned = re.sub(r"^Sold\s+", "", text, flags=re.IGNORECASE).strip()

    formats = [
        "%b %d, %Y",   # "Jan 15, 2024"
        "%b-%d-%y",     # "Jan-15-24"
        "%m/%d/%Y",     # "01/15/2024"
        "%d %b %Y",     # "15 Jan 2024"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue

    return None


def get_ebay_market_value(query: str, days: int = None) -> dict:
    """
    Get market value summary for an item based on eBay sold data.

    Returns dict with avg_price, min_price, max_price, num_sold, and items.
    """
    items = search_ebay_sold(query, days)

    if not items:
        return {
            "avg_price": 0,
            "min_price": 0,
            "max_price": 0,
            "num_sold": 0,
            "items": [],
        }

    prices = [item.sold_price for item in items]

    return {
        "avg_price": sum(prices) / len(prices),
        "min_price": min(prices),
        "max_price": max(prices),
        "num_sold": len(prices),
        "items": items,
    }
