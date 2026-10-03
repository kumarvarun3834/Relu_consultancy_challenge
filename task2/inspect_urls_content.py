import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    urls_file = Path("task2/urls.txt")
    if not urls_file.exists():
        print("urls.txt not found!")
        return
        
    with open(urls_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]
        
    print(f"Loaded {len(urls)} URLs from urls.txt. Inspecting first 5 URLs...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        for i, url in enumerate(urls[:5]):
            print(f"\n--- URL #{i+1}: {url} ---")
            try:
                await page.goto(url, timeout=30000)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(2000)
                
                title = await page.title()
                print(f"  Title: {title}")
                
                # Check headings and key info sections
                info = await page.evaluate("""() => {
                    const h1 = document.querySelector('h1')?.innerText || '';
                    const desc = document.querySelector('.company-description, .product-description, [id*="desc"]')?.innerText || '';
                    const text = document.body ? document.body.innerText : '';
                    
                    // Extract potential key facts
                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 5);
                    const sampleLines = lines.slice(0, 15);
                    
                    return {
                        h1: h1,
                        desc: desc.substring(0, 200),
                        sampleLines: sampleLines
                    };
                }""")
                
                print(f"  H1 Heading: {info['h1']}")
                print(f"  Description snippet: {info['desc']}")
                print("  Sample lines:")
                for line in info['sampleLines'][:8]:
                    print(f"    - {line}")
                    
            except Exception as e:
                print(f"  Error visiting {url}: {e}")
                
        await browser.close()

asyncio.run(main())
