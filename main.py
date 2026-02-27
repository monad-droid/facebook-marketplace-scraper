#!/usr/bin/env python3
"""
Facebook Marketplace Arbitrage Finder

Finds Facebook Marketplace items selling for under $1000 that can be
resold on eBay for a guaranteed 100%+ profit after fees and shipping.

Only considers items that have actually sold on eBay in the past 7 days.

Usage:
    python login.py --remote       (first time only — log into Facebook)
    python main.py                 (search default categories)
    python main.py --queries "kitchenaid mixer" "le creuset"
    python main.py --queries "snap on tools" --max-price 500
"""

import argparse
import asyncio
import logging
import sys

from rich.console import Console

from src.config import Config
from src.facebook_scraper import scrape_marketplace
from src.arbitrage_finder import find_arbitrage_deals
from src.display import (
    display_deals,
    display_deal_detail,
    save_deals_csv,
    save_deals_json,
)

console = Console()

# High-margin categories with specific model numbers for accurate eBay matching
DEFAULT_QUERIES = [
    # Power tools — exact brands, high demand
    "DeWalt power tool",
    "Milwaukee M18",
    "Makita drill",
    "Ridgid tool",
    "Bosch power tool",
    "Snap On tools",
    # Camera bodies + lenses — structured market, model-specific
    "Canon EOS R6",
    "Sony A7III",
    "Sony A7 IV",
    "Sigma 35mm",
    "Canon RF lens",
    "Sony GM lens",
    # Game consoles — exact SKU matching
    "PS5 console",
    "Xbox Series X",
    "Nintendo Switch OLED",
    # Laptops — model numbers everywhere, 30-80% margins
    "MacBook Air M1",
    "MacBook Pro M2",
    "MacBook Pro M3",
    "ThinkPad T480",
    "ThinkPad X1 Carbon",
    # Networking / home office — niche, precise matching, good margins
    "Ubiquiti access point",
    "Synology NAS",
    "UniFi switch",
    # Audio equipment — model-specific, audiophiles pay up
    "Denon receiver",
    "Yamaha receiver",
    "Marantz receiver",
    "turntable",
    # Video games — retro titles flip for $30-$120 each
    "N64 games",
    "GameCube games",
    "Sega Genesis games",
    "PS2 games lot",
    # Small kitchen appliances — exact models, tight comps
    "KitchenAid Artisan",
    "Vitamix 5200",
    "Breville Barista Express",
    "Le Creuset",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find Facebook Marketplace items to flip on eBay for 100%+ profit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
      Search default high-value categories

  python main.py --queries "iphone 15" "ps5 console"
      Search specific items

  python main.py --max-price 500 --min-profit 200
      Items under $500 with 200%+ ROI

  python main.py --conservative
      Use minimum eBay sold price for calculations (safer)
        """,
    )

    parser.add_argument(
        "--queries", "-q",
        nargs="+",
        default=None,
        help="Search queries for Facebook Marketplace (default: popular categories)",
    )
    parser.add_argument(
        "--max-price",
        type=int,
        default=Config.FB_MAX_PRICE,
        help=f"Maximum item price on FB Marketplace (default: ${Config.FB_MAX_PRICE})",
    )
    parser.add_argument(
        "--min-profit",
        type=float,
        default=Config.MIN_PROFIT_PERCENT,
        help=f"Minimum profit %% after all fees (default: {Config.MIN_PROFIT_PERCENT}%%)",
    )
    parser.add_argument(
        "--min-sold",
        type=int,
        default=3,
        help="Minimum eBay sold items required for confidence (default: 3)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=50,
        help="Max items to scrape per search query (default: 50)",
    )
    parser.add_argument(
        "--conservative",
        action="store_true",
        default=True,
        help="Use minimum eBay sold price for profit calc (default: True)",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        default=False,
        help="Use average eBay sold price for profit calc (higher risk)",
    )
    parser.add_argument(
        "--output", "-o",
        choices=["csv", "json", "both"],
        default="both",
        help="Output format for saving deals (default: both)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Show detailed breakdown for each deal",
    )

    return parser.parse_args()


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    queries = args.queries or DEFAULT_QUERIES
    use_conservative = not args.aggressive

    console.print(
        f"\n[bold cyan]Facebook Marketplace Arbitrage Finder[/bold cyan]\n"
    )
    console.print(f"  Searching {len(queries)} queries, max price: ${args.max_price}")
    console.print(f"  Minimum profit: {args.min_profit}%")
    console.print(f"  Pricing mode: {'Conservative (min sold)' if use_conservative else 'Aggressive (avg sold)'}")
    console.print(f"  Min eBay sold count: {args.min_sold}")
    console.print()

    # Step 1: Scrape Facebook Marketplace
    console.print("[bold]Step 1:[/bold] Scraping Facebook Marketplace...\n")
    listings = asyncio.run(
        scrape_marketplace(
            queries=queries,
            max_price=args.max_price,
            max_items_per_query=args.max_items,
        )
    )

    if not listings:
        console.print("[red]No listings found. Check your search queries or try again later.[/red]")
        sys.exit(1)

    console.print(f"[green]Found {len(listings)} listings under ${args.max_price}[/green]\n")

    # Step 2: Cross-reference with eBay sold data
    console.print("[bold]Step 2:[/bold] Cross-referencing with eBay sold data...\n")
    deals = find_arbitrage_deals(
        listings=listings,
        min_profit_percent=args.min_profit,
        min_sold_count=args.min_sold,
        use_conservative_pricing=use_conservative,
    )

    # Step 3: Display results
    console.print("[bold]Step 3:[/bold] Results\n")
    display_deals(deals)

    if args.detail and deals:
        console.print("\n[bold]Detailed Breakdowns:[/bold]\n")
        for i, deal in enumerate(deals, 1):
            display_deal_detail(deal, i)

    # Step 4: Save results
    if deals:
        if args.output in ("csv", "both"):
            csv_path = save_deals_csv(deals)
            console.print(f"\n[dim]Saved CSV: {csv_path}[/dim]")

        if args.output in ("json", "both"):
            json_path = save_deals_json(deals)
            console.print(f"[dim]Saved JSON: {json_path}[/dim]")

    console.print()


if __name__ == "__main__":
    main()
