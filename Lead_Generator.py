import os,re,csv,time,random
from datetime import date
from urllib.parse import urljoin
import requests, googlemaps, phonenumbers
from bs4 import BeautifulSoup
try:
    import streamlit as st
except Exception: st=None
try:
    from serpapi import GoogleSearch
except Exception: GoogleSearch=None
try:
    from duckduckgo_search import DDGS
except Exception: DDGS=None

BASE_DIR=os.path.dirname(os.path.abspath(__file__)); DATA_DIR=os.path.join(BASE_DIR,'data'); os.makedirs(DATA_DIR,exist_ok=True)
MASTER_LEADS_CSV=os.path.join(DATA_DIR,'leads_master.csv'); COMPANY_PROFILE_CSV=os.path.join(DATA_DIR,'company_profile.csv')
EMAIL_RE=re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_RE=re.compile(r'(?:\+?\d[\d\-.\s()]{6,16}\d)')
HEADERS={'User-Agent':'Mozilla/5.0'}; CONTACT_PAGE_HINTS=['contact','about','get-in-touch','reach-us']
LEAD_FIELDNAMES=['company_name','phone','phone_clean','website','email','address','keyword','date_found']

def _secret(name,default=None):
    if st is not None:
        try:
            if name in st.secrets:return st.secrets[name]
        except Exception:pass
    return os.getenv(name,default)

def normalize_country_code(raw):
    d=re.sub(r'\D','',raw or ''); return f'+{d}' if d else None

def region_for_dial_code(cc):
    try:
        r=phonenumbers.region_code_for_country_code(int((cc or '').lstrip('+')))
        return r if r and r!='ZZ' else None
    except Exception:return None

def clean_phone(raw,country_code=None):
    if not raw:return ''
    raw=raw.strip(); region=region_for_dial_code(country_code)
    for candidate,reg in [(raw,None if raw.startswith('+') else region)]:
        try:
            p=phonenumbers.parse(candidate,reg)
            if phonenumbers.is_valid_number(p):return phonenumbers.format_number(p,phonenumbers.PhoneNumberFormat.E164)
        except Exception:pass
    digits=re.sub(r'\D','',raw)
    if country_code and digits:
        dial=country_code.lstrip('+')
        for c in ([f'+{digits}'] if digits.startswith(dial) else [])+[f'{country_code}{digits[1:] if digits.startswith("0") else digits}']:
            try:
                p=phonenumbers.parse(c,None)
                if phonenumbers.is_valid_number(p):return phonenumbers.format_number(p,phonenumbers.PhoneNumberFormat.E164)
            except Exception:pass
    return ''

def search_google_maps(keyword,location,country_code,max_results=5):
    key=_secret('GOOGLE_MAPS_API_KEY')
    if not key:return []
    g=googlemaps.Client(key=key); out=[]
    resp=g.places(query=f'{keyword} in {location}')
    for place in resp.get('results',[])[:max_results]:
        d={}; pid=place.get('place_id')
        if pid:
            d=g.place(place_id=pid,fields=['name','formatted_address','formatted_phone_number','international_phone_number','website']).get('result',{})
        raw=d.get('international_phone_number') or d.get('formatted_phone_number','')
        out.append({'company_name':d.get('name',place.get('name','')),'phone':raw,'phone_clean':clean_phone(raw,country_code),'website':d.get('website',''),'email':'','address':d.get('formatted_address',place.get('formatted_address','')),'keyword':keyword,'date_found':date.today().isoformat()})
    return out

def fetch_page(url):
    try:
        r=requests.get(url,headers=HEADERS,timeout=10); r.raise_for_status(); return BeautifulSoup(r.text,'lxml')
    except Exception:return None

def scrape_email_from_site(url):
    soup=fetch_page(url)
    if soup is None:return ''
    emails=set(EMAIL_RE.findall(soup.get_text(' ',strip=True)))
    if not emails:
        for a in soup.find_all('a',href=True):
            if any(h in a['href'].lower() for h in CONTACT_PAGE_HINTS):
                s=fetch_page(urljoin(url,a['href']))
                if s: emails|=set(EMAIL_RE.findall(s.get_text(' ',strip=True)))
                break
    return next(iter(emails),'')

def enrich_leads(leads,location,country_code):
    serp=_secret('SERP_API_KEY')
    for lead in leads:
        if lead.get('website') and not lead.get('email'): lead['email']=scrape_email_from_site(lead['website'])
        q=f"{lead.get('company_name','')} {location}"
        snippet=''
        try:
            if serp and GoogleSearch:
                data=GoogleSearch({'q':q,'api_key':serp,'num':1}).get_dict(); snippet=(data.get('organic_results') or [{}])[0].get('snippet','')
            elif DDGS:
                rr=list(DDGS().text(q,max_results=1)); snippet=rr[0].get('body','') if rr else ''
        except Exception: pass
        if snippet and not lead.get('email'):
            m=EMAIL_RE.search(snippet); lead['email']=m.group(0) if m else ''
        if snippet and not lead.get('phone_clean'):
            m=PHONE_RE.search(snippet)
            if m: lead['phone']=m.group(0); lead['phone_clean']=clean_phone(m.group(0),country_code)
    return leads

def dedupe_leads(leads):
    seen=set(); out=[]
    for x in leads:
        k=x.get('phone_clean') or x.get('website') or x.get('company_name')
        if not k or k in seen: continue
        seen.add(k); out.append(x)
    return out

def _write(path,rows,fields):
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def run_pipeline(keywords,location,goal,country_code,max_per_keyword=5):
    all_leads=[]
    for k in keywords:
        leads=search_google_maps(k,location,country_code,max_per_keyword); enrich_leads(leads,location,country_code); all_leads.extend(leads)
    all_leads=dedupe_leads(all_leads)
    _write(MASTER_LEADS_CSV,all_leads,LEAD_FIELDNAMES)
    profiles=[{'Company':x.get('company_name',''),'Website':x.get('website',''),'Email':x.get('email',''),'Number':x.get('phone_clean',''),'WhatsApp_Verified':'Unverified'} for x in all_leads if x.get('phone_clean')]
    _write(COMPANY_PROFILE_CSV,profiles,['Company','Website','Email','Number','WhatsApp_Verified'])
    return all_leads,profiles
