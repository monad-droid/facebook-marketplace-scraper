#!/usr/bin/env python3
"""
Diagnostic script to discover Facebook's actual data format.
Captures GraphQL responses and JSON script tags, then searches
for listing-related data patterns.
"""

import asyncio
import json
import os
import re
import random
import sys

from playwright.async_api import async_playwright

COOKIES_FILE = "fb_cookies.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def find_keys_recursive(data, target_keys=None, depth=0, path="", max_depth=15):
    """Find all unique keys in nested JSON and flag interesting values."""
    results = []

    if depth > max_depth:
        return results

    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key

            # Check if value looks like a price
            if isinstance(value, str) and re.match(r"^\$[\d,]+", value):
                results.append(f"  PRICE: {current_path} = {value}")

            # Check if value looks like a listing title (string with product-like words)
            if isinstance(value, str) and len(value) > 10 and len(value) < 200:
                lower = value.lower()
                if any(w in lower for w in ["kitchenaid", "mixer", "price", "listing"]):
                    results.append(f"  MATCH: {current_path} = {value[:100]}")

            # Check for marketplace-related keys
            if isinstance(key, str):
                lower_key = key.lower()
                if any(w in lower_key for w in [
                    "price", "title", "listing", "item", "product",
                    "amount", "cost", "name", "location", "city",
                    "photo", "image", "uri", "seller", "marketplace"
                ]):
                    val_preview = str(value)[:150] if not isinstance(value, (dict, list)) else f"<{type(value).__name__}>"
                    results.append(f"  KEY: {current_path} = {val_preview}")

            results.extend(find_keys_recursive(value, target_keys, depth + 1, current_path, max_depth))

    elif isinstance(data, list):
        for i, item in enumerate(data[:5]):  # Only check first 5 items of arrays
            results.extend(find_keys_recursive(item, target_keys, depth + 1, f"{path}[{i}]", max_depth))

    return results


async def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "kitchenaid mixer"
    url = f"https://www.facebook.com/marketplace/search/?query={query}&maxPrice=1000&sortBy=creation_time_descend&exact=false"

    print(f"\n=== Facebook Data Format Discovery ===")
    print(f"Query: {query}")
    print(f"URL: {url}\n")

    # Find chromium
    chromium_path = None
    for candidate in [
        "/root/.cache/ms-playwright/chromium-1194/chrome-linux/chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]:
        if os.path.exists(candidate):
            chromium_path = candidate
            break

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        # Load cookies
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)

        page = await context.new_page()

        # Verify login
        await page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)
        login_form = await page.query_selector('input[name="email"]')
        if login_form:
            print("ERROR: Not logged in. Run login.py first.")
            await browser.close()
            return

        print("Login verified.\n")

        # Capture ALL network responses (not just GraphQL)
        all_responses = []
        graphql_responses = []

        async def handle_response(response):
            req_url = response.url
            try:
                if response.status == 200:
                    content_type = response.headers.get("content-type", "")
                    if "json" in content_type or "javascript" in content_type or "/graphql" in req_url or "/api/" in req_url:
                        text = await response.text()
                        short_url = req_url[:100]
                        all_responses.append({
                            "url": short_url,
                            "size": len(text),
                            "has_price": "$" in text or "amount" in text.lower(),
                            "has_marketplace": "marketplace" in text.lower(),
                        })
                        if "/graphql" in req_url or "/api/" in req_url:
                            # Save full response for analysis
                            for line in text.split("\n"):
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    data = json.loads(line)
                                    graphql_responses.append(data)
                                except json.JSONDecodeError:
                                    pass
            except Exception:
                pass

        page.on("response", handle_response)

        # Navigate
        print(f"Navigating to: {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        await asyncio.sleep(5)

        # Scroll a bit
        for i in range(3):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await asyncio.sleep(2)

        print(f"\n=== Network Summary ===")
        print(f"Total API/JSON responses captured: {len(all_responses)}")
        print(f"GraphQL responses captured: {len(graphql_responses)}")

        marketplace_responses = [r for r in all_responses if r["has_marketplace"]]
        print(f"Responses with 'marketplace': {len(marketplace_responses)}")
        for r in marketplace_responses[:10]:
            print(f"  {r['url'][:80]}... ({r['size']} bytes, has_price={r['has_price']})")

        # Analyze GraphQL responses
        print(f"\n=== GraphQL Response Analysis ===")
        os.makedirs("debug", exist_ok=True)

        if graphql_responses:
            # Save all for manual inspection
            with open("debug/graphql_responses.json", "w") as f:
                json.dump(graphql_responses, f, indent=2, default=str)
            print(f"Saved {len(graphql_responses)} GraphQL responses to debug/graphql_responses.json")

            # Search for interesting keys
            print("\n--- Interesting keys found in GraphQL data ---")
            for i, resp in enumerate(graphql_responses):
                keys = find_keys_recursive(resp, max_depth=12)
                if keys:
                    print(f"\nResponse {i} ({json.dumps(resp, default=str)[:80]}...):")
                    for k in keys[:30]:
                        print(k)

        # Also check script tags
        print(f"\n=== Script Tag Analysis ===")
        script_count = await page.evaluate("""
            () => {
                const scripts = document.querySelectorAll('script[type="application/json"]');
                let marketplace_scripts = 0;
                let results = [];
                for (const script of scripts) {
                    const text = script.textContent || '';
                    if (text.toLowerCase().includes('marketplace') || text.includes('$')) {
                        marketplace_scripts++;
                        // Get a preview
                        results.push(text.substring(0, 200));
                    }
                }
                return {total: scripts.length, marketplace: marketplace_scripts, previews: results.slice(0, 5)};
            }
        """)
        print(f"Total JSON scripts: {script_count['total']}")
        print(f"Scripts mentioning marketplace or $: {script_count['marketplace']}")
        for i, preview in enumerate(script_count.get('previews', [])):
            print(f"  Preview {i}: {preview[:150]}...")

        # Dump ALL script tags with marketplace data
        all_marketplace_scripts = await page.evaluate("""
            () => {
                const scripts = document.querySelectorAll('script[type="application/json"]');
                let results = [];
                for (const script of scripts) {
                    const text = script.textContent || '';
                    if (text.toLowerCase().includes('marketplace')) {
                        try {
                            results.push(JSON.parse(text));
                        } catch(e) {
                            results.push(text.substring(0, 500));
                        }
                    }
                }
                return results;
            }
        """)
        if all_marketplace_scripts:
            with open("debug/marketplace_scripts.json", "w") as f:
                json.dump(all_marketplace_scripts, f, indent=2, default=str)
            print(f"\nSaved {len(all_marketplace_scripts)} marketplace script tags to debug/marketplace_scripts.json")

            print("\n--- Interesting keys in script tag data ---")
            for i, data in enumerate(all_marketplace_scripts[:5]):
                if isinstance(data, dict):
                    keys = find_keys_recursive(data, max_depth=12)
                    if keys:
                        print(f"\nScript {i}:")
                        for k in keys[:20]:
                            print(k)

        # Final: check page source for any price-like patterns
        print(f"\n=== Page Source Price Search ===")
        price_patterns = await page.evaluate("""
            () => {
                const html = document.documentElement.innerHTML;
                // Find all $XXX patterns
                const matches = html.match(/\\$\\d[\\d,]*(?:\\.\\d{2})?/g) || [];
                const unique = [...new Set(matches)];
                return unique.slice(0, 20);
            }
        """)
        print(f"Price patterns found in page source: {price_patterns}")

        await browser.close()
        print("\nDone! Check the debug/ folder for detailed data.")


if __name__ == "__main__":
    asyncio.run(main())
