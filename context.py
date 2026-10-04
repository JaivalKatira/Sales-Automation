import csv
import os
import random
import time

from ai_provider import generate_text, provider_summary

# ---------------- Config ----------------
# Set to True to also generate context for leads Stage 1 judged a poor
# vertical fit ("Vertical_Fit" == "No"). Off by default - those leads were
# already flagged as unlikely to be worth messaging.
INCLUDE_NON_FIT_LEADS = False

# Pause between AI calls to stay comfortably under free-tier rate limits.
AI_DELAY_RANGE = (2, 4)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
COMPANY_PROFILE_CSV = os.path.join(DATA_DIR, "company_profile.csv")
SENDER_CSV = os.path.join(DATA_DIR, "sender.csv")

SENDER_FIELDNAMES = ["Company", "WhatsApp_Context", "Email_Context", "Number", "Email"]

# ---------------- Change 2: generic fallback line ----------------
def build_fallback_line(goal: str, channel: str) -> str:
    """
    Returns a generic, goal-based instruction (NOT a finished message — see
    the contract note at the top of this file) to use when both the
    Groq and Google calls fail for a given lead. Depends only on `goal`
    and `channel`, not on any per-lead data — it's the safety net when a
    lead-specific call can't be made at all.

    `channel` is "whatsapp" or "email".
    """
    if channel == "email":
        return (
            f"Write a short, warm, professional email opening paragraph "
            f"introducing myself in the context of: {goal}. Keep it under "
            f"40 words."
        )
    return (
        f"Write a short, friendly WhatsApp message introducing myself in the "
        f"context of: {goal}. Keep it warm and under 30 words."
    )


# ---------------- Prompt construction ----------------
def _build_prompt(company: str, fit_notes: str, goal: str, channel: str) -> str:
    """
    `channel` is "whatsapp" or "email" — selects which outreach instruction
    to ask the model for. Both branches produce an INSTRUCTION for another
    AI to follow, never a finished message (see the contract note at the
    top of this file).
    """
    if channel == "email":
        medium_desc = "a short, warm, professional cold-email opening paragraph"
        format_hint = (
            "Respond with ONLY the single instruction sentence (something like "
            "'Write a short, warm email opener mentioning ... in a "
            "professional but friendly tone, under 40 words.'). No preamble, "
            "no quotes, no markdown."
        )
    else:
        medium_desc = "a short, warm WhatsApp message"
        format_hint = (
            "Respond with ONLY the single instruction sentence (something like "
            "'Write a short, warm WhatsApp message mentioning ... in a "
            "friendly tone, under 30 words.'). No preamble, no quotes, no "
            "markdown."
        )

    return (
        f"You write a short INSTRUCTION for another AI to follow when it "
        f"drafts {medium_desc} — you do NOT write the message itself. "
        "The instruction should tell that AI to write a message that ties "
        "this specific lead's business to the stated goal, grounded ONLY "
        "in the fit notes below. Do not invent or assume any specific "
        "facts about the company beyond what's in the fit notes.\n\n"
        f"Goal: {goal}\n"
        f"Company: {company}\n"
        f"Fit notes: {fit_notes or '(none provided)'}\n\n"
        f"{format_hint}"
    )


def _extract_text(response) -> str:
    text = (response.choices[0].message.content or "").strip()
    return text.strip('"').strip("'").strip()


# ---------------- Personalized context generation ----------------
def generate_context(lead: dict, goal: str, channel: str) -> tuple[str, str]:
    """
    Three-tier fallback for a single channel ("whatsapp" or "email").
    Never raises - always returns a usable string, so one bad lead never
    kills the run.

    Returns (context_text, tier) where tier is one of
    "groq" / "google" / "fallback", so the caller can report which tier
    handled each lead/channel.
    """
    company = lead.get("Company") or "this business"
    fit_notes = lead.get("Fit_Notes") or ""
    prompt = _build_prompt(company, fit_notes, goal, channel)

    text, provider = generate_text(prompt, temperature=0.4)
    if text:
        return text, provider

    print(f"  Both Groq and Google failed for '{company}' ({channel}) - using generic fallback line.")
    return build_fallback_line(goal, channel), "fallback"


