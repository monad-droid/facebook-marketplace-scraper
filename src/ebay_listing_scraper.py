"""
eBay active listings scraper.

Searches eBay for currently active (Buy It Now) listings and returns
structured results. Used by the tracker to detect newly listed items.
"""

import re
import random
import logging
import time
import hashlib

import requests
from bs4 import BeautifulSoup

from src.config import Config

logger = logging.getLogger(__name__)

EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"

_session: requests.Session | None = None
_request_count = 0


def _get_session() -> requests.Session:
    """Get or create a persistent session with proper headers."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })
        try:
            _session.headers["User-Agent"] = random.choice(Config.USER_AGENTS)
            _session.get("https://www.ebay.com/", timeout=10)
        except Exception:
            pass
    return _session


def _reset_session():
    global _session, _request_count
    if _session:
        _session.close()
    _session = None
    _request_count = 0


def search_ebay_active(query: str, max_results: int = 48) -> list[dict]:
    """
    Search eBay for currently active listings.

    Returns list of dicts with keys: id, title, price, url, image_url, listing_type
    """
    global _request_count

    params = {
        "_nkw": query,
        "LH_BIN": "1",        # Buy It Now only
        "_sop": "10",         # Sort by: Newly Listed
        "rt": "nc",
        "_ipg": str(min(max_results, 240)),
    }

    session = _get_session()
    if _request_count % 5 == 0:
        session.headers["User-Agent"] = random.choice(Config.USER_AGENTS)
    session.headers["Referer"] = "https://www.ebay.com/"

    max_retries = 2
    response = None
    for attempt in range(max_retries + 1):
        try:
            response = session.get(
                EBAY_SEARCH_URL,
                params=params,
                timeout=15,
                allow_redirects=True,
            )

            final_url = response.url
            if "splashui/challenge" in final_url or "captcha" in final_url.lower():
                logger.warning(f"eBay CAPTCHA detected (attempt {attempt + 1})")
                if attempt < max_retries:
                    backoff = 15 * (2 ** attempt)
                    logger.info(f"  Waiting {backoff}s before retry...")
                    time.sleep(backoff)
                    _reset_session()
                    session = _get_session()
                    continue
                else:
                    logger.error(f"eBay blocked after {max_retries + 1} attempts for '{query}'")
                    return []

            response.raise_for_status()
            _request_count += 1
            break

        except requests.RequestException as e:
            logger.error(f"eBay request failed for '{query}': {e}")
            return []

    if response is None:
        return []

    soup = BeautifulSoup(response.text, "lxml")
    return _parse_active_listings(soup)


def _parse_active_listings(soup: BeautifulSoup) -> list[dict]:
    """Parse active listings from eBay search results."""
    items = []
    result_cards = soup.select("li.s-item")

    for card in result_cards:
        try:
            item = _parse_single_active(card)
            if item is not None:
                items.append(item)
        except Exception as e:
            logger.debug(f"Error parsing eBay listing: {e}")
            continue

    return items


def _parse_single_active(card) -> dict | None:
    """Parse a single active eBay listing card."""
    # Title
    title_el = card.select_one(".s-item__title")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)

    if title.lower().startswith("shop on ebay"):
        return None

    # Price
    price_el = card.select_one(".s-item__price")
    if not price_el:
        return None
    price = _parse_price(price_el.get_text(strip=True))
    if price is None:
        return None

    # URL
    link_el = card.select_one("a.s-item__link")
    url = link_el.get("href", "") if link_el else ""

    # Extract item ID from URL or generate a stable hash
    item_id = _extract_item_id(url) or _hash_listing(title, price)

    # Image
    img_el = card.select_one(".s-item__image-wrapper img")
    image_url = ""
    if img_el:
        image_url = img_el.get("src", "") or img_el.get("data-src", "")

    # Shipping
    shipping_el = card.select_one(".s-item__shipping, .s-item__freeXDays")
    shipping = ""
    if shipping_el:
        shipping = shipping_el.get_text(strip=True)

    return {
        "id": item_id,
        "title": title,
        "price": price,
        "url": url,
        "image_url": image_url,
        "shipping": shipping,
    }


def _extract_item_id(url: str) -> str:
    """Extract eBay item ID from URL like /itm/123456789."""
    match = re.search(r"/itm/(\d+)", url)
    return match.group(1) if match else ""


def _hash_listing(title: str, price: float) -> str:
    """Generate a stable ID for listings without a parseable URL."""
    content = f"{title.lower().strip()}|{price}"
    return hashlib.md5(content.encode()).hexdigest()[:16]


def _parse_price(text: str) -> float | None:
    """Extract a numeric price from text like '$149.99'."""
    match = re.search(r"\$[\d,]+(?:\.\d{2})?", text)
    if match:
        price_str = match.group().replace("$", "").replace(",", "")
        try:
            return float(price_str)
        except ValueError:
            return None
    return None
