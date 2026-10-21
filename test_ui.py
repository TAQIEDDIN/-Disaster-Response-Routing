import asyncio
import os
from playwright.async_api import async_playwright
from backend.config import HOST, PORT

UI_TEST_URL = os.getenv(
    "UI_TEST_URL",
    f"http://{'127.0.0.1' if HOST in {'0.0.0.0', '::'} else HOST}:{PORT}/static/index.html",
)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Hataları yakala
        errors = []
        page.on("console", lambda msg: print(f"Console: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda err: errors.append(err.message))
        
        print("Navigating to page...")
        await page.goto(UI_TEST_URL)
        await page.wait_for_selector("#openDroneWorkspaceBtn")
        
        print("Clicking button...")
        await page.click("#openDroneWorkspaceBtn")
        
        await page.wait_for_timeout(1000)
        display = await page.evaluate("window.getComputedStyle(document.getElementById('droneModeOverlay')).display")
        print(f"Overlay CSS display after click: {display}")
        
        if errors:
            print(f"Page errors: {errors}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
