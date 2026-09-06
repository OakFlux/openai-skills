#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
from urllib.parse import urljoin
import json
import re
import requests
from bs4 import BeautifulSoup

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
REPORTS = {
    '758029035672': '光大证券_2024_ASC41二期期中数据',
    '732723379903': '国元国际_2023_新药研发推进顺利',
    '645790628897': '平安证券_2020_ASC40二期结果良好',
    '787429061043': '国元国际_2024_减重不减肌研发推进',
}
BASE = 'https://gmg.9fzt.com/report/HKSE/01672/'
s = requests.Session()
s.headers.update({'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.7'})
out = Path('ascletis_9fzt_probe')
out.mkdir(exist_ok=True)
summary = []


def decode(r):
    candidates = []
    for enc in (r.encoding, r.apparent_encoding, 'utf-8', 'gb18030'):
        if not enc:
            continue
        try:
            txt = r.content.decode(enc)
            candidates.append((txt.count('�'), -len(txt), enc, txt))
        except Exception:
            pass
    candidates.sort()
    return candidates[0][2], candidates[0][3]


def probe(url, referer, label):
    try:
        r = s.get(url, headers={'User-Agent': UA, 'Referer': referer, 'Accept': 'application/pdf,image/*,text/html,*/*'}, timeout=(20, 180), allow_redirects=True)
        rec = {
            'label': label, 'requested': url, 'resolved': r.url, 'status': r.status_code,
            'type': r.headers.get('content-type'), 'disposition': r.headers.get('content-disposition'),
            'bytes': len(r.content), 'head': r.content[:16].hex(),
            'history': [(x.status_code, x.url, x.headers.get('location')) for x in r.history],
        }
        if r.status_code == 200 and r.content.startswith(b'%PDF-'):
            path = out / f'{label}.pdf'
            path.write_bytes(r.content)
            rec['pdf_path'] = str(path)
            print('FOUND_PDF', json.dumps(rec, ensure_ascii=False), flush=True)
        else:
            print('PROBE', json.dumps(rec, ensure_ascii=False), flush=True)
        return rec
    except Exception as exc:
        rec = {'label': label, 'requested': url, 'error': repr(exc)}
        print('ERROR', json.dumps(rec, ensure_ascii=False), flush=True)
        return rec


for report_id, title in REPORTS.items():
    page_url = BASE + report_id + '.html'
    entry = {'id': report_id, 'title': title, 'page': page_url, 'links': [], 'probes': []}
    try:
        r = s.get(page_url, timeout=(20, 180), allow_redirects=True)
        enc, text = decode(r)
        (out / f'{report_id}.html').write_text(text, encoding='utf-8')
        soup = BeautifulSoup(text, 'html.parser')
        meta = {
            'status': r.status_code, 'resolved': r.url, 'bytes': len(r.content),
            'type': r.headers.get('content-type'), 'encoding': enc,
            'title': soup.title.get_text(' ', strip=True) if soup.title else '',
        }
        entry['meta'] = meta
        print('PAGE', report_id, json.dumps(meta, ensure_ascii=False), flush=True)

        candidates = []
        for tag in soup.find_all(True):
            for attr in ('href', 'src', 'data-src', 'data-url', 'data-file', 'data-pdf', 'content', 'value', 'action'):
                value = tag.get(attr)
                if isinstance(value, str) and value.strip():
                    u = urljoin(r.url, value.strip())
                    hay = (u + ' ' + value).lower()
                    if any(k in hay for k in ('pdf', 'download', 'report', 'file', 'attachment', report_id)):
                        candidates.append({'tag': tag.name, 'attr': attr, 'raw': value.strip(), 'url': u})
        for pattern in (
            r'(?:https?:)?//[^\s\"\'<>\\]+',
            r'/[^\s\"\'<>\\]*(?:pdf|download|report|file|attachment)[^\s\"\'<>\\]*',
        ):
            for value in re.findall(pattern, text, flags=re.I):
                u = urljoin(r.url, value).rstrip('),]};\"\'')
                candidates.append({'tag': 'regex', 'attr': 'text', 'raw': value, 'url': u})

        seen = set()
        dedup = []
        for item in candidates:
            u = item['url']
            if u in seen or len(u) > 1000:
                continue
            seen.add(u)
            dedup.append(item)
        entry['links'] = dedup
        for item in dedup[:200]:
            print('LINK', report_id, json.dumps(item, ensure_ascii=False), flush=True)

        for i, item in enumerate(dedup[:160]):
            u = item['url']
            if any(x in u.lower() for x in ('.js', '.css', 'favicon', 'logo', 'icon')):
                continue
            entry['probes'].append(probe(u, r.url, f'{report_id}_{i:03d}'))

        # Conservative conventional paths from page origin only.
        guesses = [
            page_url.replace('.html', '.pdf'),
            page_url.replace('/report/', '/report/pdf/').replace('.html', '.pdf'),
            f'https://gmg.9fzt.com/report/pdf/{report_id}.pdf',
            f'https://gmg.9fzt.com/download/report/{report_id}',
            f'https://gmg.9fzt.com/report/download/{report_id}',
        ]
        for i, u in enumerate(guesses):
            if u not in seen:
                entry['probes'].append(probe(u, r.url, f'{report_id}_guess_{i}'))
    except Exception as exc:
        entry['error'] = repr(exc)
        print('PAGE_ERROR', report_id, repr(exc), flush=True)
    summary.append(entry)

Path('ascletis_9fzt_probe.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print('SUMMARY', json.dumps([{'id': e['id'], 'pdfs': [p.get('pdf_path') for p in e.get('probes', []) if p.get('pdf_path')]} for e in summary], ensure_ascii=False), flush=True)
