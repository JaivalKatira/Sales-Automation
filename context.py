import csv,os
from ai_provider import generate_text
BASE_DIR=os.path.dirname(os.path.abspath(__file__)); DATA_DIR=os.path.join(BASE_DIR,'data'); COMPANY_PROFILE_CSV=os.path.join(DATA_DIR,'company_profile.csv'); SENDER_CSV=os.path.join(DATA_DIR,'sender.csv')
FIELDS=['Company','WhatsApp_Context','Email_Context','Number','Email']
def load_company_profile(path=COMPANY_PROFILE_CSV):
    with open(path,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def filter_leads(rows,include_non_fit=True):
    out=[]
    for r in rows:
        if (r.get('WhatsApp_Verified') or '').strip()=='No':continue
        if not include_non_fit and (r.get('Vertical_Fit') or '').strip()=='No':continue
        out.append(r)
    return out

def _prompt(lead,goal,channel):
    company=lead.get('Company') or 'this business'; notes=lead.get('Fit_Notes') or ''
    limit='30' if channel=='whatsapp' else '40'; medium='WhatsApp message' if channel=='whatsapp' else 'professional cold-email opening paragraph'
    return f"Write ONE instruction for another AI to draft a short {medium}. Goal: {goal}. Company: {company}. Fit notes: {notes or '(none)'}. Do not invent facts. Keep the eventual message under {limit} words. Return only the instruction sentence."
def generate_context(lead,goal,channel):
    t,p=generate_text(_prompt(lead,goal,channel),temperature=0.4)
    if t:return t,p
    if channel=='email': return f'Write a short, warm, professional email opening paragraph about: {goal}. Keep it under 40 words.','fallback'
    return f'Write a short, friendly WhatsApp message about: {goal}. Keep it under 30 words.','fallback'
def build_context(goal,include_non_fit=True):
    rows=filter_leads(load_company_profile(),include_non_fit); out=[]
    for lead in rows:
        wa,_=generate_context(lead,goal,'whatsapp'); em,_=generate_context(lead,goal,'email')
        out.append({'Company':lead.get('Company',''),'WhatsApp_Context':wa,'Email_Context':em,'Number':lead.get('Number',''),'Email':lead.get('Email','')})
    with open(SENDER_CSV,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(out)
    return out
