#!/usr/bin/env python3
"""
One-time Facebook cookie setup.

Grabs your Facebook session cookies from your local browser
and saves them for the scraper to use.

Usage:
    python login.py

    Follow the on-screen instructions to copy cookies from your browser.
"""

import json

COOKIES_FILE = "fb_cookies.json"


def main():
    print()
    print("=" * 60)
    print("  FACEBOOK COOKIE SETUP")
    print("=" * 60)
    print()
    print("  We need 2 cookies from your Facebook session.")
    print("  This is a one-time setup.")
    print()
    print("  STEPS:")
    print()
    print("  1. Open Facebook in your browser and make sure")
    print("     you're logged in")
    print()
    print("  2. Open DevTools (F12 or right-click > Inspect)")
    print()
    print("  3. Go to the 'Application' tab (Chrome/Brave)")
    print("     or 'Storage' tab (Firefox)")
    print()
    print("  4. In the left sidebar, click 'Cookies' then")
    print("     'https://www.facebook.com'")
    print()
    print("  5. Find these 2 cookies and copy their values:")
    print("     - c_user")
    print("     - xs")
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

    # Build cookie objects in Playwright format
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
        {
            "name": "datr",
            "value": "placeholder",
            "domain": ".facebook.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "None",
        },
    ]

    # Also ask for datr if they have it (optional but helps)
    print()
    datr = input("  Paste your 'datr' cookie value (optional, press Enter to skip): ").strip()
    if datr:
        cookies[2]["value"] = datr
    else:
        cookies.pop(2)  # Remove the placeholder

    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)

    print()
    print(f"  Saved cookies to {COOKIES_FILE}")
    print()
    print("  You can now run: python main.py --verbose")
    print()


if __name__ == "__main__":
    main()
