import os,csv,smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from ai_provider import generate_text
try: import streamlit as st
except Exception: st=None
BASE_DIR=os.path.dirname(os.path.abspath(__file__)); CSV_FILE=os.path.join(BASE_DIR,'data','sender.csv')
def _secret(name,default=None):
    if st is not None:
        try:
            if name in st.secrets:return st.secrets[name]
        except Exception:pass
    return os.getenv(name,default)
def load_contacts(path=CSV_FILE):
    with open(path,newline='',encoding='utf-8-sig') as f:
        return [{'company':r.get('Company',''),'email':(r.get('Email') or '').strip(),'prompt':(r.get('Email_Context') or '').strip()} for r in csv.DictReader(f) if (r.get('Email') or '').strip()]
def get_ai_email_body(prompt):
    t,_=generate_text(f'{prompt}. Write the complete email body only, no greeting/closing, under 100 words, ready to send.'); return t or 'I came across your company and thought there may be an opportunity to work together. I would be glad to connect and explore potential collaboration.'
def get_ai_email_subject(company):
    t,_=generate_text(f'Write a short professional outreach email subject under 10 words for {company}. Return only the subject.'); return t or 'Collaboration Opportunity'
def send_email(recipient,subject,body):
    sender=_secret('EMAIL_SENDER'); password=_secret('EMAIL_PASSWORD'); host=_secret('EMAIL_SMTP_HOST','smtp.gmail.com'); port=int(_secret('EMAIL_SMTP_PORT','587'))
    if not sender or not password:return False,'Missing EMAIL_SENDER/EMAIL_PASSWORD'
    try:
        msg=MIMEMultipart('alternative'); msg['Subject']=subject; msg['From']=sender; msg['To']=recipient; msg.attach(MIMEText(body,'plain'))
        with smtplib.SMTP(host,port) as s: s.starttls(); s.login(sender,password); s.sendmail(sender,[recipient],msg.as_string())
        return True,''
    except Exception as e:return False,str(e)
def prepare_emails():
    out=[]
    for c in load_contacts(): out.append({**c,'subject':get_ai_email_subject(c['company']),'body':get_ai_email_body(c['prompt'])})
    return out
