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

from openai import OpenAI
from dotenv import load_dotenv

# Load variables from a .env file (in the same folder as this script) into
# the environment, so NVIDIA_API_KEY etc. don't need to be set manually in
# every terminal session.
load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CSV_FILE = r"C:\Users\hp\OneDrive\Desktop\DESKTOP\Programming\Python\Sales Automation\data\sender.csv"

# TEMPORARY: swapped to Groq to test whether NIM itself is the bottleneck.
# Swap back to the NVIDIA NIM block below once confirmed.
nim_client = OpenAI(
    api_key=os.environ.get("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
    timeout=30.0,   # seconds — fail fast on a slow/stalled connection instead of hanging
    max_retries=1,  # one retry on transient errors, then give up
)
NIM_MODEL = os.environ.get("NIM_MODEL", "llama-3.3-70b-versatile")

# --- Original NVIDIA NIM config (commented out for now) ---
# nim_client = OpenAI(
#     api_key=os.environ.get("NVIDIA_API_KEY"),
#     base_url="https://integrate.api.nvidia.com/v1",
#     timeout=30.0,
#     max_retries=1,
# )
# NIM_MODEL = os.environ.get("NIM_MODEL", "meta/llama-3.3-70b-instruct")

# Generated once, before the sending loop starts, by generate_fallback_message().
# Used per-contact whenever get_ai_message() fails for that contact.
FALLBACK_MESSAGE = None

# Hardcoded literal used only if generate_fallback_message() itself fails
# (e.g. NIM is down at startup). Never let that failure block the script
# from starting.
HARDCODED_FALLBACK_MESSAGE = (
    "Hi! Just reaching out to connect — would love to chat if you're open to it."
)

# Folder where the browser session (login) is saved, so you only scan the
# WhatsApp QR code once, not on every run.
PROFILE_DIR = os.path.join(os.getcwd(), "whatsapp_selenium_profile")

# How long to wait (seconds) for WhatsApp Web to log in / load a chat
LOGIN_TIMEOUT = 90
CHAT_LOAD_TIMEOUT = 30


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_contacts(csv_path: str) -> list[dict]:
    """Read sender.csv (produced by context.py) and return a list of
    {"phone": ..., "prompt": ...} dicts, pulling the phone number from
    `Number` and the outreach instruction from `WhatsApp_Context`.
    `Email_Context` and `Email` columns, if present, are ignored here —
    they're for a separate email sender."""
    contacts = []

    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"Could not find '{csv_path}'. Make sure it's in the same folder as this script."
        )

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            phone = (row.get("Number") or "").strip()
            prompt = (row.get("WhatsApp_Context") or "").strip()

            if not phone:
                print(f"Warning: row {i} has no phone number, skipping.")
                continue

            if not prompt:
                print(f"Warning: row {i} ({phone}) has no context/prompt, using a generic message.")
                prompt = "Write a short, friendly WhatsApp message under 30 words."

            contacts.append({"phone": phone, "prompt": prompt})

    return contacts


# ---------------------------------------------------------------------------
# AI message generation
# ---------------------------------------------------------------------------

def generate_fallback_message(instruction: str) -> str:
    """
    One-time call (not per-contact) to the NIM client used for personalization.
    Unlike the per-contact Context prompts used elsewhere in this pipeline,
    this one asks for a finished, ready-to-send message -- this is the last
    tier, nothing downstream rewrites it.

    On failure (API error, empty response), falls back to a hardcoded literal
    string so the run can still start. Never let a failure here block the
    script from starting.
    """
    api_key = os.environ.get("GROQ_API_KEY")  # TEMPORARY: testing with Groq
    if not api_key:
        print("Warning: GROQ_API_KEY environment variable not set. Using hardcoded fallback message.")
        return HARDCODED_FALLBACK_MESSAGE

    try:
        full_prompt = (
            f"{instruction}. Write this as a single finished WhatsApp message, "
            f"under 30 words, ready to send as-is."
        )

        response = nim_client.chat.completions.create(
            model=NIM_MODEL,
            messages=[{"role": "user", "content": full_prompt}],
        )

        text = (response.choices[0].message.content or "").strip()
        if not text:
            print("NIM returned an empty response for the fallback message. Using hardcoded fallback message.")
            return HARDCODED_FALLBACK_MESSAGE

        text = text.strip('"').strip("'").strip()
        return text

    except Exception as error:
        print(f"NIM API request failed while generating the fallback message: {error}")
        print("Using hardcoded fallback message.")
        return HARDCODED_FALLBACK_MESSAGE


