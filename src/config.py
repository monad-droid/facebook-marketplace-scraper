import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Facebook Marketplace
    FB_LOCATION = os.getenv("FB_LOCATION", "new york")
    FB_RADIUS_MILES = int(os.getenv("FB_RADIUS_MILES", "30"))
    FB_MAX_PRICE = int(os.getenv("FB_MAX_PRICE", "1000"))

    # eBay
    EBAY_SOLD_DAYS = int(os.getenv("EBAY_SOLD_DAYS", "7"))

    # Profit thresholds
    MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "100"))
    ESTIMATED_SHIPPING_COST = float(os.getenv("ESTIMATED_SHIPPING_COST", "15.00"))

    # eBay fee structure (as of 2024)
    EBAY_FINAL_VALUE_FEE = 0.1325  # 13.25% for most categories
    EBAY_PER_ORDER_FEE = 0.30  # $0.30 per order
    PAYMENT_PROCESSING_FEE = 0.029  # 2.9% payment processing

    # Browser
    HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

    # User agent rotation
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]
