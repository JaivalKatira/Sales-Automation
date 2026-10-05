import os,pandas as pd,streamlit as st
from Lead_Generator import run_pipeline,normalize_country_code,COMPANY_PROFILE_CSV,MASTER_LEADS_CSV
from context import build_context,SENDER_CSV
from whatapp_sender import build_whatsapp_links
from email_sender import prepare_emails,send_email
from ai_provider import provider_summary

st.set_page_config(page_title='Sales Automation',page_icon='📈',layout='wide')
st.title('Sales Automation Pipeline')
st.caption('Lead generation → context → WhatsApp → email')
st.info('WhatsApp on Streamlit Cloud uses click-to-chat links. Direct browser automation with Selenium is not supported reliably on Streamlit Community Cloud.')
st.sidebar.write('AI: '+provider_summary())

def show_csv(path,key):
    if os.path.exists(path):
        df=pd.read_csv(path); st.dataframe(df,use_container_width=True); st.download_button('Download CSV',df.to_csv(index=False).encode(),file_name=os.path.basename(path),mime='text/csv',key=key)

lead_tab,ctx_tab,wa_tab,email_tab=st.tabs(['1. Lead Gen','2. Context','3. WhatsApp','4. Email'])
with lead_tab:
    location=st.text_input('Location',placeholder='Andheri, Mumbai')
    keywords=st.text_input('Keywords (comma separated)',placeholder='contractor, architect, builder')
    cc=st.text_input('Country dial code',value='+91')
    max_results=st.number_input('Max results per keyword',1,20,5)
    if st.button('Generate leads',type='primary'):
        ks=[x.strip() for x in keywords.split(',') if x.strip()]; c=normalize_country_code(cc)
        if not location or not ks or not c: st.error('Location, at least one keyword, and a valid dial code are required.')
        else:
            with st.spinner('Generating and enriching leads...'):
                leads,profiles=run_pipeline(ks,location,'',c,int(max_results))
            st.success(f'Found {len(leads)} unique leads; {len(profiles)} have usable phone numbers.')
    show_csv(COMPANY_PROFILE_CSV,'profile_dl')
with ctx_tab:
    goal=st.text_area('Final outreach goal',placeholder='Introduce our waterproofing solutions and arrange a meeting with the project/technical team.')
    if st.button('Generate personalized contexts'):
        if not os.path.exists(COMPANY_PROFILE_CSV): st.error('Run Lead Gen first.')
        elif not goal.strip(): st.error('Enter an outreach goal.')
        else:
            with st.spinner('Generating contexts...'): rows=build_context(goal.strip())
            st.success(f'Generated context for {len(rows)} leads.')
    show_csv(SENDER_CSV,'sender_dl')
with wa_tab:
    if st.button('Generate WhatsApp messages'):
        if not os.path.exists(SENDER_CSV): st.error('Generate context first.')
        else: st.session_state['wa_rows']=build_whatsapp_links()
    for i,r in enumerate(st.session_state.get('wa_rows',[])):
        with st.expander(f"{r['company'] or r['phone']} — {r['phone']}"):
            st.write(r['message']); st.link_button('Open in WhatsApp',r['url'])
with email_tab:
    if st.button('Prepare email drafts'):
        if not os.path.exists(SENDER_CSV): st.error('Generate context first.')
        else: st.session_state['email_rows']=prepare_emails()
    rows=st.session_state.get('email_rows',[])
    for i,r in enumerate(rows):
        with st.expander(f"{r['company']} — {r['email']}"):
            subj=st.text_input('Subject',r['subject'],key=f's{i}'); body=st.text_area('Body',r['body'],key=f'b{i}',height=160)
            if st.button('Send email',key=f'send{i}'):
                ok,err=send_email(r['email'],subj,body); st.success('Sent') if ok else st.error(err)
