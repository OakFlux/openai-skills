#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, time
from pathlib import Path
import requests

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/152 Safari/537.36'
API='https://reportapi.eastmoney.com/report/list'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Referer':'https://data.eastmoney.com/'})
queries=[
 ('2025-02-15','2025-02-28','',0),('2025-02-15','2025-02-28','',1),('2025-02-15','2025-02-28','',2),
 ('2025-02-15','2025-02-28','01359',0),('2025-02-15','2025-02-28','1359',0),
 ('2025-02-15','2025-02-28','01359.HK',0),
]
results=[]
for begin,end,code,qtype in queries:
  page=1; total=1
  while page<=total:
    params={'industryCode':'*','pageSize':'100','industry':'*','rating':'*','ratingChange':'*','beginTime':begin,'endTime':end,'pageNo':str(page),'fields':'','qType':str(qtype),'orgCode':'','code':code,'rcode':'','p':str(page),'pageNum':str(page),'pageNumber':str(page)}
    r=s.get(API,params=params,timeout=(30,180)); print('QUERY',begin,end,code,qtype,page,r.status_code,len(r.content),flush=True); r.raise_for_status()
    try: payload=r.json()
    except Exception:
      print('BODY',r.text[:500],flush=True); break
    total=int(payload.get('TotalPage') or 1); rows=payload.get('data') or []
    print('META',payload.keys(),'TOTAL',total,'ROWS',len(rows),flush=True)
    for row in rows:
      hay=' '.join(str(row.get(k) or '') for k in ('title','stockName','stockCode','orgName','orgSName','researcher'))
      if any(k in hay for k in ('中国信达','信达','经济复苏','业绩筑底','01359','1359')):
        item={'query':[begin,end,code,qtype,page],**row}; results.append(item); print('MATCH',json.dumps(item,ensure_ascii=False,default=str),flush=True)
    page+=1; time.sleep(.1)
Path('china-cinda-guosen-probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print('TOTAL MATCHES',len(results),flush=True)
