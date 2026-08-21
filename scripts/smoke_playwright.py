from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://example.com")
    print("TITULO:", page.title())
    print("H1:", page.inner_text("h1"))
    browser.close()
