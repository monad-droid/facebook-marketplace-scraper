#!/usr/bin/env python3
"""
One-time Facebook login script.

Opens a browser with remote debugging so you can log into Facebook
from your local machine via SSH tunnel. Saves cookies for the scraper.

Usage (on your server):
    python login.py

Then from your local machine:
    ssh -L 9222:localhost:9222 root@YOUR_SERVER_IP

Then open http://localhost:9222 in your local browser to control the
remote Chrome, log into Facebook, and press Enter in the server terminal.
"""

import asyncio
import json
import subprocess
import shutil
import sys
import argparse

from playwright.async_api import async_playwright

COOKIES_FILE = "fb_cookies.json"


def _ensure_xvfb():
    """Install xvfb if not present."""
    if shutil.which("Xvfb") or shutil.which("xvfb-run"):
        return True
    print("  Installing xvfb (virtual display for headless server)...")
    result = subprocess.run(
        ["apt", "install", "-y", "xvfb"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  Failed to install xvfb: {result.stderr}")
        print("  Try manually: sudo apt install -y xvfb")
        return False
    return True


async def login_remote(port: int) -> None:
    """Launch browser with xvfb + remote debugging for headless servers."""
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
        print(f"  Browser is running on port {port}.")
        print()
        print("  FROM YOUR LOCAL MACHINE, open a new terminal and run:")
        print(f"    ssh -L {port}:localhost:{port} root@YOUR_SERVER_IP")
        print()
        print(f"  Then open your local browser and go to:")
        print(f"    http://localhost:{port}")
        print()
        print("  You'll see a list of open tabs. Click the Facebook")
        print("  login page to control the remote browser.")
        print("  Log into your Facebook account.")
        print()
        print("  After logging in, come back here and press ENTER.")
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
        "--port",
        type=int,
        default=9222,
        help="Remote debugging port (default: 9222)",
    )
    args = parser.parse_args()

    # Headless server needs a virtual display
    if not _ensure_xvfb():
        sys.exit(1)

    # Re-exec under xvfb-run if DISPLAY is not set
    import os
    if "DISPLAY" not in os.environ:
        print("  Starting virtual display with xvfb-run...")
        xvfb_path = shutil.which("xvfb-run")
        if not xvfb_path:
            print("  ERROR: xvfb-run not found. Install with: apt install xvfb")
            sys.exit(1)
        os.execvp(xvfb_path, [
            xvfb_path,
            "--auto-servernum",
            "--server-args=-screen 0 1920x1080x24",
            sys.executable, *sys.argv,
        ])

    # If we get here, DISPLAY is set (either real or from xvfb-run)
    asyncio.run(login_remote(args.port))


if __name__ == "__main__":
    main()
