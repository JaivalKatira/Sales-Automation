import csv,os,urllib.parse
from ai_provider import generate_text
BASE_DIR=os.path.dirname(os.path.abspath(__file__)); CSV_FILE=os.path.join(BASE_DIR,'data','sender.csv')
def load_contacts(path=CSV_FILE):
    with open(path,newline='',encoding='utf-8-sig') as f:
        return [{'company':r.get('Company',''),'phone':(r.get('Number') or '').strip(),'prompt':(r.get('WhatsApp_Context') or '').strip()} for r in csv.DictReader(f) if (r.get('Number') or '').strip()]
def get_ai_message(prompt):
    t,_=generate_text(f'{prompt}. Write the finished WhatsApp message only, under 30 words, ready to send.'); return t or 'Hi! Just reaching out to connect — would love to chat if you are open to it.'
def build_whatsapp_links():
    out=[]
    for c in load_contacts():
        msg=get_ai_message(c['prompt']); phone=''.join(ch for ch in c['phone'] if ch.isdigit()); url=f'https://wa.me/{phone}?text={urllib.parse.quote(msg)}'
        out.append({**c,'message':msg,'url':url})
    return out
