"""
build.py - runs the whole sales pipeline in order
==================================================

    1. Lead_Generator.py   -> data/company_profile.csv
    2. context.py          -> data/sender.csv
    3. whatapp_sender.py   -> asks first (Y/n), then sends WhatsApp messages
    4. email_sender.py     -> asks first (Y/n), then sends emails

Put this file in the SAME folder as the other scripts and run:
    python build.py

Optional flags (handy when re-running after a failure):
    python build.py --skip-leads      # reuse existing company_profile.csv
    python build.py --skip-context    # reuse existing sender.csv
    python build.py --skip-leads --skip-context   # jump straight to sending

Each script still asks its own questions (location, keywords, goal, ...)
because build.py simply runs them one after another in this same terminal.
"""

import argparse
import csv
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
COMPANY_PROFILE_CSV = os.path.join(DATA_DIR, "company_profile.csv")
SENDER_CSV = os.path.join(DATA_DIR, "sender.csv")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_script(script_name: str) -> bool:
    """Run one pipeline script in this terminal. True if it exited cleanly."""
    path = os.path.join(BASE_DIR, script_name)
    if not os.path.exists(path):
        print(f"ERROR: {script_name} not found next to build.py.")
        return False

    print(f"\n{'=' * 60}\n>>> Running {script_name}\n{'=' * 60}\n")
    # Same Python that is running build.py; cwd = script folder so relative
    # paths (data/, .env) resolve the same way as running them by hand.
    result = subprocess.run([sys.executable, path], cwd=BASE_DIR)

    if result.returncode != 0:
        print(f"\n{script_name} stopped with an error (exit code {result.returncode}).")
        return False
    return True


def confirm(question: str) -> bool:
    """Ask a Y/n question. Needs an explicit answer, so a stray Enter
    can never trigger a bulk send."""
    while True:
        answer = input(f"{question} (Y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please type Y or n.")


def count_recipients() -> tuple[int, int]:
    """Return (rows_with_phone_number, rows_with_email) in sender.csv."""
    whatsapp = email = 0
    with open(SENDER_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if (row.get("Number") or "").strip():
                whatsapp += 1
            if (row.get("Email") or "").strip():
                email += 1
    return whatsapp, email


def stop_if_missing(path: str, made_by: str) -> None:
    if not os.path.exists(path):
        print(f"ERROR: {os.path.relpath(path, BASE_DIR)} not found. "
              f"It is created by {made_by}; run that stage first.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run the sales automation pipeline.")
    parser.add_argument("--skip-leads", action="store_true",
                        help="skip Lead_Generator.py (reuse existing company_profile.csv)")
    parser.add_argument("--skip-context", action="store_true",
                        help="skip context.py (reuse existing sender.csv)")
    args = parser.parse_args()

    print("=== Sales Automation Pipeline ===")

    # Stage 1: lead generation
    if args.skip_leads:
        print("\nSkipping lead generation (--skip-leads).")
    elif not run_script("Lead_Generator.py"):
        sys.exit(1)

    # Stage 2: context generation
    if args.skip_context:
        print("\nSkipping context generation (--skip-context).")
    else:
        stop_if_missing(COMPANY_PROFILE_CSV, "Lead_Generator.py")
        if not run_script("context.py"):
            sys.exit(1)

    stop_if_missing(SENDER_CSV, "context.py")
    whatsapp_count, email_count = count_recipients()
    print(f"\nsender.csv is ready: {whatsapp_count} WhatsApp number(s), "
          f"{email_count} email address(es).")

    # Stage 3: WhatsApp (asks first)
    print()
    if whatsapp_count == 0:
        print("No WhatsApp numbers to message, skipping WhatsApp.")
    elif confirm(f"Send WhatsApp messages to {whatsapp_count} lead(s) now?"):
        if not run_script("whatapp_sender.py"):
            print("WhatsApp stage failed; moving on to the email question.")
    else:
        print("Skipped WhatsApp sending.")

    # Stage 4: email (asks first)
    print()
    if email_count == 0:
        print("No email addresses to message, skipping email.")
    elif confirm(f"Send emails to {email_count} lead(s) now?"):
        if not run_script("email_sender.py"):
            print("Email stage failed.")
    else:
        print("Skipped email sending.")

    print("\n=== Pipeline finished ===")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(130)