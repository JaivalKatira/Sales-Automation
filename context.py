import csv
import os
import random
import time

from dotenv import load_dotenv
from openai import OpenAI

# Both Cerebras and Groq expose OpenAI-compatible endpoints, so one client
# pattern (the `openai` SDK pointed at different base_urls) covers both
# instead of pulling in two separate vendor SDKs.

load_dotenv()

# ---------------- Config ----------------
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
# NOTE: Cerebras retired llama-3.3-70b from its public API (it now 404s with
# model_not_found). As of this writing their public catalog is gpt-oss-120b
# (production) plus preview models gemma-4-31b / zai-glm-4.7. gpt-oss-120b is
# the stable pick. If you hit model_not_found again, check
# https://inference-docs.cerebras.ai/models/overview for the current list and
# set CEREBRAS_MODEL in .env rather than editing this default.
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

# Reuses the same GROQ_API_KEY / GROQ_MODEL variable names Lead_Generator.py
# already reads from .env — same key, same quota.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Set to True to also generate context for leads Stage 1 judged a poor
# vertical fit ("Vertical_Fit" == "No"). Off by default — those leads were
# already flagged as unlikely to be worth messaging.
INCLUDE_NON_FIT_LEADS = False

# Cerebras tier (primary). The original spec assumed a 30 RPM model; with
# llama-3.3-70b gone, gpt-oss-120b's free-tier limit is reported as low as
# ~5 RPM in places, so spacing/backoff here are a bit more conservative than
# the original (1,2)/15s. If your dashboard's Limits page shows something
# more generous, feel free to tighten these back up.
CEREBRAS_DELAY_RANGE = (3, 5)
CEREBRAS_MAX_RETRIES = 2
CEREBRAS_RATE_LIMIT_BACKOFF = 20

# Groq tier (fallback, single-shot). Same constants Lead_Generator.py
# already uses for its own Groq calls — reused rather than reinvented,
# since it's the same quota/tier.
GROQ_DELAY_RANGE = (2, 4)
GROQ_MAX_RETRIES = 2
GROQ_RATE_LIMIT_BACKOFF = 20

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
COMPANY_PROFILE_CSV = os.path.join(DATA_DIR, "company_profile.csv")
SENDER_CSV = os.path.join(DATA_DIR, "sender.csv")

SENDER_FIELDNAMES = ["Company", "WhatsApp_Context", "Email_Context", "Number", "Email"]

_cerebras_client = None
_groq_client = None


def _get_cerebras_client():
    """Lazily build a single reused Cerebras client (OpenAI-compatible)."""
    global _cerebras_client
    if _cerebras_client is None:
        if not CEREBRAS_API_KEY:
            return None
        _cerebras_client = OpenAI(api_key=CEREBRAS_API_KEY, base_url="https://api.cerebras.ai/v1")
    return _cerebras_client


def _get_groq_client():
    """Lazily build a single reused Groq client (OpenAI-compatible)."""
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            return None
        _groq_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    return _groq_client


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "rate_limit" in msg or "429" in msg


