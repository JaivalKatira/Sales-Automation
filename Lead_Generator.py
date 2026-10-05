"""
Lead Gen -> WhatsApp Company Profile Pipeline
==============================================

Purpose: takes Google Maps data and ENRICHES it with web search (searching
for each company found on Maps) to find additional contact info (email/phone).
Then verifies WhatsApp numbers. Optimized for lead quality over quantity.

Outputs (in ./data/):
    leads_master.csv     -> full lead data (name, phone, website, email,
                             address, source keyword, date found). Every
                             lead found, whether or not it had a phone
                             number.
    company_profile.csv  -> ONLY leads with a usable phone number, tagged
                             with WhatsApp verification status and
                             vertical-fit judgment. Ready for context.py.

Sources combined:
1. Google Maps Places API  -> business name, address, phone, website
2. Web Enrichment (SerpAPI / DuckDuckGo) -> searches for each company name
                               to find additional contact info (email/phone)
                               not already found on Maps
3. Site enrichment         -> scrapes the websites found for email addresses
                               (no CAPTCHA risk on this step)
4. Selenium / web.whatsapp.com -> verifies each phone-having lead's number
                               is a real WhatsApp number, reusing the same
                               persistent browser profile whatapp_sender.py
                               logs into (no second QR-code scan needed)

Setup before running:
    pip install googlemaps requests beautifulsoup4 lxml python-dotenv phonenumbers selenium google-search-results duckduckgo-search
    Create a .env file next to this script with:
        GOOGLE_MAPS_API_KEY=your_maps_key_here
        SERP_API_KEY=your_serp_api_key_here      (optional, falls back to DuckDuckGo)

Run:
    python3 Lead_Generator.py
    -> you'll be prompted for a location, comma-separated keywords, and a
       one-line goal describing what you want out of these leads
"""

import csv
import os
import re
import time
import random
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import googlemaps
from dotenv import load_dotenv
import phonenumbers

try:
    from serpapi import GoogleSearch
except ImportError:
    GoogleSearch = None

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoAlertPresentException,
    TimeoutException,
    UnexpectedAlertPresentException,
)

load_dotenv()

# ---------------- Config ----------------
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
SERP_API_KEY = os.getenv("SERP_API_KEY")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 10
DELAY_RANGE = (2, 4)       # between API calls

MAX_RESULTS_PER_KEYWORD = 5  # bumped up from prototype.py's 3

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
MASTER_LEADS_CSV = os.path.join(DATA_DIR, "leads_master.csv")
COMPANY_PROFILE_CSV = os.path.join(DATA_DIR, "company_profile.csv")

# Same folder whatapp_sender.py points its persistent Chrome profile at, so
# the WhatsApp Web login session (and QR-code scan) is shared between the
# two scripts regardless of which one is run from where.
PROFILE_DIR = os.path.join(os.getcwd(), "whatsapp_selenium_profile")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\-.\s()]{6,16}\d)")
CONTACT_PAGE_HINTS = ["contact", "about", "get-in-touch", "reach-us"]

LEAD_FIELDNAMES = [
    "company_name", "phone", "phone_clean", "website", "email",
    "address", "keyword", "date_found",
]

def _digit_count(s: str) -> int:
    return sum(c.isdigit() for c in s)


