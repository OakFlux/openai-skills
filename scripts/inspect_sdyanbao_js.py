#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE = 'https://www.sdyanbao.com'
URLS = [
    f'{BASE}/_nuxt/0bec462.js',
    f'{BASE}/_nuxt/7ff3a18.js',
    f'{BASE}/_nuxt/359ac1f.js',
    f'{BASE}/_nuxt/a94fdf1.js',
]
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
s = requests.Session()
s.headers.update({'User-Agent': UA, 'Referer': f'{BASE}/detail/338702'})
out = Path('sdyanbao_js')
out.mkdir(exist_ok=True)
summary = []

for idx, url in enumerate(URLS):
    r = s.get(url, timeout=(20, 180), allow_redirects=True)
    text = r.text
    path = out / f'bundle_{idx}.js'
    path.write_text(text, encoding='utf-8')
    rec = {'url': url, 'resolved': r.url, 'status': r.status_code, 'bytes': len(r.content)}
    summary.append(rec)
    print('BUNDLE', json.dumps(rec, ensure_ascii=False), flush=True)

    patterns = [
        'download', 'unlock', 'online_url', 'page_url', 'original_id',
        'data_source_uuid', '/api/', '$axios', 'axios.', 'window.open',
        'location.href', 'file_size', 'is_vip', 'showLogin', 'showPay',
    ]
    for pattern in patterns:
        count = 0
        for match in re.finditer(re.escape(pattern), text, flags=re.I):
            start = max(0, match.start() - 700)
            end = min(len(text), match.end() + 1200)
            snippet = re.sub(r'\s+', ' ', text[start:end])
            print('SNIP', idx, pattern, snippet[:2200], flush=True)
            count += 1
            if count >= 20:
                break

    # Print URL-like string literals that look relevant.
    literals = re.findall(r'["\']([^"\']{1,400})["\']', text)
    seen = set()
    for value in literals:
        if value in seen:
            continue
        seen.add(value)
        low = value.lower()
        if any(k in low for k in ('download', 'unlock', 'detail', 'report', 'collect', 'oss.', 'api/', 'pdf', 'page/')):
            absolute = urljoin(BASE + '/', value)
            print('LITERAL', idx, json.dumps({'raw': value, 'absolute': absolute}, ensure_ascii=False), flush=True)

Path('sdyanbao_js_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
