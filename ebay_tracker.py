#!/usr/bin/env python3
"""
eBay Listing Tracker with Telegram Notifications.

Monitors eBay for new listings matching your saved search terms and sends
Telegram alerts when new items appear. Manage searches via Telegram commands.

Usage:
    1. Create a Telegram bot via @BotFather, get the token
    2. Set TELEGRAM_BOT_TOKEN in .env
    3. Run: python ebay_tracker.py
    4. Message your bot with /start to register your chat
    5. Use /add Material Histories emily xie  to track a search
    6. Bot will notify you when new listings appear

Commands:
    /start          - Register and see help
    /add <query>    - Add a search term to track
    /remove <query> - Stop tracking a search term
    /list           - Show all active searches
    /check          - Force an immediate check of all searches
    /help           - Show help message
"""

import os
import asyncio
import logging
import signal
import html
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from src.ebay_listing_scraper import search_ebay_active
from src.tracker_db import (
    add_search,
    remove_search,
    get_all_searches,
    is_listing_seen,
    mark_listings_seen_bulk,
    cleanup_old_seen,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# How often to poll eBay (seconds)
CHECK_INTERVAL = int(os.getenv("EBAY_CHECK_INTERVAL", "300"))  # default 5 min

# Authorized chat IDs (set after /start or via env)
AUTHORIZED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _is_authorized(chat_id: int) -> bool:
    """Check if a chat is authorized. If no chat ID configured, allow anyone."""
    if not AUTHORIZED_CHAT_ID:
        return True
    return str(chat_id) == AUTHORIZED_CHAT_ID


# ── Telegram command handlers ───────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"eBay Listing Tracker is running!\n\n"
        f"Your chat ID: <code>{chat_id}</code>\n\n"
        f"<b>Commands:</b>\n"
        f"/add &lt;query&gt; — Track a search term\n"
        f"/remove &lt;query&gt; — Stop tracking\n"
        f"/list — Show all tracked searches\n"
        f"/check — Force check now\n"
        f"/help — Show this message\n\n"
        f"<b>Example:</b>\n"
        f"/add Material Histories emily xie",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_chat.id):
        await update.message.reply_text("Unauthorized.")
        return

    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /add <search term>\nExample: /add Material Histories emily xie")
        return

    if add_search(query):
        await update.message.reply_text(f"Now tracking: <b>{html.escape(query)}</b>", parse_mode="HTML")
        logger.info(f"Added search: {query}")
        # Do an immediate check for this new search
        await _check_single_search(query, update.effective_chat.id, context.application)
    else:
        await update.message.reply_text(f"Already tracking: <b>{html.escape(query)}</b>", parse_mode="HTML")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_chat.id):
        await update.message.reply_text("Unauthorized.")
        return

    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /remove <search term>")
        return

    if remove_search(query):
        await update.message.reply_text(f"Stopped tracking: <b>{html.escape(query)}</b>", parse_mode="HTML")
        logger.info(f"Removed search: {query}")
    else:
        await update.message.reply_text(f"Not found: <b>{html.escape(query)}</b>", parse_mode="HTML")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_chat.id):
        await update.message.reply_text("Unauthorized.")
        return

    searches = get_all_searches()
    if not searches:
        await update.message.reply_text("No active searches. Use /add to start tracking.")
        return

    lines = [f"<b>Tracked searches ({len(searches)}):</b>\n"]
    for i, q in enumerate(searches, 1):
        lines.append(f"{i}. {html.escape(q)}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update.effective_chat.id):
        await update.message.reply_text("Unauthorized.")
        return

    searches = get_all_searches()
    if not searches:
        await update.message.reply_text("No searches to check. Use /add first.")
        return

    await update.message.reply_text(f"Checking {len(searches)} search(es)...")
    total_new = await _check_all_searches(update.effective_chat.id, context.application)
    await update.message.reply_text(f"Done. Found {total_new} new listing(s).")


# ── Core tracking logic ─────────────────────────────────────────────────────


async def _check_single_search(query: str, chat_id: int, app) -> int:
    """Check one search term for new listings. Returns count of new listings."""
    try:
        # Run the blocking scraper in a thread to avoid blocking the event loop
        listings = await asyncio.to_thread(search_ebay_active, query)
    except Exception as e:
        logger.error(f"Error scraping eBay for '{query}': {e}")
        return 0

    new_listings = []
    for listing in listings:
        if not is_listing_seen(listing["id"], query):
            new_listings.append(listing)

    if new_listings:
        # Mark all as seen
        mark_listings_seen_bulk(new_listings, query)

        # Send notifications (max 10 per check to avoid spam)
        for listing in new_listings[:10]:
            msg = _format_listing_message(query, listing)
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                )
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")

        if len(new_listings) > 10:
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"... and {len(new_listings) - 10} more new listings for <b>{html.escape(query)}</b>",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Failed to send overflow message: {e}")

    logger.info(f"'{query}': {len(listings)} total, {len(new_listings)} new")
    return len(new_listings)


async def _check_all_searches(chat_id: int, app) -> int:
    """Check all saved searches for new listings."""
    searches = get_all_searches()
    total_new = 0

    for query in searches:
        new_count = await _check_single_search(query, chat_id, app)
        total_new += new_count
        # Small delay between searches to be polite to eBay
        await asyncio.sleep(3)

    return total_new


def _format_listing_message(query: str, listing: dict) -> str:
    """Format a listing into a Telegram notification message."""
    title = html.escape(listing.get("title", "Unknown"))
    price = listing.get("price", 0)
    url = listing.get("url", "")
    shipping = listing.get("shipping", "")

    lines = [
        f"<b>New eBay Listing</b>",
        f"Search: <i>{html.escape(query)}</i>\n",
        f"<b>{title}</b>",
        f"Price: <b>${price:,.2f}</b>",
    ]

    if shipping:
        lines.append(f"Shipping: {html.escape(shipping)}")

    if url:
        lines.append(f"\n<a href=\"{url}\">View on eBay</a>")

    return "\n".join(lines)


# ── Periodic polling job ─────────────────────────────────────────────────────


async def _periodic_check(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: runs on a timer to check all searches."""
    chat_id = AUTHORIZED_CHAT_ID or context.job.data
    if not chat_id:
        logger.warning("No chat ID configured — skipping periodic check")
        return

    chat_id = int(chat_id)
    total = await _check_all_searches(chat_id, context.application)
    logger.info(f"Periodic check complete: {total} new listing(s) found")

    # Clean up old seen listings once a day
    cleanup_old_seen(days=30)


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: Set TELEGRAM_BOT_TOKEN in your .env file")
        print("Get a token from @BotFather on Telegram")
        return

    app = Application.builder().token(token).build()

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("check", cmd_check))

    # Schedule periodic checks
    if AUTHORIZED_CHAT_ID:
        app.job_queue.run_repeating(
            _periodic_check,
            interval=CHECK_INTERVAL,
            first=10,  # First check 10s after startup
            data=AUTHORIZED_CHAT_ID,
        )
        logger.info(f"Periodic checks every {CHECK_INTERVAL}s for chat {AUTHORIZED_CHAT_ID}")
    else:
        logger.warning(
            "TELEGRAM_CHAT_ID not set — periodic checks disabled. "
            "Use /start to see your chat ID, then set it in .env"
        )

    logger.info("eBay Tracker bot starting...")
    print(f"Bot is running! Check interval: {CHECK_INTERVAL}s")
    print("Send /start to your bot on Telegram to get started.")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
