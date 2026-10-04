import os
import csv
import time
import random
import urllib.parse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from dotenv import load_dotenv

from ai_provider import generate_text, provider_summary

load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CSV_FILE = r"C:\Users\hp\OneDrive\Desktop\DESKTOP\Programming\Python\Sales Automation\data\sender.csv"

FALLBACK_MESSAGE = None
HARDCODED_FALLBACK_MESSAGE = (
    "Hi! Just reaching out to connect — would love to chat if you're open to it."
)

# Profile lives OUTSIDE OneDrive (OneDrive sync can lock/corrupt Chrome profile
# files, which stops WhatsApp Web from loading its QR code). Lead_Generator.py
# should use this same path so both scripts share one WhatsApp login.
PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "whatsapp_selenium_profile",
)
LOGIN_TIMEOUT = 180
CHAT_LOAD_TIMEOUT = 30

# Chat list present => logged in. Canvas / data-ref => QR code is showing.
LOGGED_IN_XPATH = (
    '//div[@id="pane-side"] | //div[@id="side"] | //div[@aria-label="Chat list"]'
)
QR_XPATH = '//canvas | //div[@data-ref]'
COMPOSE_XPATH = (
    '//footer//div[@contenteditable="true"] | '
    '//div[@contenteditable="true"][@data-tab="10"]'
)


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_contacts(csv_path: str) -> list[dict]:
    """Read sender.csv and return list of {"phone": ..., "prompt": ...} dicts"""
    contacts = []

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Could not find '{csv_path}'.")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            phone = (row.get("Number") or "").strip()
            prompt = (row.get("WhatsApp_Context") or "").strip()

            if not phone:
                print(f"Warning: row {i} has no phone number, skipping.")
                continue

            if not prompt:
                prompt = "Write a short, friendly WhatsApp message under 30 words."

            contacts.append({"phone": phone, "prompt": prompt})

    return contacts


# ---------------------------------------------------------------------------
# AI message generation
# ---------------------------------------------------------------------------

def generate_fallback_message(instruction: str) -> str:
    """Generate a fallback message (Groq first, Google Gemini second)."""
    full_prompt = (
        f"{instruction}. Write this as a single finished WhatsApp message, "
        f"under 30 words, ready to send as-is."
    )

    text, _provider = generate_text(full_prompt)
    if text:
        return text

    print("Using hardcoded fallback message.")
    return HARDCODED_FALLBACK_MESSAGE


def get_ai_message(prompt: str) -> str | None:
    """Generate a personalized message: Groq first, Google Gemini second."""
    variation_hint = random.choice([
        "Make it upbeat.",
        "Make it warm and casual.",
        "Make it playful.",
        "Keep it simple and sincere.",
    ])
    full_prompt = f"{prompt}. Keep it under 30 words, suitable for WhatsApp. {variation_hint}"

    text, _provider = generate_text(full_prompt)
    return text


# ---------------------------------------------------------------------------
# Selenium / WhatsApp Web logic
# ---------------------------------------------------------------------------

def build_driver() -> webdriver.Chrome:
    """Launch Chrome with a persistent profile, hiding obvious automation flags
    (WhatsApp Web can refuse to render the QR code for automated browsers)."""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    options = Options()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--window-size=1200,900")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-popup-blocking")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    try:
        return webdriver.Chrome(options=options)
    except Exception as error:
        print(f"Could not start Chrome: {error}")
        print("Close ALL Chrome windows opened by these scripts (check Task Manager "
              "for stray chrome.exe / chromedriver.exe) and try again.")
        raise


def wait_for_login(driver: webdriver.Chrome) -> None:
    """Wait for WhatsApp Web login, reporting what the page is actually showing."""
    driver.get("https://web.whatsapp.com")
    print("Waiting for WhatsApp Web to load...")

    start = time.time()
    qr_announced = False
    refreshed = False

    while time.time() - start < LOGIN_TIMEOUT:
        if driver.find_elements(By.XPATH, LOGGED_IN_XPATH):
            print("Logged in to WhatsApp Web.")
            time.sleep(2)
            return

        if driver.find_elements(By.XPATH, QR_XPATH) and not qr_announced:
            print(">>> QR code is showing. Scan it with your phone: "
                  "WhatsApp > Settings > Linked devices > Link a device.")
            qr_announced = True

        # Page stuck blank for 40s with nothing recognisable: refresh once.
        if not qr_announced and not refreshed and time.time() - start > 40:
            print("Page looks blank/stuck, refreshing once...")
            driver.refresh()
            refreshed = True

        time.sleep(1)

    # Timed out: save evidence so we can see what WhatsApp actually displayed.
    try:
        driver.save_screenshot("whatsapp_debug.png")
        print(f"Page title: {driver.title!r}")
        print("Saved screenshot to whatsapp_debug.png in the current folder.")
    except Exception:
        pass
    raise TimeoutException("WhatsApp Web did not reach the chat list in time.")


def send_whatsapp_message(driver: webdriver.Chrome, phone: str, message: str) -> bool:
    """Send WhatsApp message"""
    phone_clean = phone.replace("+", "").replace(" ", "").replace("-", "")
    encoded_message = urllib.parse.quote(message)
    url = f"https://web.whatsapp.com/send?phone={phone_clean}&text={encoded_message}"

    driver.get(url)

    try:
        compose_box = WebDriverWait(driver, CHAT_LOAD_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.XPATH, COMPOSE_XPATH)
            )
        )
    except TimeoutException:
        print(f"Could not load chat for {phone}. Skipping.")
        return False

    time.sleep(2)
    compose_box.send_keys(Keys.ENTER)
    time.sleep(3)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"AI setup: {provider_summary()}\n")

    fallback_instruction = input(
        "Briefly describe the fallback message to use if personalization fails "
        "(e.g., 'a friendly generic intro mentioning I sell pens'): "
    ).strip()

    if not fallback_instruction:
        fallback_instruction = "a short, warm, generic WhatsApp introduction message"

    FALLBACK_MESSAGE = generate_fallback_message(fallback_instruction)
    print(f"Fallback message ready: {FALLBACK_MESSAGE}\n")

    contacts = load_contacts(CSV_FILE)

    if not contacts:
        print("No valid contacts found in CSV. Nothing to send.")
    else:
        driver = build_driver()
        try:
            wait_for_login(driver)

            for i, contact in enumerate(contacts, start=1):
                phone = contact["phone"]
                prompt = contact["prompt"]

                print(f"[{phone}] ({i}/{len(contacts)}) Generating message...")
                message = get_ai_message(prompt)

                if message is None:
                    message = FALLBACK_MESSAGE
                    print(f"[{phone}] AI generation failed — using fallback message.")

                print(f"[{phone}] Generated: {message}")

                sent = send_whatsapp_message(driver, phone, message)
                print(f"[{phone}] {'✓ Sent' if sent else '✗ FAILED'}\n")

            print("Done!")
        finally:
            time.sleep(3)
            driver.quit()
