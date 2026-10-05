# Streamlit Sales Automation

## GitHub / Streamlit Cloud
1. Push this folder to GitHub.
2. In Streamlit Community Cloud, create an app and choose `build.py` as the entry file.
3. Open App settings → Secrets and paste the values from `.streamlit/secrets.toml.example` with real credentials.
4. Deploy.

## Pipeline
- Lead Gen: Google Maps + website/search enrichment → `data/company_profile.csv`
- Context: AI-generated WhatsApp/email instructions → `data/sender.csv`
- WhatsApp: generates personalized message + `wa.me` click-to-chat link
- Email: prepares AI drafts and sends individual emails via SMTP

## Important WhatsApp note
The original Selenium flow depends on an interactive Chrome window and persistent local browser profile. Streamlit Community Cloud does not reliably support that workflow. This version therefore uses click-to-chat links. For true server-side sending, replace that stage with Meta WhatsApp Cloud API.

## Persistence note
Files written to `data/` on Streamlit Community Cloud are ephemeral and can disappear after app restarts/redeploys. Download CSVs from the UI or connect persistent storage/database if needed.
