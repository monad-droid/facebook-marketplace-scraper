#!/usr/bin/env python3
"""
Facebook cookie setup for the marketplace scraper.

Three ways to provide cookies:

  1. CLI args (fastest, scriptable):
     python login.py --c-user 100012345678 --xs "44:abcdef:2:..."

  2. Interactive prompt (guided):
     python login.py

  3. Browser login via Playwright (easiest if you have VNC/noVNC):
     python login.py --browser

Cookies are auto-refreshed on every successful scraper run, so you
typically only need to do this once — then re-run only if you go
weeks without scraping.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time

COOKIES_FILE = "fb_cookies.json"


def _build_cookies(c_user: str, xs: str, datr: str = "") -> list[dict]:
    """Build Playwright-format cookie list from raw values."""
    cookies = [
        {
            "name": "c_user",
            "value": c_user,
            "domain": ".facebook.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "None",
        },
        {
            "name": "xs",
            "value": xs,
            "domain": ".facebook.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "None",
        },
    ]
    if datr:
        cookies.append({
            "name": "datr",
            "value": datr,
            "domain": ".facebook.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "None",
        })
    return cookies


def _save_cookies(cookies: list[dict]) -> None:
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    print(f"\n  Saved {len(cookies)} cookies to {COOKIES_FILE}")
    print("  You can now run: python main.py --verbose\n")


def _interactive_setup() -> None:
    """Original interactive prompt for pasting cookie values from DevTools."""
    print()
    print("=" * 60)
    print("  FACEBOOK COOKIE SETUP")
    print("=" * 60)
    print()
    print("  We need 2 cookies from your Facebook session.")
    print()
    print("  STEPS:")
    print()
    print("  1. Open Facebook in your browser and log in")
    print()
    print("  2. Open DevTools (F12 or right-click > Inspect)")
    print()
    print("  3. Go to Application tab (Chrome) or Storage tab (Firefox)")
    print()
    print("  4. Click Cookies > https://www.facebook.com")
    print()
    print("  5. Find and copy these cookie values:")
    print("     - c_user  (your numeric user ID)")
    print("     - xs      (session token)")
    print()
    print("  TIP: Run with --c-user and --xs flags to skip this prompt:")
    print('    python login.py --c-user 100012345 --xs "44:abc:2:..."')
    print()
    print("=" * 60)
    print()

    c_user = input("  Paste your 'c_user' cookie value: ").strip()
    if not c_user:
        print("  ERROR: c_user cannot be empty")
        return

    xs = input("  Paste your 'xs' cookie value: ").strip()
    if not xs:
        print("  ERROR: xs cannot be empty")
        return

    print()
    datr = input("  Paste 'datr' cookie (optional, Enter to skip): ").strip()

    cookies = _build_cookies(c_user, xs, datr)
    _save_cookies(cookies)


def _ensure_display():
    """Start Xvfb if no display is available (for --browser mode on servers)."""
    if os.environ.get("DISPLAY"):
        return

    if not shutil.which("Xvfb"):
        print("  Installing xvfb for virtual display...")
        subprocess.run(
            ["apt-get", "install", "-y", "xvfb"],
            capture_output=True, timeout=60,
        )

    if not shutil.which("Xvfb"):
        print("  ERROR: Xvfb not available. Cannot use --browser mode.")
        sys.exit(1)

    for display_num in range(99, 110):
        display = f":{display_num}"
        try:
            proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1920x1080x24", "-ac"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
            if proc.poll() is None:
                os.environ["DISPLAY"] = display
                print(f"  Started virtual display on {display}")
                return
        except Exception:
            continue

    print("  ERROR: Failed to start Xvfb.")
    sys.exit(1)


async def _browser_login() -> None:
    """
    Open a real Playwright browser to facebook.com/login and wait for
    the user to log in. Captures all cookies automatically.

    On a headless server, use VNC/noVNC to interact with the browser.
    The script prints the DISPLAY and waits for you.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  ERROR: playwright is not installed. Run: pip install playwright")
        sys.exit(1)

    _ensure_display()

    print()
    print("=" * 60)
    print("  BROWSER LOGIN MODE")
    print("=" * 60)
    print()
    print(f"  Opening Chromium on DISPLAY={os.environ.get('DISPLAY', ':0')}")
    print()
    print("  If you're on a remote server, connect via VNC/noVNC to see")
    print("  the browser window and complete the Facebook login.")
    print()
    print("  The script will detect when you're logged in and save cookies.")
    print("=" * 60)
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")

        print("  Waiting for you to log in...")
        print("  (will auto-detect when login succeeds)\n")

        # Poll until the login form disappears and c_user cookie appears
        for attempt in range(180):  # 3 minute timeout
            await asyncio.sleep(1)
            cookies = await context.cookies(["https://www.facebook.com"])
            cookie_names = {c["name"] for c in cookies}
            if "c_user" in cookie_names and "xs" in cookie_names:
                print("  Login detected!")
                # Give Facebook a moment to finish setting all cookies
                await asyncio.sleep(2)
                cookies = await context.cookies(["https://www.facebook.com"])
                break
        else:
            print("  ERROR: Login timed out after 3 minutes.")
            await browser.close()
            return

        # Save the cookies
        fb_cookies = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "secure": c.get("secure", True),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": c.get("sameSite", "None"),
            }
            for c in cookies
            if c.get("domain", "").endswith("facebook.com")
            and c.get("name") in ("c_user", "xs", "datr", "fr", "sb", "wd")
        ]

        await browser.close()

    if fb_cookies:
        _save_cookies(fb_cookies)
    else:
        print("  ERROR: No Facebook cookies captured.")


def main():
    parser = argparse.ArgumentParser(
        description="Set up Facebook cookies for the marketplace scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python login.py
      Interactive prompt — paste cookies from DevTools

  python login.py --c-user 100012345678 --xs "44:abcdef:2:..."
      Provide cookies directly (scriptable)

  python login.py --browser
      Open a Playwright browser and log in visually (VNC required on servers)

Note: Cookies are auto-refreshed on every scraper run, so you only
need to re-login if you go weeks without running the scraper.
        """,
    )
    parser.add_argument("--c-user", help="Facebook c_user cookie value")
    parser.add_argument("--xs", help="Facebook xs cookie value")
    parser.add_argument("--datr", default="", help="Facebook datr cookie (optional)")
    parser.add_argument(
        "--browser", action="store_true",
        help="Open a Playwright browser to log in visually",
    )
    # Accept --remote as alias for --browser (referenced in main.py docstring)
    parser.add_argument("--remote", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.browser or args.remote:
        asyncio.run(_browser_login())
    elif args.c_user and args.xs:
        cookies = _build_cookies(args.c_user, args.xs, args.datr)
        _save_cookies(cookies)
    elif args.c_user or args.xs:
        print("  ERROR: Both --c-user and --xs are required together.")
        sys.exit(1)
    else:
        _interactive_setup()


if __name__ == "__main__":
    main()
