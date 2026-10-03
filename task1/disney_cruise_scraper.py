"""
Disney Cruise Line - Data Extraction Engineer Challenge (Task 1)
-----------------------------------------------------------------

Requirements:
    - Python 3.10+
    - Playwright
    - pandas

This script:
    1. Opens Disney Cruise Line India website with anti-bot stealth.
    2. Handles consent / cookies.
    3. Clicks View Dates.
    4. Waits for dynamic cruise results.
    5. Scrolls through the result page.
    6. Expands itinerary date sections (click -> collect -> store -> repeat).
    7. Extracts cruise/itinerary data.
    8. Handles pagination / "Load More".
    9. Cleans and deduplicates data.
   10. Saves raw JSON and final CSV.
   11. Prints and saves the five challenge answers.
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from playwright.async_api import (
    async_playwright,
    Page,
    Locator,
    TimeoutError as PlaywrightTimeoutError,
)

BASE_URL = "https://disneycruise.disney.go.com/en-in/"
OUTPUT_DIR = Path("task1/disney_cruise_output")

RAW_JSON = OUTPUT_DIR / "raw_cruises.json"
RAW_CSV = OUTPUT_DIR / "raw_cruises.csv"
CLEAN_JSON = OUTPUT_DIR / "clean_cruises.json"
CSV_FILE = OUTPUT_DIR / "results.csv"
ANSWERS_FILE = OUTPUT_DIR / "answers.txt"

MIN_PAGES_REQUIRED = 35
MAX_PAGES = 200

# Set HEADLESS to False to bypass Akamai/Cloudflare bot detection (Headful mode)
HEADLESS = False
GUESTS = 2
DEBUG_BROWSER = False

SHORT_WAIT = 1.0
MEDIUM_WAIT = 2.0


def clean_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    value = str(value)
    value = value.replace("\xa0", " ")
    value = value.replace("\u200b", "")
    value = value.replace("\u200c", "")
    value = value.replace("\u200d", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def unique_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        item = clean_text(item)
        if not item:
            continue
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def normalize_key(value: str) -> str:
    value = clean_text(value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return value.strip()


async def handle_consent(page: Page) -> None:
    consent_texts = ["Agree", "Accept", "Accept All", "Allow All", "I Agree", "Got it", "Continue"]
    for text in consent_texts:
        try:
            locator = page.get_by_role("button", name=re.compile(rf"^{re.escape(text)}$", re.I))
            if await locator.count() > 0:
                if await locator.first.is_visible():
                    await locator.first.click()
                    print(f"[CONSENT] Clicked: {text}")
                    await page.wait_for_timeout(1000)
                    return
        except Exception:
            pass


async def click_view_dates(page: Page) -> None:
    print("[SEARCH] Looking for View Dates...")
    await page.wait_for_timeout(6000)
    await page.evaluate("window.scrollBy(0, 400)")
    await page.wait_for_timeout(2000)

    selectors = [
        page.get_by_role("button", name=re.compile(r"View\s+Dates", re.I)),
        page.get_by_text("View Dates", exact=True),
        page.locator("a:has-text('View Dates')"),
        page.locator("button:has-text('View Dates')"),
        page.locator("text=/View Dates/i"),
    ]
    for locator in selectors:
        try:
            count = await locator.count()
            if count == 0:
                continue
            for i in range(count):
                candidate = locator.nth(i)
                if await candidate.is_visible():
                    await candidate.scroll_into_view_if_needed()
                    await page.wait_for_timeout(1000)
                    await candidate.click(force=True)
                    print("[SEARCH] View Dates clicked")
                    await page.wait_for_timeout(5000)
                    return
        except Exception:
            continue

    try:
        clicked_js = await page.evaluate("""() => {
            const elements = Array.from(document.querySelectorAll('button, a, div, span'));
            const target = elements.find(el => el.textContent && el.textContent.trim().toLowerCase() === 'view dates');
            if (target) {
                target.click();
                return true;
            }
            return false;
        }""")
        if clicked_js:
            print("[SEARCH] View Dates clicked via JavaScript fallback")
            await page.wait_for_timeout(5000)
            return
    except Exception:
        pass

    raise RuntimeError("Could not locate the 'View Dates' button.")


async def wait_for_results(page: Page) -> None:
    print("[WAIT] Waiting for cruise results...")
    result_words = ["Cruises", "Cruise", "View Dates", "Show Dates", "Hide Dates"]
    for word in result_words:
        try:
            await page.get_by_text(word, exact=False).first.wait_for(state="visible", timeout=3000)
            print(f"[OK] Result indicator found: {word}")
            break
        except Exception:
            pass
    await page.wait_for_load_state("domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)


async def scroll_to_bottom(page: Page, max_rounds: int = 60) -> None:
    print("[SCROLL] Loading all visible cruise cards...")
    previous_height = 0
    stable_rounds = 0
    for round_no in range(max_rounds):
        current_height = await page.evaluate("() => document.documentElement.scrollHeight")
        await page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        await page.wait_for_timeout(800)
        new_height = await page.evaluate("() => document.documentElement.scrollHeight")
        if new_height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_height = new_height
        if round_no % 5 == 0:
            print(f"[SCROLL] round={round_no + 1}, height={new_height}")
        if stable_rounds >= 5:
            break
    print("[SCROLL] Page appears fully loaded.")


async def expand_date_sections(page: Page) -> None:
    print("[DATES] Expanding cruise date sections...")
    for round_no in range(20):
        clicked = 0
        try:
            show_dates = page.get_by_text(re.compile(r"Show\s+(?:\d+\s+)?Dates?", re.I))
            count = await show_dates.count()
            for i in range(count):
                item = show_dates.nth(i)
                try:
                    if not await item.is_visible():
                        continue
                    await item.scroll_into_view_if_needed()
                    await item.click(timeout=5000)
                    clicked += 1
                    await page.wait_for_timeout(300)
                except Exception:
                    continue
        except Exception:
            pass
        if clicked == 0:
            break
        print(f"[DATES] Expansion round {round_no + 1}: {clicked} clicked")
    await page.wait_for_timeout(1000)


async def find_candidate_cards(page: Page) -> List[Locator]:
    candidates = []
    # Target specific cruise card containers to avoid scanning thousands of generic divs
    selectors = [
        "article",
        "[role='article']",
        "li",
        "[class*='card']",
        "[class*='Cruise']",
        "div:has-text('Night Cruise')",
        "div:has-text('Sailing to')",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            max_check = min(count, 150)
            for i in range(max_check):
                item = locator.nth(i)
                try:
                    text = clean_text(await item.inner_text(timeout=400))
                except Exception:
                    continue
                if not text:
                    continue
                lower = text.casefold()
                if "night" in lower and ("cruise" in lower or "sailing" in lower):
                    candidates.append(item)
        except Exception:
            continue

    unique = []
    seen_boxes = set()
    for item in candidates:
        try:
            box = await item.bounding_box()
            if not box:
                continue
            key = (round(box["x"]), round(box["y"]), round(box["width"]), round(box["height"]))
            if key in seen_boxes:
                continue
            seen_boxes.add(key)
            unique.append(item)
        except Exception:
            continue
    return unique


def parse_cruise_text(text: str) -> Dict:
    text = clean_text(text)
    record = {
        "cruise_name": "",
        "ship": "",
        "nights": "",
        "departure_port": "",
        "destination": "",
        "itinerary": "",
        "cruise_type": "",
        "dates": "",
        "date_count": 0,
        "raw_text": text,
    }

    # 1. Nights
    nights_match = re.search(r"(\d+)\s*[-]?\s*Night", text, flags=re.I)
    if nights_match:
        record["nights"] = nights_match.group(1)

    # 2. Cruise Name / Title (supports both "3-Night Cruise from Singapore" and "10-Night Southern Caribbean Cruise from Port Canaveral")
    title_match = re.search(r"(\d+\s*[-]?\s*Night\s+(?:[A-Za-z\s,.-]+?\s+)?Cruise\s+from\s+[A-Za-z\s,.-]+)", text, flags=re.I)
    if title_match:
        record["cruise_name"] = clean_text(title_match.group(1))
    else:
        for line in text.splitlines():
            if "night" in line.casefold() and "cruise" in line.casefold():
                record["cruise_name"] = clean_text(line)
                break

    # 3. Departure Port
    from_match = re.search(r"Cruise\s+from\s+([A-Za-z\s,.-]+?)(?=\s+What's|\s+USD|\s+INR|\s+Rate|\s+Show|\s+Hide|\s+Sailing|$)", text, flags=re.I)
    if from_match:
        record["departure_port"] = clean_text(from_match.group(1))
    else:
        dep_match = re.search(r"from\s+([A-Z][A-Za-z .,'-]+)", text, flags=re.I)
        if dep_match:
            record["departure_port"] = clean_text(dep_match.group(1)).split("Cruise", 1)[0]

    # 4. Destination ("Sailing to" or from title or fallback to departure port)
    sailing_match = re.search(r"Sailing\s+to\s+([A-Za-z\s,.-|]+?)(?=\s+Price|\s+USD|\s+INR|\s+Rate|\s+Show|\s+Hide|$)", text, flags=re.I)
    if sailing_match:
        record["destination"] = clean_text(sailing_match.group(1))
    elif record["cruise_name"]:
        dest_match = re.search(r"\d+\s*[-]?\s*Night\s+(.+?)\s+Cruise\s+from", record["cruise_name"], flags=re.I)
        if dest_match:
            record["destination"] = clean_text(dest_match.group(1))

    if not record["destination"] and record["departure_port"]:
        record["destination"] = record["departure_port"]

    # 5. Ship Name (e.g. "Sailing on Disney ADVENTURE")
    ship_match = re.search(r"Sailing\s+on\s+(Disney\s+[A-Za-z]+)", text, flags=re.I)
    if ship_match:
        record["ship"] = clean_text(ship_match.group(1))
    else:
        ship_names = [
            "Disney Adventure",
            "Disney Destiny",
            "Disney Wish",
            "Disney Treasure",
            "Disney Fantasy",
            "Disney Dream",
            "Disney Wonder",
            "Disney Magic",
        ]
        for ship in ship_names:
            if ship.casefold() in text.casefold():
                record["ship"] = ship
                break

    # 6. Dates
    date_patterns = [
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\s*-\s*\d{1,2},?\s+\d{4}\b",
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
    ]
    dates = []
    for pattern in date_patterns:
        dates.extend(re.findall(pattern, text, flags=re.I))
    dates = unique_preserve_order(dates)
    record["dates"] = " | ".join(dates)
    record["date_count"] = len(dates)

    # 7. Cruise Type
    holiday_keywords = ["holiday", "halloween", "very merrytime", "merrytime", "christmas", "thanksgiving", "new year's", "new years", "valentine", "easter"]
    found_holidays = [kw for kw in holiday_keywords if kw in text.casefold()]
    record["cruise_type"] = "Holiday" if found_holidays else "Regular"

    # 8. Itinerary
    if record["cruise_name"]:
        itinerary = record["cruise_name"]
        if record["departure_port"] and record["departure_port"] not in itinerary:
            itinerary += f" from {record['departure_port']}"
        record["itinerary"] = itinerary
    else:
        record["itinerary"] = f"{record['nights']}-Night Cruise from {record['departure_port']}"

    return record


async def extract_cards_sequentially(page: Page) -> List[Dict]:
    print("[EXTRACT] Discovering main cruise cards and expanding sub-cards (click -> collect -> store -> repeat)...")
    cards = await find_candidate_cards(page)
    print(f"[EXTRACT] Total main candidate cards discovered on current page: {len(cards)}")

    all_sub_records = []
    for index, card in enumerate(cards):
        try:
            print(f"\n--- [MAIN CARD {index + 1}/{len(cards)}] Processing main card ---")
            print(f"[MAIN CARD {index + 1}] [1/4 CLICK] Scrolling card into view...")
            await card.scroll_into_view_if_needed()
            await page.wait_for_timeout(300)

            # Click expand date button on this card ("Show X Dates" / "Show Dates")
            expanded = False
            try:
                expand_btn = card.locator("text=/Show\\s+(?:\\d+\\s+)?Dates?/i").first
                if await expand_btn.is_visible(timeout=1000):
                    btn_text = await expand_btn.inner_text()
                    print(f"[MAIN CARD {index + 1}] [1/4 CLICK] Found expand button ('{btn_text}'). Clicking to reveal sub-cards (sail dates)...")
                    await expand_btn.click()
                    await page.wait_for_timeout(1500)  # wait for sub-cards to render
                    expanded = True
                    print(f"[MAIN CARD {index + 1}] [1/4 CLICK] Sub-cards expanded successfully.")
                else:
                    print(f"[MAIN CARD {index + 1}] [1/4 CLICK] No expand button visible (already expanded or single date).")
            except Exception as e:
                print(f"[MAIN CARD {index + 1}] [1/4 CLICK] Sub-card expansion note: {e}")

            print(f"[MAIN CARD {index + 1}] [2/4 COLLECT] Extracting base main card text...")
            main_text = clean_text(await card.inner_text(timeout=3000))
            if len(main_text) < 30:
                print(f"[MAIN CARD {index + 1}] [2/4 COLLECT] Skipped: Main card text too short.")
                continue

            base_record = parse_cruise_text(main_text)

            # Look for sub-cards (individual sail date rows/containers inside this card)
            sub_card_elements = []
            try:
                sub_card_elements = await card.locator("text=/[A-Z][a-z]+\\s+\\d{1,2}\\s*-\\s*(?:[A-Z][a-z]+\\s+)?\\d{1,2},?\\s+\\d{4}/").all()
            except Exception:
                pass

            if sub_card_elements:
                print(f"[MAIN CARD {index + 1}] [3/4 STORE] Discovered {len(sub_card_elements)} sub-card(s) (individual sail dates). Storing each sub-card raw...")
                for sub_idx, sub_el in enumerate(sub_card_elements):
                    try:
                        sub_container = sub_el.locator("xpath=ancestor::*[contains(@class, 'row') or contains(@class, 'card') or self::div][1]")
                        sub_text = clean_text(await sub_container.inner_text(timeout=2000)) if sub_container else clean_text(await sub_el.inner_text())
                        
                        combined_text = f"{main_text}\n--- Sail Date Option ---\n{sub_text}"
                        sub_record = parse_cruise_text(combined_text)
                        
                        if not sub_record.get("cruise_name") and base_record.get("cruise_name"):
                            sub_record["cruise_name"] = base_record["cruise_name"]
                        if not sub_record.get("departure_port") and base_record.get("departure_port"):
                            sub_record["departure_port"] = base_record["departure_port"]
                        if not sub_record.get("destination") and base_record.get("destination"):
                            sub_record["destination"] = base_record["destination"]
                        if not sub_record.get("nights") and base_record.get("nights"):
                            sub_record["nights"] = base_record["nights"]

                        sub_record["source_index"] = f"{index + 1}.{sub_idx + 1}"
                        try:
                            sub_record["source_url"] = await page.url
                        except Exception:
                            sub_record["source_url"] = ""

                        all_sub_records.append(sub_record)
                        print(f"    -> [SUB-CARD {sub_idx + 1}] Stored raw: Dates: {sub_record.get('dates', 'N/A')} | Ship: {sub_record.get('ship', 'N/A')}")
                    except Exception as sub_exc:
                        print(f"    -> [SUB-CARD {sub_idx + 1}] Extraction error: {sub_exc}")
            else:
                base_record["source_index"] = f"{index + 1}.1"
                try:
                    base_record["source_url"] = await page.url
                except Exception:
                    base_record["source_url"] = ""
                all_sub_records.append(base_record)
                print(f"[MAIN CARD {index + 1}] [3/4 STORE] Stored base record raw (no sub-cards detected).")

            print(f"[MAIN CARD {index + 1}] [4/4 REPEAT] Collapsing sub-cards ('Hide Dates') if open...")
            if expanded:
                try:
                    collapse_btn = card.locator("text=/Hide\\s+(?:\\d+\\s+)?Dates?/i").first
                    if await collapse_btn.is_visible(timeout=500):
                        await collapse_btn.click()
                        await page.wait_for_timeout(300)
                        print(f"[MAIN CARD {index + 1}] [4/4 REPEAT] Collapsed successfully. Moving to next main card...")
                except Exception:
                    pass

        except Exception as exc:
            print(f"[WARN] Main card {index + 1} processing failed: {exc}")

    print(f"[EXTRACT] Page processing complete. Total raw records (including sub-cards): {len(all_sub_records)}")
    return all_sub_records


# Backward compatibility alias
async def extract_cards(page: Page) -> List[Dict]:
    return await extract_cards_sequentially(page)


async def get_page_signature(page: Page) -> str:
    try:
        body = clean_text(await page.locator("body").inner_text())
        return body[:10000]
    except Exception:
        return ""


async def click_next_page(page: Page) -> bool:
    old_signature = await get_page_signature(page)
    patterns = [r"^Next$", r"Next Page", r"Go to next", r"Next results"]
    for pattern in patterns:
        try:
            locator = page.get_by_role("button", name=re.compile(pattern, re.I))
            count = await locator.count()
            for i in range(count):
                button = locator.nth(i)
                try:
                    if not await button.is_visible() or await button.is_disabled():
                        continue
                    await button.scroll_into_view_if_needed()
                    await button.click()
                    await page.wait_for_timeout(2500)
                    if await get_page_signature(page) != old_signature:
                        return True
                except Exception:
                    continue
        except Exception:
            pass
    return False


def deduplicate_records(records: List[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for record in records:
        key = (
            normalize_key(record.get("cruise_name", "")),
            normalize_key(record.get("ship", "")),
            normalize_key(record.get("departure_port", "")),
            normalize_key(record.get("destination", "")),
            normalize_key(record.get("dates", "")),
        )
        if not any(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def clean_records(records: List[Dict]) -> List[Dict]:
    print(f"[CLEAN] Reading and cleaning {len(records)} raw records from raw CSV/dataset...")
    cleaned = []
    for idx, record in enumerate(records):
        cleaned_record = {k: clean_text(v) if isinstance(v, str) else v for k, v in record.items()}
        if not cleaned_record.get("departure_port"):
            print(f"[CLEAN] Skipping record {idx + 1}: missing departure port.")
            continue
        if not cleaned_record.get("destination"):
            itinerary = cleaned_record.get("itinerary", "")
            if itinerary:
                cleaned_record["destination"] = itinerary
        if not cleaned_record.get("destination"):
            print(f"[CLEAN] Skipping record {idx + 1}: missing destination.")
            continue
        cleaned.append(cleaned_record)
        print(f"[CLEAN] Validated record {idx + 1}: {cleaned_record.get('cruise_name')} | Port: {cleaned_record.get('departure_port')} | Dates: {cleaned_record.get('dates')}")
    return deduplicate_records(cleaned)


def save_csv(records: List[Dict]) -> pd.DataFrame:
    print(f"[OUTPUT] Preparing to write {len(records)} cleansed records to CSV...")
    columns = [
        "cruise_name",
        "ship",
        "nights",
        "departure_port",
        "destination",
        "itinerary",
        "cruise_type",
        "dates",
        "date_count",
        "source_url",
        "raw_text",
    ]
    df = pd.DataFrame(records)
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    df = df[columns]
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].fillna("").astype(str).map(clean_text)
    df = df.drop_duplicates()

    print(f"[OUTPUT] Printing all final rows being written to CSV:")
    for idx, row in df.iterrows():
        print(f"    -> [ROW {idx + 1}] {row['cruise_name']} | Ship: {row['ship']} | Port: {row['departure_port']} | Dest: {row['destination']} | Dates: {row['dates']}")

    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    print(f"[OUTPUT] CSV saved successfully: {CSV_FILE}")
    print(f"[OUTPUT] Total CSV rows: {len(df)}")
    return df


def calculate_answers(df: pd.DataFrame) -> Dict[str, int]:
    if df.empty:
        return {
            "pacific_cruises": 0,
            "total_cruises": 0,
            "holiday_cruises": 0,
            "more_than_2_dates": 0,
            "miami_cruises": 0,
            "london_cruises": 0,
            "miami_and_london": 0,
        }

    pacific_mask = df[["destination", "itinerary", "raw_text"]].fillna("").astype(str).apply(
        lambda row: row.str.contains(r"\bPacific\b", case=False, regex=True).any(), axis=1
    )
    pacific_count = int(pacific_mask.sum())
    total_count = len(df)

    holiday_mask = df[["cruise_type", "raw_text", "itinerary"]].fillna("").astype(str).apply(
        lambda row: row.str.contains(r"holiday|halloween|very merrytime|merrytime|christmas|thanksgiving|new year", case=False, regex=True).any(), axis=1
    )
    holiday_count = int(holiday_mask.sum())

    date_count = pd.to_numeric(df["date_count"], errors="coerce").fillna(0)
    more_than_2_dates = int((date_count > 2).sum())

    departure = df["departure_port"].fillna("").astype(str)
    miami_count = int(departure.str.contains(r"\bMiami\b", case=False, regex=True).sum())
    london_count = int(departure.str.contains(r"\bLondon\b", case=False, regex=True).sum())
    combined_miami_london = miami_count + london_count

    return {
        "pacific_cruises": pacific_count,
        "total_cruises": total_count,
        "holiday_cruises": holiday_count,
        "more_than_2_dates": more_than_2_dates,
        "miami_cruises": miami_count,
        "london_cruises": london_count,
        "miami_and_london": combined_miami_london,
    }


def print_answers(answers: Dict[str, int]) -> None:
    print("\n" + "=" * 70)
    print("RELU CONSULTANCY - CHALLENGE ANSWERS")
    print("=" * 70)
    print(f"(i)   Pacific destination cruises : {answers['pacific_cruises']}")
    print(f"(ii)  Total cruises               : {answers['total_cruises']}")
    print(f"(iii) Holiday cruises             : {answers['holiday_cruises']}")
    print(f"(iv)  Cruises with >2 dates       : {answers['more_than_2_dates']}")
    print(f"(v)   Miami departure cruises     : {answers['miami_cruises']}")
    print(f"      London departure cruises    : {answers['london_cruises']}")
    print(f"      Miami + London total        : {answers['miami_and_london']}")
    print("=" * 70 + "\n")


def save_answers(answers: Dict[str, int]) -> None:
    with open(ANSWERS_FILE, "w", encoding="utf-8") as file:
        file.write("Relu Consultancy - Data Extraction Challenge\n")
        file.write("==============================================\n\n")
        file.write(f"(i) How many total cruises are there for the Pacific as a destination? {answers['pacific_cruises']}\n")
        file.write(f"(ii) How many total cruises are there? {answers['total_cruises']}\n")
        file.write(f"(iii) How many holiday cruises are there? {answers['holiday_cruises']}\n")
        file.write(f"(iv) How many cruises offer more than 2 dates for booking? {answers['more_than_2_dates']}\n")
        file.write(f"(v) Miami departure cruises: {answers['miami_cruises']}\n")
        file.write(f"    London departure cruises: {answers['london_cruises']}\n")
        file.write(f"    Combined Miami + London: {answers['miami_and_london']}\n")
    print(f"[OUTPUT] Answers saved: {ANSWERS_FILE}")


async def run_scraper():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_records = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-infobars",
                "--start-maximized",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )

        page = await context.new_page()

        # Stealth evasion against bot detection
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        api_requests = []
        page.on("request", lambda req: api_requests.append(req.url) if any(x in req.url.lower() for x in ["api", "graphql", "cruise", "search"]) else None)

        try:
            print("\n[STEP 1] Opening Disney Cruise Line...")
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
            print(f"[PAGE] {await page.title()}")

            for attempt in range(3):
                title_text = await page.title()
                body_text = await page.inner_text("body")
                if "Access Denied" in title_text or "Access Denied" in body_text:
                    print(f"[WARN] Encountered Access Denied (Attempt {attempt+1}/3). Waiting 5s and reloading...")
                    await page.wait_for_timeout(5000)
                    await page.reload(wait_until="domcontentloaded")
                else:
                    break

            await handle_consent(page)
            await page.wait_for_timeout(2000)

            print("\n[STEP 2] Configuring cruise search...")
            await click_view_dates(page)

            print("\n[STEP 3] Waiting for results...")
            await wait_for_results(page)

            print("\n[STEP 4] Extracting cruise data...")
            visited_signatures = set()

            for page_number in range(1, MAX_PAGES + 1):
                print(f"\n" + "=" * 60)
                print(f"[PAGE {page_number}] Processing results")
                print("=" * 60)

                await scroll_to_bottom(page)
                await scroll_to_bottom(page, max_rounds=20)
                await page.wait_for_timeout(3000)

                records = await extract_cards(page)
                print(f"[PAGE {page_number}] Extracted candidates: {len(records)}")
                all_records.extend(records)

                signature = await get_page_signature(page)
                if signature in visited_signatures:
                    print("[PAGINATION] Repeated page detected. Stopping.")
                    break
                visited_signatures.add(signature)

                moved = await click_next_page(page)
                if not moved:
                    print("[PAGINATION] No additional page detected.")
                    break
                await wait_for_results(page)

            print("\n[STEP 5] Saving raw data to raw CSV first...")
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            df_raw = pd.DataFrame(all_records)
            df_raw.to_csv(RAW_CSV, index=False, encoding="utf-8-sig")
            print(f"[OUTPUT] Raw CSV saved: {RAW_CSV} ({len(df_raw)} rows)")

            with open(RAW_JSON, "w", encoding="utf-8") as file:
                json.dump(all_records, file, ensure_ascii=False, indent=2)
            print(f"[OUTPUT] Raw JSON saved: {RAW_JSON}")

            print("\n[STEP 5B] Loading data from raw CSV for collection, cleaning, and processing...")
            df_loaded_raw = pd.read_csv(RAW_CSV).fillna("")
            raw_records_from_csv = df_loaded_raw.to_dict(orient="records")

            cleaned_records = clean_records(raw_records_from_csv)
            with open(CLEAN_JSON, "w", encoding="utf-8") as file:
                json.dump(cleaned_records, file, ensure_ascii=False, indent=2)

            print("\n[STEP 6] Creating final cleansed CSV...")
            df = save_csv(cleaned_records)

            answers = calculate_answers(df)
            print_answers(answers)
            save_answers(answers)

            print("\n[VALIDATION]")
            print(f"Raw records       : {len(all_records)}")
            print(f"Clean records     : {len(cleaned_records)}")
            print(f"CSV records       : {len(df)}")
            print(f"Pages processed   : {len(visited_signatures)}")
            print("\n[DONE] Scraping completed successfully.")

        finally:
            try:
                api_file = OUTPUT_DIR / "discovered_api_urls.txt"
                with open(api_file, "w", encoding="utf-8") as file:
                    for url in sorted(set(api_requests)):
                        file.write(url + "\n")
            except Exception:
                pass
            await context.close()
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_scraper())
    except KeyboardInterrupt:
        print("\n[STOPPED] Scraper interrupted.")
    except Exception as exc:
        print("\n[ERROR]")
        print(str(exc))
        raise