# ---------------- Phone parsing helpers (Change 2) ----------------
# Address-based region guessing (Maps' formatted_address -> country ->
# ISO region) was producing wrong E.164 numbers for a chunk of leads,
# which in turn made WhatsApp verification land on "Unverified" instead
# of correctly detecting a valid number. Simpler and more reliable: the
# user manually enters the country dial code once per run (all leads in
# a run share one location/search anyway), and every number is parsed
# against that.
def normalize_country_code(raw: str) -> str | None:
    """
    Normalizes a user-entered country dial code like '+91', '91', or
    ' +91 ' into the '+91' shape phonenumbers expects. Returns None if it
    doesn't look like a valid dial code.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    return f"+{digits}"


def region_for_dial_code(country_code: str | None) -> str | None:
    """'+51' -> 'PE', '+91' -> 'IN'. None if the dial code is unknown."""
    if not country_code:
        return None
    try:
        region = phonenumbers.region_code_for_country_code(int(country_code.lstrip("+")))
    except ValueError:
        return None
    # "ZZ" means the dial code doesn't exist.
    return region if region and region != "ZZ" else None


def clean_phone(raw: str, country_code: str | None = None) -> str:
    """
    Normalize a phone number into E.164 ('+<countrycode><number>') using
    the phonenumbers library.

    Tries, in order, and returns the first valid result:
      1. Self-describing international format ('+51 1 234 5678').
      2. Parsed with the REGION of the dial code you entered ('PE' for +51).
         This is the important one: phonenumbers knows each country's own
         trunk prefixes and quirks (Peru's '01' Lima area code, Argentina's
         '0' + '15' mobile prefixes, Mexico's old '044'/'045', Italy keeping
         its leading 0, ...). The old code blindly deleted a leading 0 and
         glued the dial code on, which broke many non-Indian numbers.
      3. Digits that already start with the dial code but lack the '+'
         ('51987654321').
      4. Legacy fallback: strip one leading 0 and prepend the dial code.
    Returns '' if nothing validates.
    """
    if not raw:
        return ""
    raw = raw.strip()

    def _to_e164(parsed) -> str | None:
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        return None

    def _try(candidate: str, region: str | None) -> str | None:
        try:
            return _to_e164(phonenumbers.parse(candidate, region))
        except phonenumbers.NumberParseException:
            return None

    # 1. Already international.
    if raw.startswith("+"):
        result = _try(raw, None)
        if result:
            return result

    region = region_for_dial_code(country_code)

    # 2. National format, parsed with the right country's rules.
    if region:
        result = _try(raw, region)
        if result:
            return result

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""

    # 3. Dial code present but '+' missing, e.g. '51987654321'.
    if country_code:
        dial = country_code.lstrip("+")
        if digits.startswith(dial):
            result = _try(f"+{digits}", None)
            if result:
                return result

    # 4. Legacy fallback: drop one trunk '0', prepend the dial code.
    if country_code:
        national = digits[1:] if digits.startswith("0") else digits
        result = _try(f"{country_code}{national}", None)
        if result:
            return result

    return ""


# ---------------- Source 1: Google Maps ----------------
def search_google_maps(keyword: str, location: str, country_code: str,
                        max_results: int = MAX_RESULTS_PER_KEYWORD) -> list[dict]:
    if not GOOGLE_MAPS_API_KEY:
        print("  [Maps] Skipped — no GOOGLE_MAPS_API_KEY set in .env")
        return []

    gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
    results = []
    try:
        response = gmaps.places(query=f"{keyword} in {location}")
        for place in response.get("results", [])[:max_results]:
            place_id = place.get("place_id")
            details = {}
            if place_id:
                details = gmaps.place(
                    place_id=place_id,
                    fields=["name", "formatted_address", "formatted_phone_number",
                            "international_phone_number", "website"],
                ).get("result", {})
            # Prefer Maps' international format ('+51 1 234 5678'): it names
            # its own country, so no guessing is needed. Fall back to the
            # national format, which clean_phone parses with the dial code.
            phone_intl = details.get("international_phone_number", "")
            phone_national = details.get("formatted_phone_number", "")
            phone_raw = phone_intl or phone_national
            phone_clean = (clean_phone(phone_intl, country_code=country_code)
                           or clean_phone(phone_national, country_code=country_code))
            address = details.get("formatted_address", place.get("formatted_address", ""))

            results.append({
                "company_name": details.get("name", place.get("name", "")),
                "website": details.get("website", ""),
                "email": "",
                "phone": phone_raw,
                "phone_clean": phone_clean,
                "address": address,
                "keyword": keyword,
                "date_found": date.today().isoformat(),
            })
            time.sleep(random.uniform(*DELAY_RANGE))
    except Exception as e:
        print(f"  [Maps] Error: {e}")
    return results


# ---------------- Source 2: Web Search (SerpAPI / DuckDuckGo) ----------------
# NEW APPROACH: Enrich existing Maps leads by searching for company name + location
# (Instead of discovering new leads, we verify and enrich the ones we already have)
def enrich_with_web_search(leads: list[dict], location: str, country_code: str) -> list[dict]:
    """
    For each lead already found in Google Maps, search the web for the company name
    to find additional contact info (email, phone) that Maps may have missed.
    Uses SerpAPI (primary) or DuckDuckGo (fallback).
    
    This ENRICHES existing leads rather than discovering new ones.
    """
    if not leads:
        return leads

    for lead in leads:
        company_name = lead.get("company_name", "")
        if not company_name:
            continue

        # Search for this specific company to find additional contact info
        search_query = f"{company_name} {location}"

        # Try SerpAPI first
        if SERP_API_KEY and GoogleSearch:
            try:
                search = GoogleSearch({
                    "q": search_query,
                    "api_key": SERP_API_KEY,
                    "num": 1,  # Just need top result for verification
                })
                data = search.get_dict()
                result = data.get("organic_results", [{}])[0]
                snippet = result.get("snippet", "")

                # Extract email and phone from snippet if not already in lead
                if snippet:
                    if not lead.get("email"):
                        email_match = EMAIL_RE.search(snippet)
                        if email_match:
                            lead["email"] = email_match.group(0)
                    if not lead.get("phone") and not lead.get("phone_clean"):
                        phone_match = PHONE_RE.search(snippet)
                        if phone_match:
                            phone_raw = phone_match.group(0)
                            lead["phone"] = phone_raw
                            lead["phone_clean"] = clean_phone(phone_raw, country_code=country_code)

                time.sleep(random.uniform(*DELAY_RANGE))
                continue  # Move to next lead
            except Exception as e:
                pass  # Silently continue, not critical for enrichment

        # Fallback to DuckDuckGo
        if DDGS:
            try:
                with DDGS() as ddgs:
                    search_results = list(ddgs.text(search_query, max_results=1))
                    if search_results:
                        result = search_results[0]
                        snippet = result.get("body", "")

                        # Extract email and phone from snippet if not already in lead
                        if snippet:
                            if not lead.get("email"):
                                email_match = EMAIL_RE.search(snippet)
                                if email_match:
                                    lead["email"] = email_match.group(0)
                            if not lead.get("phone") and not lead.get("phone_clean"):
                                phone_match = PHONE_RE.search(snippet)
                                if phone_match:
                                    phone_raw = phone_match.group(0)
                                    lead["phone"] = phone_raw
                                    lead["phone_clean"] = clean_phone(phone_raw, country_code=country_code)

                        time.sleep(random.uniform(*DELAY_RANGE))
            except Exception as e:
                pass  # Silently continue, not critical for enrichment

    return leads


# ---------------- Source 3: Company website scraping ----------------
def fetch_page(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except requests.RequestException:
        return None


def find_contact_page(base_url: str, soup: BeautifulSoup):
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(hint in href for hint in CONTACT_PAGE_HINTS):
            return urljoin(base_url, a["href"])
    return None


def scrape_email_from_site(url: str) -> str:
    soup = fetch_page(url)
    if soup is None:
        return ""

    page_text = soup.get_text(" ", strip=True)
    emails = set(EMAIL_RE.findall(page_text))

    contact_url = find_contact_page(url, soup)
    if contact_url and not emails:
        time.sleep(random.uniform(*DELAY_RANGE))
        contact_soup = fetch_page(contact_url)
        if contact_soup:
            contact_text = contact_soup.get_text(" ", strip=True)
            emails |= set(EMAIL_RE.findall(contact_text))

    return next(iter(emails), "")


def enrich_leads_with_email(leads: list[dict]) -> int:
    """Scrapes each lead's website (found via Maps) for an email, in place."""
    enriched_count = 0
    for lead in leads:
        website = lead.get("website")
        if not website:
            continue
        email = scrape_email_from_site(website)
        if email:
            lead["email"] = email
            enriched_count += 1
        time.sleep(random.uniform(*DELAY_RANGE))
    return enriched_count


