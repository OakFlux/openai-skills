#!/usr/bin/env python3
import json
from pathlib import Path
import requests

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Referer':'https://www.fxbaogao.com/'})
urls=[
 'https://public.fxbaogao.com/report-image/2025/02/18/4702606-1.png',
 'https://public.fxbaogao.com/report-image/2025/02/18/4702606-29.png',
 'https://public.fxbaogao.com/report-image/2026/02/10/5263403-1.png',
 'https://public.fxbaogao.com/report-image/2026/02/10/5263403-28.png',
]
out=Path('kerry-image-probe'); out.mkdir(exist_ok=True)
res=[]
for i,u in enumerate(urls,1):
 try:
  r=s.get(u,timeout=(20,120),allow_redirects=True)
  rec={'url':u,'resolved':r.url,'status':r.status_code,'type':r.headers.get('content-type'),'bytes':len(r.content),'head':r.content[:16].hex()}
  res.append(rec); print(json.dumps(rec,ensure_ascii=False),flush=True)
  if r.status_code==200 and (r.content.startswith(b'\x89PNG') or r.content.startswith(b'\xff\xd8\xff')):
   ext='.png' if r.content.startswith(b'\x89PNG') else '.jpg'
   (out/f'{i}{ext}').write_bytes(r.content)
 except Exception as e:
  rec={'url':u,'error':repr(e)}; res.append(rec); print(json.dumps(rec,ensure_ascii=False),flush=True)
Path('kerry-image-probe.json').write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8')
