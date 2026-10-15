# Sales Automation Agent

A multi-agent pipeline that finds business leads on Google Maps, fills in their contact details, and drafts a personalised WhatsApp message and email for each one, ready for you to review and send.

Type a location and a few keywords (say, *"contractor, architect"* in *"Andheri, Mumbai"*). The app returns a cleaned list of businesses with a short, relevant message written for each.

---

## How it works

```
User input (location, keywords, dial code)
        │
        ▼
1. Lead Gen agent ──► data/company_profile.csv
   Google Maps search → website scrape → web search
   phones cleaned to E.164, duplicates removed
        │
        ▼
2. Context agent  ◄── outreach goal (typed by user)
   writes one WhatsApp brief + one email brief per lead
        │                         ──► data/sender.csv
   ┌────┴─────┐
   ▼          ▼
3. WhatsApp   4. Email agent
   agent         subject + body, sent via SMTP
   wa.me link
   └────┬─────┘
        ▼
   Human review: nothing is sent until you click
```

| Stage | File | What it does |
| --- | --- | --- |
| Lead Gen | `Lead_Generator.py` | Searches Google Maps for each keyword, scrapes emails from company websites (and their contact/about pages), falls back to SerpAPI or DuckDuckGo for missing details, normalises phone numbers with `phonenumbers`, and removes duplicates. |
| Context | `context.py` | For every lead, asks the LLM to write a short *instruction* for the WhatsApp agent and the email agent, based on your outreach goal. |
| WhatsApp | `whatapp_sender.py` | Turns each instruction into a message under 30 words and builds a `wa.me` click-to-chat link. |
| Email | `email_sender.py` | Writes a subject (under 10 words) and body (under 100 words), shows them for editing, and sends over SMTP when you click **Send**. |
| AI provider | `ai_provider.py` | One `generate_text()` call that tries **Groq** (`openai/gpt-oss-120b`) first and **Google Gemini** as a fallback. If both fail, the calling stage uses a fixed template message. |
| App | `build.py` | The Streamlit interface: four tabs, one per stage, with a CSV download at each step. |

---

## Tech stack

- **Python 3.10+**, **Streamlit** for the UI
- **Google Maps Places API** for lead data
- **requests + BeautifulSoup/lxml** for website scraping
- **SerpAPI** (optional) or **DuckDuckGo search** for enrichment
- **phonenumbers** for phone validation and E.164 formatting
- **Groq** and **Google Gemini** via the `openai` Python client (OpenAI-compatible endpoints)
- **smtplib** for email, **pandas** for tables

---

## Using the app

1. **Lead Gen**: enter a location, comma-separated keywords, the country dial code (e.g. `+91`) and results per keyword (1–20). Click **Generate leads**.
2. **Context**: describe your outreach goal, e.g. *"Introduce our waterproofing solutions and set up a meeting with the technical team."* Click **Generate personalized contexts**.
3. **WhatsApp**: click **Generate WhatsApp messages**, read each one, then click **Open in WhatsApp** to send it from your own account.
4. **Email**: click **Prepare email drafts**, edit the subject or body if needed, and click **Send email** for each one.

Each stage saves a CSV you can download from the app.

---

## Output files

All are written to `data/` and kept out of Git.

| File | Columns |
| --- | --- |
| `leads_master.csv` | company_name, phone, phone_clean, website, email, address, keyword, date_found |
| `company_profile.csv` | Company, Website, Email, Number, WhatsApp_Verified |
| `sender.csv` | Company, WhatsApp_Context, Email_Context, Number, Email |

---

## Deploying to Streamlit Community Cloud

1. Push the repo to GitHub.
2. In Streamlit Community Cloud, create a new app and choose `build.py` as the entry file.
3. Open **App settings → Secrets** and paste the contents of your `secrets.toml`.
4. Deploy.

---

## Known limitations

- **WhatsApp isn't sent automatically.** The earlier Selenium version needed a local Chrome window, which Streamlit Cloud can't provide, so this version uses click-to-chat links. Fully automatic sending needs the Meta WhatsApp Cloud API.
- **Data isn't persistent on Streamlit Cloud.** Files in `data/` can be wiped on restart, so download your CSVs after each run.
- **Emails aren't always found.** Sites that load content with JavaScript or hide emails behind contact forms often return nothing.
- **WhatsApp numbers aren't verified.** Every lead is marked `Unverified` for now.
- **API costs.** Google Maps and SerpAPI charge beyond their free tiers, and each lead uses up to five LLM calls.

## Roadmap

- [ ] Meta WhatsApp Cloud API for real sending and delivery status
- [ ] Automatic WhatsApp number verification
- [ ] Lead-scoring agent (fills `Fit_Notes` / `Vertical_Fit`, which `context.py` already reads)
- [ ] Database storage (SQLite / Supabase) instead of CSVs
- [ ] Reply tracking and follow-ups
- [ ] Batched, rate-limited email sending

---

## Responsible use

This tool drafts cold outreach. Only contact businesses you have a genuine reason to reach. Follow anti-spam rules and data-protection law (such as India's DPDP Act, 2023), and honour any request to stop. The human review step is there on purpose.

## Contributors

- [Jaival Katira](https://github.com/JaivalKatira)
- [Krish Kalsi](https://github.com/KrishKalsi)