# ---------------- Change 3: filtering ----------------
def load_company_profile(path: str) -> list[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Could not find '{path}'. Run Lead_Generator.py first to produce it."
        )
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def filter_leads(rows: list[dict]) -> tuple[list[dict], int, int]:
    """
    Returns (eligible_rows, skipped_not_whatsapp, skipped_poor_fit).
      - Skips WhatsApp_Verified == "No" outright (never worth an API call).
      - Skips Vertical_Fit == "No" unless INCLUDE_NON_FIT_LEADS is True.
      - Keeps "Unverified" / "Uncertain" — those proceed normally.
    """
    eligible = []
    skipped_not_whatsapp = 0
    skipped_poor_fit = 0

    for row in rows:
        whatsapp_verified = (row.get("WhatsApp_Verified") or "").strip()
        vertical_fit = (row.get("Vertical_Fit") or "").strip()

        if whatsapp_verified == "No":
            skipped_not_whatsapp += 1
            continue

        if vertical_fit == "No" and not INCLUDE_NON_FIT_LEADS:
            skipped_poor_fit += 1
            continue

        eligible.append(row)

    return eligible, skipped_not_whatsapp, skipped_poor_fit


# ---------------- Change 5: output ----------------
def build_whatsapp_context_rows(eligible_leads: list[dict], goal: str) -> tuple[list[dict], dict]:
    """
    For each eligible lead, generates BOTH a WhatsApp_Context instruction
    and an Email_Context instruction (two separate tiered AI calls) and
    assembles the output row. Also tallies which tier (groq / google /
    fallback) handled each channel call, for the end-of-run summary.
    """
    rows = []
    tier_counts = {
        "whatsapp": {"groq": 0, "google": 0, "fallback": 0},
        "email": {"groq": 0, "google": 0, "fallback": 0},
    }
    total = len(eligible_leads)

    for i, lead in enumerate(eligible_leads, start=1):
        company = lead.get("Company", "")
        print(f"[{i}/{total}] {company}")

        whatsapp_text, whatsapp_tier = generate_context(lead, goal, "whatsapp")
        tier_counts["whatsapp"][whatsapp_tier] += 1

        # Spacing between the two per-lead channel calls — keeps us under
        # the provider's rate cap now that each lead makes two calls.
        time.sleep(random.uniform(*AI_DELAY_RANGE))

        email_text, email_tier = generate_context(lead, goal, "email")
        tier_counts["email"][email_tier] += 1

        rows.append({
            "Company": company,
            "WhatsApp_Context": whatsapp_text,
            "Email_Context": email_text,
            "Number": lead.get("Number", ""),
            "Email": lead.get("Email", ""),
        })

        # Light spacing between leads regardless of which tier handled the
        # last call — keeps us comfortably under the rate cap.
        time.sleep(random.uniform(*AI_DELAY_RANGE))

    return rows, tier_counts


def save_sender_csv(rows: list[dict]):
    """Overwrites sender.csv each run — same reasoning as the rest of the
    pipeline: avoids re-messaging the same batch on a re-run."""
    with open(SENDER_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SENDER_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {SENDER_CSV}")


# ---------------- Main ----------------
if __name__ == "__main__":
    print("=== Context Generation (Groq -> Google Gemini -> generic fallback) ===")
    print(f"AI setup: {provider_summary()}\n")

    goal_input = input(
        "What's your final goal for these leads? (used both to guide the "
        "personalized messages and as a fallback line if the AI call "
        "fails): "
    ).strip()

    if not goal_input:
        print("A goal is required. Exiting.")
    else:
        profile_rows = load_company_profile(COMPANY_PROFILE_CSV)
        eligible, skipped_not_whatsapp, skipped_poor_fit = filter_leads(profile_rows)

        print(
            f"\n{len(eligible)} leads eligible, {skipped_not_whatsapp} skipped "
            f"(not on WhatsApp), {skipped_poor_fit} skipped (poor vertical fit)."
        )

        if not eligible:
            print("Nothing to do — no eligible leads.")
        else:
            print("\nGenerating personalized context...")
            context_rows, tier_counts = build_whatsapp_context_rows(eligible, goal_input)
            save_sender_csv(context_rows)

            wa = tier_counts["whatsapp"]
            em = tier_counts["email"]
            print(
                f"\nDone. Processed {len(context_rows)} leads (2 contexts each).\n"
                f"  WhatsApp_Context: {wa['groq']} via Groq, "
                f"{wa['google']} via Google fallback, {wa['fallback']} via generic fallback.\n"
                f"  Email_Context:    {em['groq']} via Groq, "
                f"{em['google']} via Google fallback, {em['fallback']} via generic fallback."
            )
