"""
Profit calculator accounting for all eBay selling costs.

Calculates net profit after:
- eBay final value fee (13.25%)
- eBay per-order fee ($0.30)
- Payment processing fee (2.9%)
- Estimated shipping cost
"""

from src.config import Config


def calculate_net_profit(
    buy_price: float,
    sell_price: float,
    shipping_cost: float = None,
) -> dict:
    """
    Calculate net profit after all fees and costs.

    Args:
        buy_price: Price to buy the item on Facebook Marketplace.
        sell_price: Expected sale price on eBay (based on sold comps).
        shipping_cost: Estimated shipping cost (defaults to config value).

    Returns:
        Dict with profit breakdown: gross, fees, shipping, net_profit, profit_percent.
    """
    if shipping_cost is None:
        shipping_cost = Config.ESTIMATED_SHIPPING_COST

    # eBay final value fee — percentage of total sale amount
    ebay_fvf = sell_price * Config.EBAY_FINAL_VALUE_FEE

    # eBay per-order fee
    ebay_order_fee = Config.EBAY_PER_ORDER_FEE

    # Payment processing fee (on total amount buyer pays)
    payment_fee = sell_price * Config.PAYMENT_PROCESSING_FEE

    # Total fees
    total_fees = ebay_fvf + ebay_order_fee + payment_fee

    # Net profit = sale price - purchase price - all fees - shipping
    net_profit = sell_price - buy_price - total_fees - shipping_cost

    # Profit percentage relative to investment (buy price)
    profit_percent = (net_profit / buy_price * 100) if buy_price > 0 else 0

    return {
        "buy_price": buy_price,
        "sell_price": sell_price,
        "ebay_final_value_fee": round(ebay_fvf, 2),
        "ebay_order_fee": ebay_order_fee,
        "payment_processing_fee": round(payment_fee, 2),
        "total_fees": round(total_fees, 2),
        "shipping_cost": shipping_cost,
        "total_cost": round(buy_price + total_fees + shipping_cost, 2),
        "net_profit": round(net_profit, 2),
        "profit_percent": round(profit_percent, 1),
    }


def meets_profit_threshold(
    buy_price: float,
    sell_price: float,
    min_profit_percent: float = None,
    shipping_cost: float = None,
) -> tuple[bool, dict]:
    """
    Check if a deal meets the minimum profit threshold.

    Args:
        buy_price: Marketplace purchase price.
        sell_price: Expected eBay sale price.
        min_profit_percent: Minimum profit % required (defaults to config).
        shipping_cost: Estimated shipping cost.

    Returns:
        Tuple of (meets_threshold: bool, profit_breakdown: dict).
    """
    if min_profit_percent is None:
        min_profit_percent = Config.MIN_PROFIT_PERCENT

    breakdown = calculate_net_profit(buy_price, sell_price, shipping_cost)
    meets = breakdown["profit_percent"] >= min_profit_percent

    return meets, breakdown
