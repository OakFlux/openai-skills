import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT = Path('output/baolidi_detail_probe')
OUT.mkdir(parents=True, exist_ok=True)

pages = [
    ('northeast_2025', 'https://www.9fzt.com/detail/sz_300905_10_801847363260.html'),
    ('haitong_2024_9fzt', 'https://www.9fzt.com/detail/sz_300905_10_770729890472.html'),
    ('haitong_2024_sina', 'https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/770729890472/index.phtml'),
]

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
})

summary = []
for label, url in pages:
    response = session.get(url, timeout=90, allow_redirects=True)
    response.encoding = response.apparent_encoding or 'utf-8'
    html = response.text
    (OUT / f'{label}.html').write_text(html, encoding='utf-8')
    soup = BeautifulSoup(html, 'html.parser')
    text = '\n'.join(line.strip() for line in soup.get_text('\n').splitlines() if line.strip())
    (OUT / f'{label}.txt').write_text(text, encoding='utf-8')

    links = []
    for tag in soup.find_all(['a', 'script', 'iframe', 'embed', 'object', 'link', 'img', 'source']):
        raw = tag.get('href') or tag.get('src') or tag.get('data') or ''
        if raw:
            absolute = urljoin(response.url, raw)
            blob = (absolute + ' ' + str(tag)).lower()
            if any(key in blob for key in ['.pdf', 'download', 'attach', 'report', '研报', 'detail', 'api', 'file']):
                links.append({
                    'tag': tag.name,
                    'text': ' '.join(tag.get_text(' ', strip=True).split())[:200],
                    'raw': raw,
                    'url': absolute,
                })

    regex_urls = re.findall(r'https?://[^\"\'<>\s]+', html, re.I)
    for candidate in regex_urls:
        if any(key in candidate.lower() for key in ['.pdf', 'download', 'attach', 'report', 'api', 'file']):
            links.append({'tag': 'regex', 'text': '', 'raw': candidate, 'url': candidate})

    suspicious = []
    patterns = [
        r'(?i)(?:pdf|download|attach|file|report)[A-Za-z0-9_\-]*\s*[:=]\s*[\"\']([^\"\']+)[\"\']',
        r'(?i)[\"\']([^\"\']+\.pdf(?:\?[^\"\']*)?)[\"\']',
        r'(?i)[\"\']([^\"\']*(?:download|attach|report)[^\"\']*)[\"\']',
    ]
    for pattern in patterns:
        suspicious.extend(re.findall(pattern, html))

    dedup_links = []
    seen = set()
    for item in links:
        key = item['url']
        if key not in seen:
            seen.add(key)
            dedup_links.append(item)

    record = {
        'label': label,
        'requested_url': url,
        'final_url': response.url,
        'status': response.status_code,
        'bytes': len(response.content),
        'encoding': response.encoding,
        'title': soup.title.get_text(' ', strip=True) if soup.title else '',
        'links': dedup_links,
        'suspicious_strings': list(dict.fromkeys(suspicious))[:200],
    }
    summary.append(record)
    print('DETAIL', json.dumps(record, ensure_ascii=False))

(OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