# ---------------- Change 2: generic fallback line ----------------
def build_fallback_line(goal: str, channel: str) -> str:
    """
    Returns a generic, goal-based instruction (NOT a finished message — see
    the contract note at the top of this file) to use when both the
    Cerebras and Groq calls fail for a given lead. Depends only on `goal`
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
            "professional but friendly tone, under 100 words.'). No preamble, "
            "no quotes, no markdown."
        )
    else:
        medium_desc = "a short, warm WhatsApp message"
        format_hint = (
            "Respond with ONLY the single instruction sentence (something like "
            "'Write a short, warm WhatsApp message mentioning ... in a "
            "friendly tone, under 60 words.'). No preamble, no quotes, no "
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


# ---------------- Change 4: personalized context generation ----------------
def _call_cerebras(prompt: str, company: str) -> str | None:
    client = _get_cerebras_client()
    if client is None:
        print("  [Cerebras] Skipped — no CEREBRAS_API_KEY set in .env")
        return None

    for attempt in range(1, CEREBRAS_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=CEREBRAS_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )
            text = _extract_text(response)
            if text:
                return text
            print(f"  [Cerebras] Empty response for '{company}' (attempt {attempt}/{CEREBRAS_MAX_RETRIES})")
        except Exception as e:
            if _is_rate_limit_error(e):
                if attempt < CEREBRAS_MAX_RETRIES:
                    print(f"  [Cerebras] Rate limited on '{company}' (attempt {attempt}/{CEREBRAS_MAX_RETRIES}), "
                          f"waiting {CEREBRAS_RATE_LIMIT_BACKOFF}s...")
                    time.sleep(CEREBRAS_RATE_LIMIT_BACKOFF)
                    continue
                print(f"  [Cerebras] Still rate limited on '{company}' after {CEREBRAS_MAX_RETRIES} attempts.")
                return None
            print(f"  [Cerebras] Failed for '{company}': {e}")
            return None

        time.sleep(random.uniform(*CEREBRAS_DELAY_RANGE))

    return None


def _call_groq_once(prompt: str, company: str) -> str | None:
    client = _get_groq_client()
    if client is None:
        print("  [Groq fallback] Skipped — no GROQ_API_KEY set in .env")
        return None

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        text = _extract_text(response)
        if text:
            return text
        print(f"  [Groq fallback] Empty response for '{company}'")
        return None
    except Exception as e:
        if _is_rate_limit_error(e):
            print(f"  [Groq fallback] Rate limited for '{company}': {e}")
        else:
            print(f"  [Groq fallback] Failed for '{company}': {e}")
        return None


def generate_context(lead: dict, goal: str, channel: str) -> tuple[str, str]:
    """
    Three-tier fallback for a single channel ("whatsapp" or "email").
    Never raises — always returns a usable string, so one bad lead never
    kills the run.

    Returns (context_text, tier) where tier is one of
    "cerebras" / "groq" / "fallback", so the caller can report which tier
    handled each lead/channel.
    """
    company = lead.get("Company") or "this business"
    fit_notes = lead.get("Fit_Notes") or ""
    prompt = _build_prompt(company, fit_notes, goal, channel)

    text = _call_cerebras(prompt, company)
    if text:
        return text, "cerebras"

    print(f"  [Cerebras] Exhausted for '{company}' ({channel}) — falling back to Groq...")
    time.sleep(random.uniform(*GROQ_DELAY_RANGE))
    text = _call_groq_once(prompt, company)
    if text:
        return text, "groq"

    print(f"  [Groq fallback] Also failed for '{company}' ({channel}) — using generic fallback line.")
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
    assembles the output row. Also tallies which tier (cerebras / groq /
    fallback) handled each channel call, for the end-of-run summary.
    """
    rows = []
    tier_counts = {
        "whatsapp": {"cerebras": 0, "groq": 0, "fallback": 0},
        "email": {"cerebras": 0, "groq": 0, "fallback": 0},
    }
    total = len(eligible_leads)

    for i, lead in enumerate(eligible_leads, start=1):
        company = lead.get("Company", "")
        print(f"[{i}/{total}] {company}")

        whatsapp_text, whatsapp_tier = generate_context(lead, goal, "whatsapp")
        tier_counts["whatsapp"][whatsapp_tier] += 1

        # Spacing between the two per-lead channel calls — same reasoning
        # as the spacing between leads below, keeps us under Cerebras's
        # rate cap now that each lead makes two calls instead of one.
        time.sleep(random.uniform(*CEREBRAS_DELAY_RANGE))

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
        # last call — keeps us comfortably under Cerebras's rate cap.
        time.sleep(random.uniform(*CEREBRAS_DELAY_RANGE))

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
    print("=== Context Generation (Cerebras -> Groq -> generic fallback) ===\n")

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
                f"  WhatsApp_Context: {wa['cerebras']} via Cerebras, "
                f"{wa['groq']} via Groq fallback, {wa['fallback']} via generic fallback.\n"
                f"  Email_Context:    {em['cerebras']} via Cerebras, "
                f"{em['groq']} via Groq fallback, {em['fallback']} via generic fallback."
            )
