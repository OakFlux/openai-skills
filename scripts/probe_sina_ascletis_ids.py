#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
REPORTS = {
    '5170842': '中信建投_肝病领域新星关注NASH研发进展',
    '5607547': '东北证券_2023年年度业绩公告点评',
    '5527741': '光大证券_ASC41二期期中数据',
    '5289480': '国元国际_ASC40治疗痤疮II期数据优秀',
    '5233323': '国元国际_新药研发推进顺利',
}

session = requests.Session()
session.headers.update({
    'User-Agent': UA,
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://finance.sina.com.cn/',
})
out = Path('sina_ascletis_probe')
out.mkdir(exist_ok=True)
summary = []


def decode(response):
    choices = []
    for enc in (response.encoding, response.apparent_encoding, 'utf-8', 'gb18030'):
        if not enc:
            continue
        try:
            text = response.content.decode(enc)
            choices.append((text.count('�'), -len(text), enc, text))
        except Exception:
            pass
    choices.sort()
    return choices[0][2], choices[0][3]


def probe_pdf(label, url, referer):
    try:
        response = session.get(url, headers={'User-Agent': UA, 'Referer': referer, 'Accept': 'application/pdf,*/*'}, timeout=(20, 180), allow_redirects=True)
        record = {
            'label': label,
            'requested': url,
            'resolved': response.url,
            'status': response.status_code,
            'content_type': response.headers.get('content-type'),
            'content_length': response.headers.get('content-length'),
            'bytes': len(response.content),
            'head': response.content[:12].hex(),
            'history': [(r.status_code, r.url, r.headers.get('location')) for r in response.history],
        }
        if response.status_code == 200 and response.content.startswith(b'%PDF-'):
            path = out / f'{label}.pdf'
            path.write_bytes(response.content)
            record['pdf_path'] = str(path)
            print('FOUND_PDF', json.dumps(record, ensure_ascii=False), flush=True)
        else:
            print('PROBE', json.dumps(record, ensure_ascii=False), flush=True)
        return record
    except Exception as exc:
        record = {'label': label, 'requested': url, 'error': repr(exc)}
        print('ERROR', json.dumps(record, ensure_ascii=False), flush=True)
        return record


for rptid, title in REPORTS.items():
    entry = {'rptid': rptid, 'title': title, 'pages': [], 'pdf_probes': []}
    variants = [
        f'https://vip.stock.finance.sina.com.cn/q/go.php/vReport_Show/kind/search/rptid/{rptid}/index.phtml',
        f'https://vip.stock.finance.sina.com.cn/q/go.php/vReport_Show/kind/lastest/rptid/{rptid}/index.phtml',
        f'https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/{rptid}/index.phtml',
        f'https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/lastest/rptid/{rptid}/index.phtml',
    ]
    discovered = []
    for index, page_url in enumerate(variants):
        try:
            response = session.get(page_url, timeout=(20, 120), allow_redirects=True)
            enc, text = decode(response)
            page_path = out / f'{rptid}_{index}.html'
            page_path.write_text(text, encoding='utf-8')
            soup = BeautifulSoup(text, 'html.parser')
            record = {
                'requested': page_url,
                'resolved': response.url,
                'status': response.status_code,
                'content_type': response.headers.get('content-type'),
                'bytes': len(response.content),
                'encoding': enc,
                'title': soup.title.get_text(' ', strip=True) if soup.title else '',
            }
            entry['pages'].append(record)
            print('PAGE', rptid, json.dumps(record, ensure_ascii=False), flush=True)
            for tag in soup.find_all(True):
                for attr in ('href', 'src', 'data-url', 'data-src', 'content', 'value'):
                    value = tag.get(attr)
                    if not isinstance(value, str) or not value.strip():
                        continue
                    url = urljoin(response.url, value.strip())
                    low = url.lower()
                    if '.pdf' in low or any(token in low for token in ('download', 'reportfile', 'attachment')):
                        discovered.append((url, response.url))
                        print('LINK', rptid, url, flush=True)
            for value in re.findall(r'(?:https?:)?//[^\s\"\'<>]+', text):
                url = urljoin(response.url, value).rstrip('),]};\"\'')
                if '.pdf' in url.lower():
                    discovered.append((url, response.url))
                    print('REGEX_LINK', rptid, url, flush=True)
        except Exception as exc:
            record = {'requested': page_url, 'error': repr(exc)}
            entry['pages'].append(record)
            print('PAGE_ERROR', rptid, repr(exc), flush=True)

    # Conventional Sina PDF URL guesses used by archived research reports.
    guesses = [
        f'https://pdf.dfcfw.com/pdf/H3_{rptid}_1.pdf',
        f'https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/{rptid}/index.phtml?download=1',
        f'https://vip.stock.finance.sina.com.cn/q/go.php/vReport_Show/kind/search/rptid/{rptid}/index.phtml?download=1',
    ]
    for url in guesses:
        discovered.append((url, variants[0]))

    seen = set()
    for number, (url, referer) in enumerate(discovered):
        if url in seen or len(url) > 1000:
            continue
        seen.add(url)
        record = probe_pdf(f'{rptid}_{number:03d}', url, referer)
        entry['pdf_probes'].append(record)
    summary.append(entry)

Path('sina_ascletis_probe.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print('SUMMARY', json.dumps([{'rptid': x['rptid'], 'pdfs': [p.get('pdf_path') for p in x['pdf_probes'] if p.get('pdf_path')]} for x in summary], ensure_ascii=False), flush=True)
