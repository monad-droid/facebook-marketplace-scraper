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

        # Rate limit eBay requests
        time.sleep(random.uniform(1, 3))

    # Sort by profit percentage, best deals first
    deals.sort(key=lambda d: d.profit_percent, reverse=True)

    logger.info(f"Found {len(deals)} arbitrage deals out of {total} listings")
    return deals


def _evaluate_listing(
    listing: MarketplaceListing,
    min_profit_percent: float,
    min_sold_count: int,
    use_conservative_pricing: bool,
) -> ArbitrageDeal | None:
    """Evaluate a single listing for arbitrage potential."""
    # Search eBay for sold comparables
    market_data = get_ebay_market_value(listing.title)

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