def get_ai_message(prompt: str) -> str | None:
    """
    Generate a short WhatsApp-friendly message using NVIDIA NIM.

    Returns the generated text, or None if the API call fails for any
    reason. When this returns None, the caller should use FALLBACK_MESSAGE
    (generated once, up front, by generate_fallback_message()) rather than
    skipping the contact or calling the AI again.
    """
    api_key = os.environ.get("GROQ_API_KEY")  # TEMPORARY: testing with Groq
    if not api_key:
        print("Warning: GROQ_API_KEY environment variable not set.")
        return None

    try:
        variation_hint = random.choice([
            "Make it upbeat.",
            "Make it warm and casual.",
            "Make it playful.",
            "Keep it simple and sincere.",
        ])
        full_prompt = f"{prompt}. Keep it under 30 words, suitable for WhatsApp. {variation_hint}"

        response = nim_client.chat.completions.create(
            model=NIM_MODEL,
            messages=[{"role": "user", "content": full_prompt}],
        )

        text = (response.choices[0].message.content or "").strip()
        if not text:
            print("NIM returned an empty response.")
            return None

        text = text.strip('"').strip("'").strip()
        return text

    except Exception as error:
        print(f"NIM API request failed: {error}")
        return None


# ---------------------------------------------------------------------------
# Selenium / WhatsApp Web logic
# ---------------------------------------------------------------------------

def build_driver() -> webdriver.Chrome:
    """Launch Chrome with a persistent profile so the login session is reused."""
    options = Options()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    # Keeps the window reasonably sized and visible so you can scan the QR code
    options.add_argument("--window-size=1200,900")

    driver = webdriver.Chrome(options=options)
    return driver


def wait_for_login(driver: webdriver.Chrome) -> None:
    """Open WhatsApp Web and wait until the chat list is visible (i.e. logged in)."""
    driver.get("https://web.whatsapp.com")

    print("Waiting for WhatsApp Web to load. If a QR code appears, scan it now...")
    WebDriverWait(driver, LOGIN_TIMEOUT).until(
        EC.presence_of_element_located((By.XPATH, '//div[@id="side"]'))
    )
    print("Logged in to WhatsApp Web.")


def send_whatsapp_message(driver: webdriver.Chrome, phone: str, message: str) -> bool:
    """
    Navigate to a chat with the given phone number (pre-filled with `message`)
    in the SAME browser tab, and send it by pressing Enter.

    Returns True if the message appeared to send, False otherwise.
    """
    phone_clean = phone.replace("+", "").replace(" ", "").replace("-", "")
    encoded_message = urllib.parse.quote(message)
    url = f"https://web.whatsapp.com/send?phone={phone_clean}&text={encoded_message}"

    driver.get(url)

    try:
        # Wait for the message compose box to appear (chat has loaded)
        compose_box = WebDriverWait(driver, CHAT_LOAD_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.XPATH, '//footer//div[@contenteditable="true"]')
            )
        )
    except TimeoutException:
        print(f"Could not load chat for {phone} (invalid number or WhatsApp didn't load in time). Skipping.")
        return False

    # Give WhatsApp a moment to finish injecting the pre-filled text
    time.sleep(2)

    compose_box.send_keys(Keys.ENTER)

    # Brief pause to let the send request go through before navigating away
    time.sleep(3)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fallback_instruction = input(
        "Briefly describe the fallback message to use if personalization fails "
        "for a contact (e.g. 'a friendly generic intro mentioning I sell pens'): "
    ).strip()

    if not fallback_instruction:
        fallback_instruction = "a short, warm, generic WhatsApp introduction message"

    FALLBACK_MESSAGE = generate_fallback_message(fallback_instruction)
    print(f"Fallback message ready: {FALLBACK_MESSAGE}")

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
                    print(f"[{phone}] Personalization failed — using fallback message.")

                print(f"[{phone}] Generated message: {message}")

                sent = send_whatsapp_message(driver, phone, message)
                print(f"[{phone}] {'Sent' if sent else 'FAILED to send'}")

            print("Done!")
        finally:
            # Keep the browser open briefly so you can visually confirm the last send
            time.sleep(3)
            driver.quit()
