import os, re
from openai import OpenAI
try:
    import streamlit as st
except Exception:
    st = None

def _secret(name, default=None):
    if st is not None:
        try:
            if name in st.secrets:
                return st.secrets[name]
        except Exception:
            pass
    return os.getenv(name, default)

GROQ_MODEL = _secret('GROQ_MODEL','openai/gpt-oss-120b')
GROQ_REASONING_EFFORT = _secret('GROQ_REASONING_EFFORT','low')
GOOGLE_MODEL = _secret('GOOGLE_MODEL','gemini-3.5-flash-lite')
GROQ_BASE_URL='https://api.groq.com/openai/v1'
GOOGLE_BASE_URL='https://generativelanguage.googleapis.com/v1beta/openai/'
_clients={}

def _get_client(provider):
    if provider in _clients: return _clients[provider]
    if provider=='groq': key,base=_secret('GROQ_API_KEY'),GROQ_BASE_URL
    else: key,base=_secret('GOOGLE_API_KEY'),GOOGLE_BASE_URL
    _clients[provider]=OpenAI(api_key=key,base_url=base,timeout=30,max_retries=2) if key else None
    return _clients[provider]

def _clean(text):
    text=re.sub(r'<think>.*?</think>','',text or '',flags=re.DOTALL).strip()
    return text.strip('"').strip("'").strip()

def _call(provider,prompt,temperature=None):
    c=_get_client(provider)
    if c is None:return None
    if provider=='groq':
        kwargs={'model':GROQ_MODEL}
        if temperature is not None: kwargs['temperature']=temperature
        if 'gpt-oss' in GROQ_MODEL.lower() and GROQ_REASONING_EFFORT:
            kwargs['extra_body']={'reasoning_effort':GROQ_REASONING_EFFORT}
    else:
        kwargs={'model':GOOGLE_MODEL}
    r=c.chat.completions.create(messages=[{'role':'user','content':prompt}],**kwargs)
    return _clean(r.choices[0].message.content) or None

def generate_text(prompt,temperature=None):
    for provider in ('groq','google'):
        try:
            txt=_call(provider,prompt,temperature)
            if txt:return txt,provider
        except Exception:
            pass
    return None,None

def provider_summary():
    g=f'Groq ({GROQ_MODEL})' if _secret('GROQ_API_KEY') else 'Groq (NO KEY)'
    x=f'Google ({GOOGLE_MODEL})' if _secret('GOOGLE_API_KEY') else 'Google (NO KEY)'
    return f'{g} -> {x}'
