import sys
import os
import asyncio
import pandas as pd
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

CONCURRENCY = 10
TIMEOUT = 8000


async def check_sitemap(context, row, index, total, semaphore):
    async with semaphore:
        raw_url = str(row["website_link"]).strip()

        if not raw_url.startswith(("http://", "https://")):
            clean_url = f"https://{raw_url}"
        else:
            clean_url = raw_url

        sitemap_url = clean_url.rstrip("/") + "/sitemap.xml"
        company_name = row.get("name", "Unknown Company")

        print(f"[{index + 1}/{total}] {company_name}: checking...", end=" ")

        page = await context.new_page()

        stealth = Stealth()
        await stealth.apply_stealth_async(page)

        try:
            response = await page.goto(
                sitemap_url,
                timeout=TIMEOUT,
                wait_until="commit",
            )

            if response and response.status == 200:
                final_url = page.url.rstrip("/")

                # Consider valid if it ends with .xml (could be /sitemap_index.xml)
                if final_url.lower().endswith(".xml"):
                    print(f"✅ FOUND ({final_url})")
                    result = row.to_dict()
                    result["sitemap_url"] = final_url
                    return ("has", result)
                else:
                    print(f"❌ REDIRECTED TO NON-SITEMAP ({final_url})")
                    result = row.to_dict()
                    result["status_code"] = response.status
                    result["redirected_to"] = final_url
                    return ("no", result)

            elif response:
                print(f"❌ MISSING ({response.status})")
                result = row.to_dict()
                result["status_code"] = response.status
                return ("no", result)

            else:
                print("⚠️ NO RESPONSE")
                result = row.to_dict()
                result["error"] = "no_response"
                return ("error", result)

        except Exception as e:
            print("⚠️ ERROR")
            result = row.to_dict()
            result["error"] = str(e)
            return ("error", result)

        finally:
            await page.close()


async def run_scanner():
    if len(sys.argv) < 2:
        print("Usage: python main.py <path_to_csv>")
        return

    input_path = sys.argv[1]

    if not os.path.exists(input_path):
        print(f"Error: File '{input_path}' not found.")
        return

    df = pd.read_csv(input_path)

    if "website_link" not in df.columns:
        print("Missing 'website_link' column.")
        return

    has_sitemap = []
    no_sitemap = []
    error_sitemap = []

    print(f"Starting scan for {len(df)} companies...\n")

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="en-US",
        )

        # optional speed boost (highly recommended)
        await context.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in ["image", "stylesheet", "font"]
                else route.continue_()
            ),
        )

        tasks = [
            check_sitemap(context, row, i, len(df), semaphore)
            for i, row in df.iterrows()
        ]

        results = await asyncio.gather(*tasks)

        for result_type, data in results:
            if result_type == "has":
                has_sitemap.append(data)
            elif result_type == "no":
                no_sitemap.append(data)
            else:
                error_sitemap.append(data)

        await browser.close()

    pd.DataFrame(has_sitemap).to_csv("companies_with_sitemap.csv", index=False)
    pd.DataFrame(no_sitemap).to_csv("companies_no_sitemap.csv", index=False)
    pd.DataFrame(error_sitemap).to_csv("companies_error.csv", index=False)

    print("-" * 30)
    print("Scan complete!")
    print(f"With Sitemap: {len(has_sitemap)}")
    print(f"Without Sitemap: {len(no_sitemap)}")
    print(f"Errors: {len(error_sitemap)}")


if __name__ == "__main__":
    asyncio.run(run_scanner())
