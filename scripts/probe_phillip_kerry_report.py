#!/usr/bin/env python3
import json,re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
URL='https://www.poems.com.hk/en-us/research-and-analysis/research-report/?codeval=175&num=3171'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Accept-Language':'en-US,en;q=0.9,zh-CN;q=0.7'})
res={}
try:
 r=s.get(URL,timeout=(30,180),allow_redirects=True)
 res['page']={'requested':URL,'resolved':r.url,'status':r.status_code,'type':r.headers.get('content-type'),'bytes':len(r.content),'history':[(x.status_code,x.url,x.headers.get('location')) for x in r.history]}
 enc=r.apparent_encoding or r.encoding or 'utf-8'
 text=r.content.decode(enc,errors='replace')
 Path('phillip-kerry-page.html').write_text(text,encoding='utf-8')
 soup=BeautifulSoup(text,'html.parser')
 print('PAGE',json.dumps(res['page'],ensure_ascii=False),flush=True)
 print('TITLE',soup.title.get_text(' ',strip=True) if soup.title else '',flush=True)
 links=[]
 for tag in soup.find_all(True):
  for attr in ('href','src','data-src','data-url','data-file','data-pdf','data-download','content','value','action'):
   v=tag.get(attr)
   if isinstance(v,str) and v.strip():
    u=urljoin(r.url,v.strip().replace('\\/','/'))
    hay=(u+' '+v).lower()
    if any(k in hay for k in ('.pdf','download','reportfile','research','attachment','file')):
     links.append({'tag':tag.name,'attr':attr,'raw':v.strip(),'url':u})
 for v in re.findall(r'(?:https?:)?//[^\s\"\'<>\\]+',text):
  u=v if v.startswith('http') else 'https:'+v
  if any(k in u.lower() for k in ('.pdf','download','reportfile','attachment','research')):
   links.append({'tag':'regex','attr':'text','raw':v,'url':u.rstrip('),]};\"\'')})
 seen=set(); ded=[]
 for x in links:
  if x['url'] not in seen:
   seen.add(x['url']); ded.append(x)
 res['links']=ded
 for x in ded: print('LINK',json.dumps(x,ensure_ascii=False),flush=True)
 snippets=[]
 for m in re.finditer(r'.{0,400}(?:\.pdf|download|reportfile|attachment|codeval|num=3171).{0,900}',text,flags=re.I|re.S):
  sn=re.sub(r'\s+',' ',m.group(0))[:1800]
  if sn not in snippets: snippets.append(sn)
 res['snippets']=snippets[:100]
 for sn in snippets[:100]: print('SNIP',sn,flush=True)
 for i,x in enumerate(ded,1):
  u=x['url']
  if any(z in u.lower() for z in ('.css','.js','.png','.jpg','.svg')) and '.pdf' not in u.lower(): continue
  try:
   q=s.get(u,headers={'User-Agent':UA,'Referer':r.url,'Accept':'application/pdf,application/octet-stream;q=0.9,*/*;q=0.8'},timeout=(30,300),allow_redirects=True)
   rec={'url':u,'resolved':q.url,'status':q.status_code,'type':q.headers.get('content-type'),'bytes':len(q.content),'head':q.content[:16].hex()}
   print('PROBE',json.dumps(rec,ensure_ascii=False),flush=True)
   if q.status_code==200 and q.content.startswith(b'%PDF-'):
    p=Path(f'phillip-kerry-{i}.pdf'); p.write_bytes(q.content); rec['path']=str(p)
    res.setdefault('pdfs',[]).append(rec)
  except Exception as e: print('PROBEERR',u,repr(e),flush=True)
except Exception as e:
 res['error']=repr(e); print('ERROR',repr(e),flush=True)
Path('phillip-kerry-probe.json').write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8')
