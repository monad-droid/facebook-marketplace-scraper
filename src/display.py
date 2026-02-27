"""
Rich terminal display for arbitrage deals.
"""

import csv
import json
import os
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.models import ArbitrageDeal

console = Console()


def display_deals(deals: list[ArbitrageDeal]) -> None:
    """Display arbitrage deals in a formatted table."""
    if not deals:
        console.print("\n[yellow]No profitable deals found matching your criteria.[/yellow]\n")
        return

    console.print(f"\n[bold green]Found {len(deals)} Arbitrage Deals![/bold green]\n")

    table = Table(
        title="Marketplace Arbitrage Opportunities",
        show_header=True,
        header_style="bold cyan",
    )

    table.add_column("#", style="dim", width=4)
    table.add_column("Item", max_width=40)
    table.add_column("FB Price", justify="right", style="green")
    table.add_column("eBay Min", justify="right", style="blue")
    table.add_column("eBay Avg", justify="right", style="blue")
    table.add_column("Fees", justify="right", style="red")
    table.add_column("Shipping", justify="right", style="red")
    table.add_column("Net Profit", justify="right", style="bold green")
    table.add_column("ROI %", justify="right", style="bold yellow")
    table.add_column("Sold", justify="right")

    for i, deal in enumerate(deals, 1):
        table.add_row(
            str(i),
            deal.marketplace_listing.title[:40],
            f"${deal.marketplace_listing.price:,.2f}",
            f"${deal.min_ebay_price:,.2f}",
            f"${deal.avg_ebay_price:,.2f}",
            f"${deal.ebay_fees:,.2f}",
            f"${deal.shipping_cost:,.2f}",
            f"${deal.estimated_profit:,.2f}",
            f"{deal.profit_percent:.0f}%",
            str(deal.num_sold),
        )

    console.print(table)


def display_deal_detail(deal: ArbitrageDeal, index: int) -> None:
    """Display detailed breakdown for a single deal."""
    listing = deal.marketplace_listing

    detail = f"""[bold]{listing.title}[/bold]

[cyan]Facebook Marketplace:[/cyan]
  Price:    ${listing.price:,.2f}
  Location: {listing.location}
  URL:      {listing.url}

[cyan]eBay Sold Data (last 7 days):[/cyan]
  Items Sold:  {deal.num_sold}
  Min Price:   ${deal.min_ebay_price:,.2f}
  Avg Price:   ${deal.avg_ebay_price:,.2f}
  Max Price:   ${deal.max_ebay_price:,.2f}

[cyan]Profit Breakdown:[/cyan]
  Buy Price:         ${listing.price:,.2f}
  Sell Price (min):  ${deal.min_ebay_price:,.2f}
  eBay Fees:        -${deal.ebay_fees:,.2f}
  Shipping:         -${deal.shipping_cost:,.2f}
  [bold green]Net Profit:      ${deal.estimated_profit:,.2f}[/bold green]
  [bold yellow]ROI:             {deal.profit_percent:.0f}%[/bold yellow]"""

    console.print(Panel(detail, title=f"Deal #{index}", border_style="green"))


def save_deals_csv(deals: list[ArbitrageDeal], output_dir: str = "results") -> str:
    """Save deals to a CSV file."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"deals_{timestamp}.csv")

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Title", "FB Price", "FB URL", "FB Location",
            "eBay Min Price", "eBay Avg Price", "eBay Max Price",
            "Num Sold", "eBay Fees", "Shipping",
            "Net Profit", "ROI %",
        ])

        for deal in deals:
            writer.writerow([
                deal.marketplace_listing.title,
                f"{deal.marketplace_listing.price:.2f}",
                deal.marketplace_listing.url,
                deal.marketplace_listing.location,
                f"{deal.min_ebay_price:.2f}",
                f"{deal.avg_ebay_price:.2f}",
                f"{deal.max_ebay_price:.2f}",
                deal.num_sold,
                f"{deal.ebay_fees:.2f}",
                f"{deal.shipping_cost:.2f}",
                f"{deal.estimated_profit:.2f}",
                f"{deal.profit_percent:.1f}",
            ])

    return filepath


def save_deals_json(deals: list[ArbitrageDeal], output_dir: str = "results") -> str:
    """Save deals to a JSON file."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"deals_{timestamp}.json")

    data = []
    for deal in deals:
        data.append({
            "title": deal.marketplace_listing.title,
            "fb_price": deal.marketplace_listing.price,
            "fb_url": deal.marketplace_listing.url,
            "fb_location": deal.marketplace_listing.location,
            "ebay_min_price": deal.min_ebay_price,
            "ebay_avg_price": deal.avg_ebay_price,
            "ebay_max_price": deal.max_ebay_price,
            "num_sold": deal.num_sold,
            "ebay_fees": deal.ebay_fees,
            "shipping_cost": deal.shipping_cost,
            "net_profit": deal.estimated_profit,
            "roi_percent": deal.profit_percent,
        })

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    return filepath
