#!/usr/bin/env python3
"""
One-time Facebook login script.

Opens a visible browser window so you can log into Facebook manually.
After you log in, your session cookies are saved to fb_cookies.json.
The main scraper uses these cookies so it doesn't need your password.

Usage:
    python login.py

    Then log into Facebook in the browser window that opens.
    Once you're on the Facebook homepage, press Enter in the terminal.

Note: If running on a headless server, use --remote flag to set up
      port forwarding and access the browser from your local machine.
"""

import asyncio
import json
import argparse

from playwright.async_api import async_playwright

COOKIES_FILE = "fb_cookies.json"


async def login_interactive() -> None:
    """Open a browser for manual Facebook login, then save cookies."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()

        await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")

        print("\n" + "=" * 60)
        print("  FACEBOOK LOGIN")
        print("=" * 60)
        print()
        print("  A browser window has opened.")
        print("  1. Log into your Facebook account")
        print("  2. Wait until you see the Facebook homepage/news feed")
        print("  3. Come back here and press ENTER")
        print()
        print("=" * 60)

        input("\n  Press ENTER after you've logged in... ")

        # Save cookies
        cookies = await context.cookies()
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f, indent=2)

        print(f"\n  Saved {len(cookies)} cookies to {COOKIES_FILE}")
        print("  You can now run: python main.py")
        print()

        await browser.close()


async def login_remote(port: int) -> None:
    """Launch browser on server with remote debugging for headless servers."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=0.0.0.0",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()

        await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")

        print("\n" + "=" * 60)
        print("  REMOTE FACEBOOK LOGIN")
        print("=" * 60)
        print()
        print(f"  Browser is running with remote debugging on port {port}")
        print()
        print("  From your LOCAL machine, run:")
        print(f"    ssh -L {port}:localhost:{port} root@YOUR_SERVER_IP")
        print()
        print(f"  Then open Chrome on your local machine and go to:")
        print(f"    chrome://inspect/#devices")
        print()
        print("  Or use the direct URL approach:")
        print(f"    1. Open http://localhost:{port}/json in your local browser")
        print(f"    2. Find the 'devtoolsFrontendUrl' and open it")
        print(f"    3. Log into Facebook in the remote browser")
        print()
        print("  After logging in, come back here and press ENTER")
        print("=" * 60)

        input("\n  Press ENTER after you've logged in... ")

        cookies = await context.cookies()
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f, indent=2)

        print(f"\n  Saved {len(cookies)} cookies to {COOKIES_FILE}")
        print("  You can now run: python main.py")
        print()

        await browser.close()


def main():
    parser = argparse.ArgumentParser(description="Log into Facebook and save cookies")
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Use remote debugging mode (for headless servers)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9222,
        help="Remote debugging port (default: 9222)",
    )
    args = parser.parse_args()

    if args.remote:
        asyncio.run(login_remote(args.port))
    else:
        asyncio.run(login_interactive())


if __name__ == "__main__":
    main()
