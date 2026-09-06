#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path

import requests

API = 'https://api.sdyanbao.com'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
s = requests.Session()
s.headers.update({
    'User-Agent': UA,
    'Origin': 'https://www.sdyanbao.com',
    'Referer': 'https://www.sdyanbao.com/search?keyword=%E6%AD%8C%E7%A4%BC%E5%88%B6%E8%8D%AF',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7',
})

queries = ['歌礼制药', '歌礼制药-B', '歌礼', 'ASCLETIS', '1672.HK', '01672']
results = {}
for keyword in queries:
    payload = {
        'device': 1,
        'hideTrader': 0,
        'onlyTitle': 0,
        'order': 0,
        'page': 1,
        'pageSize': 100,
        'keyword': keyword,
        'pageCount': 0,
        'dateRange': 0,
        'startTime': '',
        'endTime': '',
        'typeIds': '',
        'industryIds': '',
    }
    r = s.post(API + '/api/file/search', json=payload, timeout=(20, 180))
    print('SEARCH', keyword, r.status_code, len(r.content), r.text[:300], flush=True)
    try:
        data = r.json()
    except Exception:
        data = {'raw': r.text}
    results[keyword] = data
    files = ((data.get('data') or {}).get('files') or []) if isinstance(data, dict) else []
    for item in files:
        print('FILE', keyword, json.dumps(item, ensure_ascii=False, default=str), flush=True)
        report_id = item.get('id')
        if report_id:
            d = s.post(API + '/api/file/detail', json={'id': report_id}, timeout=(20, 180))
            try:
                detail = d.json()
            except Exception:
                detail = {'raw': d.text}
            item['_detail'] = detail
            print('DETAIL', report_id, json.dumps(detail, ensure_ascii=False, default=str)[:20000], flush=True)

Path('sdyanbao_ascletis_search.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
