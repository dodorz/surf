"""Debug archive.is interaction flow — run this manually."""
import sys
sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time

URL = "https://www.wsj.com/tech/ai/ai-math-solves-erdos-problem-openai-c4029e84"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = ctx.new_page()
    page.set_default_timeout(30000)

    # Step 1: Open archive.is
    print("[1] Opening archive.is...")
    page.goto("https://archive.is/", wait_until="networkidle")
    page.wait_for_timeout(2000)

    # Show page title and URL
    print(f"    Title: {page.title()}")
    print(f"    URL: {page.url}")

    # Check CAPTCHA
    captcha = page.locator("iframe[src*='recaptcha'], iframe[src*='captcha'], div.g-recaptcha")
    captcha_count = captcha.count()
    print(f"    CAPTCHA elements: {captcha_count}")

    if captcha_count > 0:
        print("[!] CAPTCHA detected. Please solve it in the browser...")
        print("    Waiting up to 120 seconds...")
        for i in range(60):
            time.sleep(2)
            if captcha.count() == 0:
                print("[✓] CAPTCHA resolved!")
                break
            if i % 5 == 0:
                print(f"    Still waiting... ({i*2}s)")
        else:
            print("[✗] CAPTCHA timeout")
            browser.close()
            sys.exit(1)

    page.wait_for_timeout(1500)
    print(f"    After CAPTCHA — Title: {page.title()}")
    print(f"    After CAPTCHA — URL: {page.url}")

    # Step 2: Find search input
    print("\n[2] Looking for search form...")
    inputs = page.locator("input[type='text'], input[name='q'], input[name='url'], textarea[name='url']")
    print(f"    Found {inputs.count()} input elements")

    for i in range(min(inputs.count(), 5)):
        try:
            el = inputs.nth(i)
            print(f"    [{i}] tag={el.evaluate('el => el.tagName')} name={el.get_attribute('name')} type={el.get_attribute('type')} placeholder={el.get_attribute('placeholder')}")
        except Exception as e:
            print(f"    [{i}] error: {e}")

    # Also check for any textarea
    textareas = page.locator("textarea")
    for i in range(min(textareas.count(), 3)):
        try:
            ta = textareas.nth(i)
            print(f"    textarea[{i}] name={ta.get_attribute('name')} id={ta.get_attribute('id')}")
        except Exception as e:
            print(f"    textarea[{i}] error: {e}")

    # Step 3: Try the direct submit URL
    from urllib.parse import quote
    submit_url = f"https://archive.is/?run=1&url={quote(URL, safe='')}"
    print(f"\n[3] Trying submit URL: {submit_url}")
    try:
        resp = page.goto(submit_url, wait_until="networkidle", timeout=20000)
        print(f"    HTTP status: {resp.status if resp else 'None'}")
    except PlaywrightTimeoutError:
        print("    Timeout!")
    except Exception as e:
        print(f"    Error: {e}")

    page.wait_for_timeout(2000)
    print(f"    Title: {page.title()}")
    print(f"    URL: {page.url}")

    # Check CAPTCHA again
    captcha2 = page.locator("iframe[src*='recaptcha'], iframe[src*='captcha'], div.g-recaptcha")
    if captcha2.count() > 0:
        print("[!] CAPTCHA after submit. Please solve...")
        for i in range(60):
            time.sleep(2)
            if captcha2.count() == 0:
                print("[✓] CAPTCHA resolved!")
                break
            if i % 5 == 0:
                print(f"    Still waiting... ({i*2}s)")
        else:
            print("[✗] CAPTCHA timeout")
        page.wait_for_timeout(1500)

    print(f"    After submit — Title: {page.title()}")
    print(f"    After submit — URL: {page.url}")

    # Step 4: Look for snapshot links
    print("\n[4] Looking for snapshot links...")
    all_links = page.locator("a[href]")
    print(f"    Total links: {all_links.count()}")
    import re
    snapshot_urls = []
    for i in range(min(all_links.count(), 30)):
        try:
            href = all_links.nth(i).get_attribute("href") or ""
            text = all_links.nth(i).inner_text()[:80]
            if re.search(r"archive\.(is|ph|today|fo|li|vn|md)/", href):
                if "run=1" not in href and "search=" not in href.lower():
                    snapshot_urls.append(href)
                    print(f"    [{i}] SNAPSHOT: {href}")
                    print(f"         text: {text}")
            elif "http" in href:
                print(f"    [{i}] {href[:100]}")
                print(f"         text: {text[:80]}")
        except Exception:
            pass

    if snapshot_urls:
        print(f"\n    Found {len(snapshot_urls)} snapshots!")
    else:
        print("\n    No snapshots found.")

    # Step 5: Print page body snippet
    print("\n[5] Page body preview:")
    body = page.locator("body").first
    if body.count() > 0:
        text = body.inner_text()[:500]
        print(f"    {text}")

    print("\nDebug complete. Browser remains open for inspection.")
    print("Press Enter to close...")
    input()
    browser.close()
