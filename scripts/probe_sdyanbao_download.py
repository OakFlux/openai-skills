#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path

import requests

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
PAGE = 'https://www.sdyanbao.com/detail/338702'
API = 'https://api.sdyanbao.com'
REPORT_ID = 338702

s = requests.Session()
s.headers.update({
    'User-Agent': UA,
    'Origin': 'https://www.sdyanbao.com',
    'Referer': PAGE,
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7',
})

results = []

def record(label, response):
    item = {
        'label': label,
        'status': response.status_code,
        'url': response.url,
        'type': response.headers.get('content-type'),
        'bytes': len(response.content),
        'head_hex': response.content[:32].hex(),
        'history': [(x.status_code, x.url, x.headers.get('location')) for x in response.history],
        'headers': {k: v for k, v in response.headers.items() if k.lower() in ('content-disposition','location','content-length','content-type')},
    }
    if response.content.startswith(b'%PDF-'):
        path = Path(f'{label}.pdf')
        path.write_bytes(response.content)
        item['pdf_path'] = str(path)
    else:
        try:
            item['text'] = response.text[:5000]
        except Exception:
            pass
    results.append(item)
    print(json.dumps(item, ensure_ascii=False), flush=True)

for payload in ({'id': REPORT_ID}, {'id': str(REPORT_ID)}):
    for use_json in (True, False):
        try:
            kwargs = {'json': payload} if use_json else {'data': payload}
            r = s.post(API + '/api/file/detail', timeout=(20, 120), allow_redirects=True, **kwargs)
            record(f'detail_{"json" if use_json else "form"}_{payload["id"]}', r)
        except Exception as exc:
            print('ERROR detail', repr(exc), flush=True)
        try:
            kwargs = {'json': payload} if use_json else {'data': payload}
            r = s.post(API + '/api/file/download', timeout=(20, 120), allow_redirects=True, **kwargs)
            record(f'download_{"json" if use_json else "form"}_{payload["id"]}', r)
        except Exception as exc:
            print('ERROR download', repr(exc), flush=True)

for url in (
    'https://www.sdyanbao.com/download/338702/7',
    'https://www.sdyanbao.com/download/338702',
    'https://api.sdyanbao.com/api/file/download?id=338702',
    'https://api.sdyanbao.com/api/file/detail?id=338702',
):
    try:
        r = s.get(url, timeout=(20, 120), allow_redirects=True)
        record('get_' + str(len(results)), r)
    except Exception as exc:
        print('ERROR get', url, repr(exc), flush=True)

Path('sdyanbao_download_probe.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
