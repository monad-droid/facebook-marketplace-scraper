from dataclasses import dataclass, field


@dataclass
class MarketplaceListing:
    title: str
    price: float
    url: str
    location: str = ""
    image_url: str = ""
    seller: str = ""
    description: str = ""


@dataclass
class EbaySoldItem:
    title: str
    sold_price: float
    sold_date: str
    shipping_cost: float = 0.0
    url: str = ""


@dataclass
class ArbitrageDeal:
    marketplace_listing: MarketplaceListing
    ebay_sold_items: list = field(default_factory=list)
    avg_ebay_price: float = 0.0
    min_ebay_price: float = 0.0
    max_ebay_price: float = 0.0
    estimated_profit: float = 0.0
    profit_percent: float = 0.0
    ebay_fees: float = 0.0
    shipping_cost: float = 0.0
    num_sold: int = 0