# ---------------- Source 3: Groq vertical-fit check (Change 3) ----------------


# ---------------- Source 3: WhatsApp verification via Selenium ----------------
def build_driver():
    """
    Builds a Chrome WebDriver pointed at the same persistent profile
    directory whatapp_sender.py uses, so a previously scanned WhatsApp Web
    QR-code session carries over — no second scan needed.
    """
    options = Options()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--start-maximized")
    # Force English so the "invalid number" dialog text is predictable
    # (a non-English browser would show it in another language).
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-popup-blocking")
    driver = webdriver.Chrome(options=options)
    driver.get("https://web.whatsapp.com")
    return driver


def wait_for_login(driver, timeout: int = 60) -> bool:
    """Blocks until WhatsApp Web's main chat list has loaded (i.e. we're logged in)."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, "//div[@id='pane-side']"))
        )
        return True
    except TimeoutException:
        print("  [WhatsApp] Timed out waiting for login — scan the QR code if prompted.")
        return False


# The chat's message box lives inside <footer>. The old XPath
# (//div[@contenteditable='true'][@data-tab]) also matched the SIDEBAR
# search box, which exists as soon as you're logged in, so it could say
# "Yes" before WhatsApp had even decided whether the number was valid.
COMPOSE_XPATH = '//footer//div[@contenteditable="true"]'

# Case-insensitive match on the invalid-number popup. Matching just "invalid"
# (not the full sentence) survives small wording changes by WhatsApp.
_LOWER = "translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
INVALID_XPATH = (
    f'//div[@role="dialog"]//*[contains({_LOWER}, "invalid")] | '
    f'//div[@role="dialog"]//*[contains({_LOWER}, "not on whatsapp")] | '
    f'//div[@role="dialog"]//*[contains({_LOWER}, "isn\'t on whatsapp")] | '
    f'//*[contains({_LOWER}, "phone number shared via url is invalid")]'
)

VERIFY_TIMEOUT = 40      # full page reload of WhatsApp Web can be slow
VERIFY_ATTEMPTS = 2      # retry once before giving up with "Unverified"


def _dismiss_alert(driver) -> None:
    """Accept a 'Leave site?' / beforeunload popup if one is blocking us."""
    try:
        driver.switch_to.alert.accept()
    except NoAlertPresentException:
        pass
    except Exception:
        pass


def _check_once(driver, phone_clean: str, timeout: int) -> str:
    digits = re.sub(r"\D", "", phone_clean)
    url = f"https://web.whatsapp.com/send?phone={digits}"

    try:
        driver.get(url)
    except UnexpectedAlertPresentException:
        _dismiss_alert(driver)
        try:
            driver.get(url)
        except Exception as e:
            print(f"  [WhatsApp] Navigation failed for {phone_clean}: {e}")
            return "Unverified"
    except Exception as e:
        print(f"  [WhatsApp] Navigation failed for {phone_clean}: {e}")
        return "Unverified"

    # Poll for BOTH outcomes at once, so an invalid-number popup is seen the
    # moment it appears instead of after a long wait for a box that never
    # comes.
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if driver.find_elements(By.XPATH, COMPOSE_XPATH):
                return "Yes"
            if driver.find_elements(By.XPATH, INVALID_XPATH):
                return "No"
        except UnexpectedAlertPresentException:
            _dismiss_alert(driver)
        time.sleep(1)

    return "Unverified"


def check_whatsapp_number(driver, phone_clean: str, timeout: int = VERIFY_TIMEOUT) -> str:
    """
    Navigates to web.whatsapp.com/send?phone=<digits> and inspects the
    result:
      - chat message box (inside <footer>) appears   -> "Yes"
      - WhatsApp's "invalid phone number" popup      -> "No"
      - neither appears in time (after a retry)      -> "Unverified"
    On "Unverified" a screenshot and the visible page text are saved to
    ./data/whatsapp_unverified_<digits>.png/.txt so you can SEE what
    WhatsApp showed (QR code? logged out? a new popup wording?).
    Does NOT send a message or press Enter — read-only check.
    """
    if not phone_clean:
        return "Unverified"

    result = "Unverified"
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        result = _check_once(driver, phone_clean, timeout)
        if result != "Unverified":
            return result
        if attempt < VERIFY_ATTEMPTS:
            print(f"  [WhatsApp] No answer for {phone_clean}, retrying ({attempt}/{VERIFY_ATTEMPTS - 1})...")

    print(f"  [WhatsApp] Couldn't confirm valid/invalid for {phone_clean} — marking Unverified")
    try:
        digits = re.sub(r"\D", "", phone_clean)
        base = os.path.join(DATA_DIR, f"whatsapp_unverified_{digits}")
        driver.save_screenshot(base + ".png")
        page_text = driver.find_element(By.TAG_NAME, "body").text
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(page_text)
        print(f"    Saved {os.path.basename(base)}.png / .txt in data/ to show what WhatsApp displayed.")
    except Exception:
        pass
    return "Unverified"


# ---------------- CSV output ----------------
def save_master_leads(leads: list[dict]):
    write_header = not os.path.exists(MASTER_LEADS_CSV)
    with open(MASTER_LEADS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEAD_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(leads)
    print(f"\nAppended {len(leads)} leads to {MASTER_LEADS_CSV}")


def build_company_profile_rows(leads: list[dict], driver) -> list[dict]:
    """
    For every lead with a usable phone_clean, runs WhatsApp verification
    and includes ALL of them in the output (verified or not) — filtering is
    a decision for context.py / the user downstream, not this stage.
    """
    rows = []
    phoned_leads = [l for l in leads if l.get("phone_clean")]
    total = len(phoned_leads)

    for i, lead in enumerate(phoned_leads, start=1):
        company = lead.get("company_name", "")
        print(f"  [{i}/{total}] {company}")

        print("    -> WhatsApp verification...")
        whatsapp_verified = check_whatsapp_number(driver, lead["phone_clean"])

        rows.append({
            "Company": company,
            "Website": lead.get("website", ""),
            "Email": lead.get("email", ""),
            "Number": lead["phone_clean"],
            "WhatsApp_Verified": whatsapp_verified,
        })

    return rows


def save_company_profile_csv(rows: list[dict]):
    """Overwrites company_profile.csv each run — avoids re-messaging the
    same batch on a re-run (same reasoning the old save_context_csv() used)."""
    fieldnames = ["Company", "Website", "Email", "Number", "WhatsApp_Verified"]
    with open(COMPANY_PROFILE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {COMPANY_PROFILE_CSV} (ready for context.py)")


# ---------------- Main ----------------
def dedupe_leads(leads: list[dict]) -> list[dict]:
    """Drop duplicate leads (same phone, or same website if no phone) that
    can occur when overlapping keywords return the same business twice."""
    seen = set()
    unique = []
    for lead in leads:
        key = lead.get("phone_clean") or lead.get("website") or lead.get("company_name")
        if key in seen:
            continue
        seen.add(key)
        unique.append(lead)
    return unique


def run_pipeline(keywords: list[str], location: str, goal: str, country_code: str,
                  max_per_keyword: int = MAX_RESULTS_PER_KEYWORD):
    all_leads = []

    for idx, keyword in enumerate(keywords, start=1):
        print(f"\n[{idx}/{len(keywords)}] Keyword: '{keyword}'")

        print("  [1/2] Google Maps...")
        maps_leads = search_google_maps(keyword, location, country_code, max_results=max_per_keyword)
        print(f"    -> {len(maps_leads)} leads")

        print("  [2/2] Enriching leads (scraping websites + web search)...")
        # First scrape websites for emails
        email_count = enrich_leads_with_email(maps_leads)
        print(f"    -> {email_count} leads got an email added")
        
        # Then enrich with web search for additional contact info
        enrich_with_web_search(maps_leads, location, country_code)
        print(f"    -> Web search completed for enrichment")

        all_leads.extend(maps_leads)

    all_leads = dedupe_leads(all_leads)
    print(f"\nTotal unique leads across all keywords: {len(all_leads)}")

    save_master_leads(all_leads)

    print("\nVerifying WhatsApp numbers...")
    driver = None
    profile_rows = []
    try:
        driver = build_driver()
        wait_for_login(driver)
        profile_rows = build_company_profile_rows(all_leads, driver)
    finally:
        if driver is not None:
            driver.quit()

    save_company_profile_csv(profile_rows)

    no_phone = len(all_leads) - len(profile_rows)
    if no_phone:
        print(f"Note: {no_phone} lead(s) had no usable phone number and were left out of "
              f"company_profile.csv (they're still in {os.path.basename(MASTER_LEADS_CSV)}).")

    return all_leads, profile_rows


if __name__ == "__main__":
    print("=== Lead Gen -> WhatsApp Company Profile Pipeline ===\n")

    location_input = input("Enter location (e.g. 'Andheri, Mumbai'): ").strip()
    keywords_input = input("Enter keywords, comma-separated (e.g. 'digital marketing agency, interior designer'): ").strip()
    country_code_input = input("Enter the country dial code for these leads' phone numbers "
                                "(e.g. '+91' for India, '+51' for Peru): ").strip()

    keyword_list = [k.strip() for k in keywords_input.split(",") if k.strip()]
    country_code = normalize_country_code(country_code_input)

    if not location_input or not keyword_list:
        print("Location and at least one keyword are required. Exiting.")
    elif not country_code:
        print(f"'{country_code_input}' doesn't look like a valid country dial code "
              f"(e.g. '+91'). Exiting.")
    else:
        run_pipeline(keyword_list, location_input, "", country_code)