"""
Email bulk sender with AI-generated messages (Groq primary, Google Gemini fallback).
Sends personalized cold emails to leads found by Lead_Generator.py,
using personalized context from context.py.

Requirements:
    pip install openai python-dotenv
    (also needs ai_provider.py in this same folder)

Setup:
    Set your API keys in a .env file in this same folder:
        GROQ_API_KEY=your-groq-key-here          (primary, message generation)
        GOOGLE_API_KEY=your-google-key-here      (fallback if Groq fails)
        EMAIL_SENDER=your-email@gmail.com         (your email address)
        EMAIL_PASSWORD=your-app-password-here     (Gmail app password or SMTP password)
        EMAIL_SMTP_HOST=smtp.gmail.com            (Gmail SMTP, or your provider's)
        EMAIL_SMTP_PORT=587                       (Gmail port, adjust for your provider)
    
    Get free keys at: console.groq.com and aistudio.google.com
    
    For Gmail:
        - Enable 2-factor authentication on your account
        - Create an App Password: https://myaccount.google.com/apppasswords
        - Use that 16-character password as EMAIL_PASSWORD
    
    For other providers (Outlook, SendGrid, etc.), adjust SMTP_HOST and PORT accordingly.

CSV format (sender.csv, produced by context.py):
    Company,WhatsApp_Context,Email_Context,Number,Email
    Acme Co,"Write a short, warm 'Hi' message...",
            "Write a cold email for a B2B SaaS pitch...",+919819042429,acme@example.com
    Beta Inc,"Write a short, warm 'Hi' message...",
            "Write a cold email for a B2B SaaS pitch...",+919323096918,beta@example.com

This script reads:
    - Company: for logging and email headers
    - Email_Context: personalized instruction for AI to draft email body
    - Email: recipient email address
    
It ignores WhatsApp_Context and Number (those are for whatapp_sender.py).
"""

import os
import csv
import time
import smtplib
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from dotenv import load_dotenv

from ai_provider import generate_text, provider_summary

# Load variables from a .env file
load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CSV_FILE = r"C:\Users\hp\OneDrive\Desktop\DESKTOP\Programming\Python\Sales Automation\data\sender.csv"

# Email configuration
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))

# Generated once, before the sending loop starts
FALLBACK_EMAIL_BODY = None

# Hardcoded literal if fallback generation fails
HARDCODED_FALLBACK_BODY = """Hi there,

I came across your company and thought we might be able to work together. 
I'd love to connect and explore potential collaboration opportunities.

Looking forward to hearing from you!

Best regards"""

# Delay between emails (in seconds) to avoid rate limiting
EMAIL_DELAY = (5, 15)  # random delay between 5-15 seconds


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_contacts(csv_path: str) -> list[dict]:
    """
    Read sender.csv and return a list of {"company": ..., "email": ..., "prompt": ...}
    dicts, pulling email from `Email` and the personalization instruction from
    `Email_Context`. `WhatsApp_Context` and `Number` are ignored here — 
    they're for whatapp_sender.py.
    """
    contacts = []

    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"Could not find '{csv_path}'. Make sure context.py has been run first."
        )

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            email = (row.get("Email") or "").strip()
            company = (row.get("Company") or "").strip()
            prompt = (row.get("Email_Context") or "").strip()

            if not email:
                print(f"Warning: row {i} ({company}) has no email address, skipping.")
                continue

            if not prompt:
                print(f"Warning: row {i} ({email}) has no email context, using generic message.")
                prompt = "Write a friendly cold email opener under 100 words."

            contacts.append({
                "email": email,
                "company": company,
                "prompt": prompt,
            })

    return contacts


# ---------------------------------------------------------------------------
# AI message generation
# ---------------------------------------------------------------------------

def generate_fallback_email(instruction: str) -> str:
    """
    One-time call to generate a fallback email body used when personalization
    fails for a contact. Unlike per-contact prompts, this is a finished email
    ready to send.

    Tries Groq, then Google Gemini. If both fail, returns the hardcoded
    literal so the run can still start.
    """
    full_prompt = (
        f"{instruction}. Write this as a complete, professional email body "
        f"(no subject line, no greeting, just the body text), under 100 words, "
        f"ready to send as-is. Make it warm and personable."
    )

    text, _provider = generate_text(full_prompt)
    if text:
        return text

    print("Both AI providers failed generating the fallback email. Using hardcoded fallback.")
    return HARDCODED_FALLBACK_BODY


