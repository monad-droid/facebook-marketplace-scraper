"""
Arbitrage finder — the core engine.

Takes Facebook Marketplace listings, cross-references each with eBay sold data,
and identifies deals where we can guarantee 100%+ profit after all fees/shipping.

Conservative approach:
- Uses the LOWEST eBay sold price (not average) for profit calculation
- Requires multiple sold items as proof of demand
- Accounts for all eBay fees and shipping costs
"""

import logging
import re
import time
import random

from src.models import MarketplaceListing, ArbitrageDeal
from src.ebay_checker import get_ebay_market_value
from src.profit_calculator import calculate_net_profit
from src.config import Config

logger = logging.getLogger(__name__)

MIN_EBAY_SOLD_COUNT = 3  # Require at least 3 sold items for confidence


def find_arbitrage_deals(
    listings: list[MarketplaceListing],
    min_profit_percent: float = None,
    min_sold_count: int = MIN_EBAY_SOLD_COUNT,
    use_conservative_pricing: bool = True,
) -> list[ArbitrageDeal]:
    """
    Find arbitrage opportunities from marketplace listings.

    For each listing, searches eBay sold data and calculates profit potential.
    Only returns deals meeting the minimum profit threshold.

    Args:
        listings: Facebook Marketplace listings to evaluate.
        min_profit_percent: Minimum profit % after all costs (default: 100%).
        min_sold_count: Minimum number of eBay sold items required.
        use_conservative_pricing: If True, use min eBay price instead of avg.

    Returns:
        List of ArbitrageDeal objects sorted by profit percentage (best first).
    """
    if min_profit_percent is None:
        min_profit_percent = Config.MIN_PROFIT_PERCENT

    deals: list[ArbitrageDeal] = []
    total = len(listings)

    for i, listing in enumerate(listings, 1):
        logger.info(
            f"[{i}/{total}] Checking: {listing.title[:60]}... (${listing.price})"
        )

        deal = _evaluate_listing(
            listing, min_profit_percent, min_sold_count, use_conservative_pricing
        )

        if deal is not None:
            deals.append(deal)
            logger.info(
                f"  -> DEAL FOUND! Profit: ${deal.estimated_profit:.2f} "
                f"({deal.profit_percent:.0f}%)"
            )
        else:
            logger.debug(f"  -> No profitable deal found")

        # Rate limit eBay requests — be polite to avoid CAPTCHAs
        time.sleep(random.uniform(3, 6))

    # Sort by profit percentage, best deals first
    deals.sort(key=lambda d: d.profit_percent, reverse=True)

    logger.info(f"Found {len(deals)} arbitrage deals out of {total} listings")
    return deals


def _simplify_query(title: str) -> str:
    """
    Convert a verbose Facebook Marketplace title into a concise eBay search query.

    FB titles are often like:
      "KitchenAid 7 Hand Mixer, Onyx Black, all the attachments!!! See video"
    We need to extract just the searchable product terms:
      "KitchenAid Hand Mixer Onyx Black"
    """
    # Take only the first line (FB listings often have multi-line descriptions)
    title = title.split("\n")[0].strip()

    # Remove common FB marketplace filler phrases
    filler_patterns = [
        r"\b(see (video|photos?|pics?|description|details))\b",
        r"\b(message me|text me|call me|contact me|dm me)\b",
        r"\b(pick\s*up|local|must\s*go|need\s*gone|moving\s*sale)\b",
        r"\b(firm|obo|or best offer|negotiable|make.?offer)\b",
        r"\b(brand new|like new|excellent|good) condition\b",
        r"\b(barely|never|gently|lightly) used\b",
        r"\b(all the|with all|includes all|comes with)\b",
        r"\b(great deal|amazing|perfect|awesome|beautiful)\b",
        r"\b(retail(s| price)?)\s*\$?\d+\b",
        r"\b(no (lowballers?|trades?))\b",
        r"\bNIB\b",
        r"\bNWT\b",
        r"\bEUC\b",
    ]
    for pattern in filler_patterns:
        title = re.sub(pattern, " ", title, flags=re.IGNORECASE)

    # Remove special characters but keep alphanumeric, dots, dashes
    title = re.sub(r"[!?*#@&()\"'\[\]{}|/\\]+", " ", title)

    # Remove standalone numbers that aren't part of a model number
    # Keep numbers attached to letters (like "K45" or "5qt")
    title = re.sub(r"(?<![a-zA-Z])\b\d{1,2}\b(?![a-zA-Z\.])", " ", title)

    # Collapse whitespace
    title = re.sub(r"\s+", " ", title).strip()

    # Limit to first ~8 meaningful words (eBay searches work best with 4-8 terms)
    words = title.split()
    if len(words) > 8:
        words = words[:8]

    query = " ".join(words)

    # Remove trailing comma/period
    query = query.rstrip(",.")

    return query


def _evaluate_listing(
    listing: MarketplaceListing,
    min_profit_percent: float,
    min_sold_count: int,
    use_conservative_pricing: bool,
) -> ArbitrageDeal | None:
    """Evaluate a single listing for arbitrage potential."""
    # Simplify the FB title into a concise eBay search query
    ebay_query = _simplify_query(listing.title)
    logger.debug(f"  eBay search query: '{ebay_query}'")

    # Search eBay for sold comparables
    market_data = get_ebay_market_value(ebay_query)

    if market_data["num_sold"] < min_sold_count:
        logger.debug(
            f"  Only {market_data['num_sold']} sold (need {min_sold_count})"
        )
        return None

    # Use conservative pricing — the minimum sold price gives us a
    # "guaranteed" baseline. Average is riskier.
    if use_conservative_pricing:
        reference_price = market_data["min_price"]
    else:
        reference_price = market_data["avg_price"]

    # Calculate profit
    profit_info = calculate_net_profit(
        buy_price=listing.price,
        sell_price=reference_price,
    )

    if profit_info["profit_percent"] < min_profit_percent:
        logger.debug(
            f"  Profit {profit_info['profit_percent']:.0f}% "
            f"< {min_profit_percent}% threshold"
        )
        return None

    return ArbitrageDeal(
        marketplace_listing=listing,
        ebay_sold_items=market_data["items"],
        avg_ebay_price=market_data["avg_price"],
        min_ebay_price=market_data["min_price"],
        max_ebay_price=market_data["max_price"],
        estimated_profit=profit_info["net_profit"],
        profit_percent=profit_info["profit_percent"],
        ebay_fees=profit_info["total_fees"],
        shipping_cost=profit_info["shipping_cost"],
        num_sold=market_data["num_sold"],
    )
