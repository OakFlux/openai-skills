#!/usr/bin/env python3
import json
import concurrent.futures
import requests

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
reports=[('4702606','2025/02/18',35),('5263403','2026/02/10',35)]

def probe(args):
    rid,date,page=args
    u=f'https://public.fxbaogao.com/report-image/{date}/{rid}-{page}.png'
    try:
        r=requests.get(u,headers={'User-Agent':UA,'Referer':'https://www.fxbaogao.com/'},timeout=(10,30))
        return {'rid':rid,'page':page,'url':u,'status':r.status_code,'type':r.headers.get('content-type'),'bytes':len(r.content),'png':r.content.startswith(b'\x89PNG\r\n\x1a\n')}
    except Exception as e:
        return {'rid':rid,'page':page,'url':u,'error':repr(e)}

items=[(rid,date,p) for rid,date,n in reports for p in range(1,n+1)]
with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
    rows=list(ex.map(probe,items))
for row in rows: print(json.dumps(row,ensure_ascii=False),flush=True)
open('kerry-page-ranges.json','w',encoding='utf-8').write(json.dumps(rows,ensure_ascii=False,indent=2))
