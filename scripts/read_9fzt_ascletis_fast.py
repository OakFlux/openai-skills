#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
IDS=['758029035672','732723379903','645790628897','787429061043']
s=requests.Session(); s.headers.update({'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9'})
all_out=[]
for rid in IDS:
    url=f'https://gmg.9fzt.com/report/HKSE/01672/{rid}.html'
    try:
        r=s.get(url,timeout=(20,90),allow_redirects=True)
        r.encoding=r.apparent_encoding or r.encoding or 'utf-8'
        text=r.text
        Path(f'9fzt_{rid}.html').write_text(text,encoding='utf-8')
        soup=BeautifulSoup(text,'html.parser')
        rec={'id':rid,'url':url,'status':r.status_code,'resolved':r.url,'bytes':len(r.content),'title':soup.title.get_text(' ',strip=True) if soup.title else '', 'links':[]}
        seen=set()
        for tag in soup.find_all(True):
            for attr in ('href','src','data-src','data-url','data-file','data-pdf','content','value','action'):
                val=tag.get(attr)
                if not isinstance(val,str) or not val.strip(): continue
                u=urljoin(r.url,val.strip())
                if u in seen: continue
                hay=(u+' '+val).lower()
                if any(k in hay for k in ('pdf','download','report','file','attachment','api','01672',rid)):
                    seen.add(u); rec['links'].append({'tag':tag.name,'attr':attr,'raw':val.strip(),'url':u})
        for pat in [r'(?:https?:)?//[^\s\"\'<>\\]+',r'/[^\s\"\'<>\\]*(?:pdf|download|report|file|attachment|api)[^\s\"\'<>\\]*']:
            for val in re.findall(pat,text,flags=re.I):
                u=urljoin(r.url,val).rstrip('),]};\"\'')
                if u not in seen and len(u)<1000:
                    seen.add(u); rec['links'].append({'tag':'regex','attr':'text','raw':val,'url':u})
        all_out.append(rec)
        print('PAGE',json.dumps({k:v for k,v in rec.items() if k!='links'},ensure_ascii=False),flush=True)
        for x in rec['links'][:300]: print('LINK',rid,json.dumps(x,ensure_ascii=False),flush=True)
        for key in ('pdf','download','fileUrl','file_url','reportUrl','report_url','attachment','oss','api'):
            for i,m in enumerate(re.finditer(r'.{0,500}'+re.escape(key)+r'.{0,1000}',text,flags=re.I|re.S)):
                print('SNIP',rid,key,re.sub(r'\s+',' ',m.group(0))[:1800],flush=True)
                if i>=9: break
    except Exception as e:
        all_out.append({'id':rid,'url':url,'error':repr(e)}); print('ERROR',rid,repr(e),flush=True)
Path('9fzt_ascletis_fast.json').write_text(json.dumps(all_out,ensure_ascii=False,indent=2),encoding='utf-8')
