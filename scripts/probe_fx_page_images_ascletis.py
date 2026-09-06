#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import json
import requests

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
REPORTS = [
    {'id': '4757488', 'date_path': '2025/04/01', 'title': '东吴证券_全新GLP-1减重不减肌'},
    {'id': '5048656', 'date_path': '2025/09/07', 'title': '兴证国际_聚焦减重代谢产品差异化管线'},
    {'id': '5205547', 'date_path': '2025/12/28', 'title': '东方证券_口服小分子率先破局'},
]

s = requests.Session()
s.headers.update({'User-Agent': UA, 'Referer': 'https://www.fxbaogao.com/'})
results = []

for report in REPORTS:
    misses = 0
    pages = []
    for page in range(1, 81):
        url = f"https://public.fxbaogao.com/report-image/{report['date_path']}/{report['id']}-{page}.png"
        try:
            r = s.get(url, timeout=(15, 60), allow_redirects=True)
            ctype = r.headers.get('content-type', '')
            ok = r.status_code == 200 and ctype.startswith('image/') and len(r.content) > 5000
            rec = {'page': page, 'url': url, 'status': r.status_code, 'type': ctype, 'bytes': len(r.content), 'ok': ok}
            pages.append(rec)
            print('PAGE', report['id'], json.dumps(rec, ensure_ascii=False), flush=True)
            if ok:
                misses = 0
            else:
                misses += 1
                if page > 3 and misses >= 3:
                    break
        except Exception as exc:
            pages.append({'page': page, 'url': url, 'error': repr(exc), 'ok': False})
            print('ERROR', report['id'], page, repr(exc), flush=True)
            misses += 1
            if page > 3 and misses >= 3:
                break
    results.append({**report, 'pages': pages, 'available_pages': [p['page'] for p in pages if p.get('ok')]})

Path('ascletis_fx_page_probe.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print('SUMMARY', json.dumps([{'id': r['id'], 'available': r['available_pages']} for r in results], ensure_ascii=False), flush=True)
