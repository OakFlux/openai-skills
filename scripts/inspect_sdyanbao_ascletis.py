#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = 'https://www.sdyanbao.com/detail/338702'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
s = requests.Session()
s.headers.update({'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'})

r = s.get(URL, timeout=(20, 120), allow_redirects=True)
r.encoding = r.apparent_encoding or r.encoding or 'utf-8'
text = r.text
Path('sdyanbao_338702.html').write_text(text, encoding='utf-8')
print('PAGE', r.status_code, r.url, r.headers.get('content-type'), len(r.content), flush=True)

soup = BeautifulSoup(text, 'html.parser')
print('TITLE', soup.title.get_text(' ', strip=True) if soup.title else '', flush=True)

links = []
for tag in soup.find_all(True):
    for attr in ('href', 'src', 'action', 'data-url', 'data-download', 'data-file', 'data-pdf', 'value', 'content'):
        value = tag.get(attr)
        if not isinstance(value, str) or not value.strip():
            continue
        absolute = urljoin(r.url, value.strip())
        hay = (absolute + ' ' + value).lower()
        if any(key in hay for key in ('pdf', 'download', 'file', 'report', '338702', 'api', 'detail')):
            links.append({'tag': tag.name, 'attr': attr, 'raw': value, 'url': absolute})

seen = set()
for item in links:
    if item['url'] in seen:
        continue
    seen.add(item['url'])
    print('LINK', json.dumps(item, ensure_ascii=False), flush=True)

for pattern in ('download', 'pdf', '338702', 'fileUrl', 'file_url', 'reportUrl', 'report_url', 'api/'):
    print('PATTERN', pattern, flush=True)
    count = 0
    for match in re.finditer(r'.{0,400}' + re.escape(pattern) + r'.{0,900}', text, flags=re.I | re.S):
        snippet = re.sub(r'\s+', ' ', match.group(0))[:1600]
        print('SNIP', snippet, flush=True)
        count += 1
        if count >= 30:
            break

# Save external JS URLs for a later targeted probe.
js_urls = []
for tag in soup.find_all('script'):
    src = tag.get('src')
    if isinstance(src, str) and src.strip():
        js_urls.append(urljoin(r.url, src.strip()))
Path('sdyanbao_js_urls.json').write_text(json.dumps(js_urls, ensure_ascii=False, indent=2), encoding='utf-8')
print('JS_URLS', json.dumps(js_urls, ensure_ascii=False), flush=True)
