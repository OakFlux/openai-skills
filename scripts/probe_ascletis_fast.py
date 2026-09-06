#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, time
from pathlib import Path
import requests

API='https://reportapi.eastmoney.com/report/list'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Referer':'https://data.eastmoney.com/'})
out=Path('ascletis_fast'); out.mkdir(exist_ok=True)
windows=[
 ('2023-01-02','2023-01-05'),
 ('2025-03-31','2025-04-03'),
 ('2025-08-22','2025-08-29'),
 ('2025-09-05','2025-09-12'),
 ('2025-12-27','2025-12-31'),
]
keywords=['歌礼制药','歌禮製藥','ASCLETIS','ASC30','ASC47','减重不减肌']
results=[]; seen=set()
for begin,end in windows:
  page=1; total=1
  while page<=total:
    params={'industryCode':'*','pageSize':'100','industry':'*','rating':'*','ratingChange':'*','beginTime':begin,'endTime':end,'pageNo':page,'fields':'','qType':'0','orgCode':'','code':'','rcode':'','p':page,'pageNum':page,'pageNumber':page}
    r=s.get(API,params=params,timeout=(30,180)); print('GET',begin,end,page,r.status_code,len(r.content),flush=True); r.raise_for_status(); p=r.json(); total=int(p.get('TotalPage') or 1)
    for row in p.get('data') or []:
      hay=' '.join(str(row.get(k) or '') for k in ('title','stockName','stockCode','orgName','orgSName')).upper()
      if any(k.upper() in hay for k in keywords):
        info=str(row.get('infoCode') or '')
        if not info or info in seen: continue
        seen.add(info); item={'query':[begin,end,page],**row}; print('MATCH',json.dumps(item,ensure_ascii=False,default=str),flush=True)
        pdfurl=f'https://pdf.dfcfw.com/pdf/H3_{info}_1.pdf'
        pr=s.get(pdfurl,headers={'User-Agent':UA,'Referer':'https://data.eastmoney.com/report/','Accept':'application/pdf,*/*'},timeout=(30,600),allow_redirects=True)
        print('PDF',info,pr.status_code,pr.headers.get('content-type'),len(pr.content),pr.content[:5],flush=True)
        if pr.status_code==200 and pr.content.startswith(b'%PDF-'):
          path=out/f'{info}.pdf'; path.write_bytes(pr.content); item['pdf']={'url':pr.url,'path':str(path),'bytes':len(pr.content)}
        results.append(item)
    page+=1; time.sleep(.1)
Path('ascletis_fast_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print('TOTAL',len(results),'PDFS',sum(1 for x in results if x.get('pdf')),flush=True)