def get_ai_email_body(prompt: str) -> str | None:
    """
    Generate a personalized email body (Groq first, Google Gemini second).

    Returns the generated text, or None if both providers fail.
    Caller should use FALLBACK_EMAIL_BODY on failure.
    """
    variation_hint = random.choice([
        "Keep it professional but warm.",
        "Make it conversational and genuine.",
        "Be direct and respectful.",
        "Write in a friendly, approachable tone.",
    ])

    full_prompt = (
        f"{prompt}. Write this as a complete email body (no subject line, "
        f"no greeting/closing, just the body), under 100 words, ready to send. "
        f"{variation_hint}"
    )

    text, _provider = generate_text(full_prompt)
    return text


def get_ai_email_subject(company: str, email_body: str) -> str | None:
    """
    Generate a subject line based on the company name and email body.
    This is a separate call so the subject is always contextual.

    Returns subject line, or None if both providers fail.
    """
    prompt = (
        f"Write a short, professional email subject line (under 10 words) "
        f"for an outreach email to {company}. Make it intriguing but not clickbait. "
        f"Just the subject line, nothing else."
    )

    text, _provider = generate_text(prompt)
    return text


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(recipient_email: str, subject: str, body: str, sender_name: str = "") -> bool:
    """
    Send an email via SMTP. Returns True if sent successfully, False otherwise.
    """
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        print(f"[{recipient_email}] EMAIL_SENDER or EMAIL_PASSWORD not set in .env. Cannot send.")
        return False

    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = recipient_email

        # Create plain text part
        text_part = MIMEText(body, "plain")
        msg.attach(text_part)

        # Connect to SMTP server and send
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()  # Encrypt connection
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, [recipient_email], msg.as_string())

        print(f"[{recipient_email}] Email sent successfully.")
        return True

    except Exception as error:
        print(f"[{recipient_email}] Failed to send email: {error}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Verify configuration
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        print("ERROR: EMAIL_SENDER or EMAIL_PASSWORD not set in .env file.")
        print("Setup instructions are at the top of this script.")
        exit(1)

    print(f"AI setup: {provider_summary()}\n")

    fallback_instruction = input(
        "Briefly describe the fallback email to use if personalization fails "
        "(e.g., 'a friendly cold email introducing my SaaS product'): "
    ).strip()

    if not fallback_instruction:
        fallback_instruction = "a professional but friendly cold email introduction"

    FALLBACK_EMAIL_BODY = generate_fallback_email(fallback_instruction)
    print(f"Fallback email body ready.\n")

    # Load contacts
    try:
        contacts = load_contacts(CSV_FILE)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        print("Make sure context.py has been run first.")
        exit(1)

    if not contacts:
        print("No valid contacts found in CSV. Nothing to send.")
    else:
        sent_count = 0
        failed_count = 0

        print(f"Ready to send {len(contacts)} emails.\n")

        for i, contact in enumerate(contacts, start=1):
            recipient_email = contact["email"]
            company = contact["company"]
            prompt = contact["prompt"]

            print(f"[{i}/{len(contacts)}] {company} ({recipient_email})")

            # Generate email body
            print("  -> Generating email body...")
            body = get_ai_email_body(prompt)

            if body is None:
                body = FALLBACK_EMAIL_BODY
                print("  -> Personalization failed, using fallback email body.")

            print(f"  -> Generating subject line...")
            subject = get_ai_email_subject(company, body)

            if subject is None:
                subject = f"Collaboration Opportunity"
                print("  -> Could not generate subject line, using generic.")

            print(f"  -> Subject: {subject}")
            print(f"  -> Body preview: {body[:60]}...")

            # Send email
            print("  -> Sending...")
            success = send_email(recipient_email, subject, body)

            if success:
                sent_count += 1
            else:
                failed_count += 1

            # Delay before next email to avoid rate limiting
            if i < len(contacts):
                delay = random.uniform(*EMAIL_DELAY)
                print(f"  -> Waiting {delay:.1f}s before next email...\n")
                time.sleep(delay)

        print(f"\n{'='*60}")
        print(f"Done! Sent {sent_count}/{len(contacts)} emails.")
        if failed_count > 0:
            print(f"Failed: {failed_count}")
        print(f"{'='*60}")