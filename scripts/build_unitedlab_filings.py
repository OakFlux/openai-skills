#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

OUT = Path('output')
DEBUG = OUT / 'debug'
OUT.mkdir(parents=True, exist_ok=True)
DEBUG.mkdir(parents=True, exist_ok=True)

HKEX_BASE = 'https://www1.hkexnews.hk'
HKEX_API = HKEX_BASE + '/search/titleSearchServlet.do'
STOCK_ID = '15767'
TODAY = '20260922'

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
})


def get(url: str, *, params=None, headers=None, timeout=90) -> requests.Response:
    merged = dict(S.headers)
    if headers:
        merged.update(headers)
    last = None
    for attempt in range(5):
        try:
            r = S.get(url, params=params, headers=merged, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return r
            last = RuntimeError(f'HTTP {r.status_code} for {r.url}: {r.text[:200]}')
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f'Failed to fetch {url}: {last}')


def unwrap_redirect(url: str) -> str:
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if qs.get('url'):
            return unquote(qs['url'][0])
    except Exception:
        pass
    return url


def normalize_rows(obj):
    if isinstance(obj, str):
        try:
            return normalize_rows(json.loads(obj))
        except Exception:
            return []
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return obj
        for value in obj:
            rows = normalize_rows(value)
            if rows:
                return rows
        return []
    if isinstance(obj, dict):
        for key in ('result', 'data', 'rows', 'records', 'items'):
            if key in obj:
                rows = normalize_rows(obj[key])
                if rows:
                    return rows
        for value in obj.values():
            rows = normalize_rows(value)
            if rows:
                return rows
    return []


def query_hkex(lang: str, from_date: str, to_date: str, t2code: str, label: str):
    params = {
        'sortDir': '1',
        'sortByOptions': 'DateTime',
        'category': '0',
        'market': 'SEHK',
        'stockId': STOCK_ID,
        'documentType': '-1',
        'fromDate': from_date,
        'toDate': to_date,
        'title': '',
        'searchType': '1',
        't1code': '40000' if t2code != '-2' else '-2',
        't2Gcode': '-2',
        't2code': t2code,
        'rowRange': '2000',
        'lang': lang,
    }
    headers = {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': HKEX_BASE + '/search/titlesearch.xhtml?category=0&lang=' + ('ZH' if lang == 'zh' else 'EN') + '&market=SEHK&stockId=' + STOCK_ID,
        'X-Requested-With': 'XMLHttpRequest',
    }
    r = get(HKEX_API, params=params, headers=headers, timeout=60)
    (DEBUG / f'{label}_{lang}_{t2code}.txt').write_bytes(r.content)
    try:
        obj = r.json()
    except Exception:
        text = r.text.strip()
        left, right = text.find('('), text.rfind(')')
        if left >= 0 and right > left:
            obj = json.loads(text[left + 1:right])
        else:
            raise
    rows = normalize_rows(obj)
    print('HKEX_QUERY', label, lang, t2code, 'rows=', len(rows), 'url=', r.url)
    for row in rows[:15]:
        print('HKEX_ROW', json.dumps({k: row.get(k) for k in ('DATE_TIME', 'TITLE', 'FILE_LINK', 'FILE_INFO', 'T2_CODE')}, ensure_ascii=False))
    return rows


def title_of(row: dict) -> str:
    return str(row.get('TITLE') or row.get('title') or '').strip()


def date_of(row: dict) -> str:
    return str(row.get('DATE_TIME') or row.get('dateTime') or row.get('date') or '').strip()


def link_of(row: dict) -> str:
    link = str(row.get('FILE_LINK') or row.get('fileLink') or row.get('href') or row.get('url') or '').strip()
    return unwrap_redirect(urljoin(HKEX_BASE + '/', link)) if link else ''


def scrape_pdf_rows(url: str, label: str):
    r = get(url, timeout=60)
    (DEBUG / f'{label}.html').write_bytes(r.content)
    soup = BeautifulSoup(r.text, 'html.parser')
    rows = []
    for a in soup.find_all('a', href=True):
        raw = urljoin(r.url, a['href'])
        href = unwrap_redirect(raw)
        text = ' '.join(a.get_text(' ', strip=True).split())
        parent = a.find_parent(['li', 'tr', 'p', 'div'])
        context = ' '.join((parent.get_text(' ', strip=True) if parent else text).split())
        if '.pdf' in href.lower() or 'redirect.cgi' in raw.lower():
            rows.append({'TITLE': text or context, 'CONTEXT': context, 'FILE_LINK': href, 'DATE_TIME': ''})
    print('SCRAPED', label, len(rows))
    for row in rows:
        print('SCRAPED_ROW', json.dumps(row, ensure_ascii=False))
    return rows


def dedupe(rows):
    seen = set()
    out = []
    for row in rows:
        url = link_of(row)
        if not url or url in seen:
            continue
        seen.add(url)
        copy = dict(row)
        copy['FILE_LINK'] = url
        out.append(copy)
    return out


def inspect_pdf(url: str, temp_name: str):
    r = get(url, timeout=180)
    data = r.content
    if not data.startswith(b'%PDF-'):
        raise RuntimeError(f'Not PDF: {url}; head={data[:40]!r}; content-type={r.headers.get("content-type")}')
    if len(data) < 40000:
        raise RuntimeError(f'PDF too small: {url}; bytes={len(data)}')
    temp = DEBUG / temp_name
    temp.write_bytes(data)
    reader = PdfReader(str(temp))
    if reader.is_encrypted:
        raise RuntimeError(f'Encrypted PDF: {url}')
    pages = len(reader.pages)
    first = ''
    try:
        first = ' '.join((reader.pages[0].extract_text() or '').split())[:2000]
    except Exception:
        pass
    return {
        'url': url,
        'final_url': r.url,
        'data': data,
        'bytes': len(data),
        'pages': pages,
        'first_text': first,
    }


def save_direct(url: str, filename: str, display_title: str, category: str, min_pages: int, source_date=''):
    rec = inspect_pdf(url, re.sub(r'[^A-Za-z0-9]+', '_', filename) + '.tmp.pdf')
    if rec['pages'] < min_pages:
        raise RuntimeError(f'{filename}: page count {rec["pages"]} < {min_pages}')
    path = OUT / filename
    path.write_bytes(rec['data'])
    meta = {
        'filename': filename,
        'title': display_title,
        'category': category,
        'source_title': display_title,
        'source_date': source_date,
        'source_url': url,
        'final_url': rec['final_url'],
        'pages': rec['pages'],
        'bytes': rec['bytes'],
        'sha256': hashlib.sha256(rec['data']).hexdigest(),
        'first_page_text_excerpt': rec['first_text'],
    }
    print('SAVED_DIRECT', json.dumps(meta, ensure_ascii=False))
    return path, meta


def choose_candidate(rows, filename, display_title, category, min_pages, validator, max_candidates=30):
    valid = []
    errors = []
    for idx, row in enumerate(dedupe(rows)[:max_candidates], 1):
        url = link_of(row)
        try:
            print('TRY_CANDIDATE', category, title_of(row), url)
            rec = inspect_pdf(url, f'{re.sub(r"[^A-Za-z0-9]+", "_", category)}_{idx}.pdf')
            if rec['pages'] < min_pages:
                raise RuntimeError(f'page count {rec["pages"]} < {min_pages}')
            if not validator(row, rec):
                raise RuntimeError('validator rejected candidate')
            score = rec['pages'] * 1_000_000 + rec['bytes']
            valid.append((score, row, rec))
            print('VALID_CANDIDATE', category, rec['pages'], rec['bytes'], url)
        except Exception as exc:
            errors.append({'title': title_of(row), 'url': url, 'error': repr(exc)})
            print('CANDIDATE_FAILED', category, url, repr(exc))
    if not valid:
        raise RuntimeError(f'No valid candidate for {category}: {json.dumps(errors, ensure_ascii=False)}')
    valid.sort(key=lambda x: x[0], reverse=True)
    _, row, rec = valid[0]
    path = OUT / filename
    path.write_bytes(rec['data'])
    meta = {
        'filename': filename,
        'title': display_title,
        'category': category,
        'source_title': title_of(row),
        'source_date': date_of(row),
        'source_url': rec['url'],
        'final_url': rec['final_url'],
        'pages': rec['pages'],
        'bytes': rec['bytes'],
        'sha256': hashlib.sha256(rec['data']).hexdigest(),
        'first_page_text_excerpt': rec['first_text'],
    }
    print('SELECTED', json.dumps(meta, ensure_ascii=False))
    return path, meta


def prospectus_validator(row, rec):
    text = (title_of(row) + ' ' + str(row.get('CONTEXT') or '') + ' ' + rec['first_text']).upper()
    company_ok = 'UNITED LABORATORIES' in text or '聯邦製藥' in text or '联邦制药' in text
    document_ok = any(k in text for k in ('PROSPECTUS', 'GLOBAL OFFERING', '招股章程', '招股書', '招股说明书'))
    return company_ok and (document_ok or rec['pages'] >= 250)


def full_interim_validator(row, rec):
    text = (title_of(row) + ' ' + rec['first_text']).upper()
    return '2026' in text and any(k in text for k in ('INTERIM REPORT', '中期報告', '中期报告'))


def interim_results_validator(row, rec):
    text = (title_of(row) + ' ' + rec['first_text']).upper()
    return '2026' in text and any(k in text for k in ('INTERIM RESULTS', '中期業績', '中期业绩', 'SIX MONTHS ENDED 30 JUNE 2026'))


docs = []

for index, year in enumerate(range(2020, 2026), 1):
    url = f'https://doc.irasia.com/listco/hk/unitedlab/annual/{year}/ar{year}.pdf'
    docs.append(save_direct(
        url,
        f'{index:02d}_联邦制药_{year}年年报.pdf',
        f'联邦制药国际控股有限公司 - {year}年年报',
        f'{year}年年报',
        min_pages=70,
        source_date=str(year),
    ))

listing_rows = scrape_pdf_rows('https://www.irasia.com/listco/hk/unitedlab/listingdoc/07index.htm', 'listing_2007')
hkex_prospectus_rows = []
for lang in ('zh', 'en'):
    try:
        hkex_prospectus_rows += query_hkex(lang, '20070101', '20071231', '40500', 'prospectus')
    except Exception as exc:
        print('PROSPECTUS_QUERY_FAILED', lang, repr(exc))
prospectus_rows = []
for row in listing_rows + hkex_prospectus_rows:
    text = (title_of(row) + ' ' + str(row.get('CONTEXT') or '')).upper()
    if any(k in text for k in ('PROSPECTUS', 'GLOBAL OFFERING', '招股章程', '招股書', '招股说明书', '招股')):
        if not any(x in text for x in ('FORMAL NOTICE', 'APPLICATION FORM', 'WHITE FORM', 'YELLOW FORM', '正式通告', '申請表', '申请表')):
            prospectus_rows.append(row)
if not prospectus_rows:
    prospectus_rows = listing_rows + hkex_prospectus_rows

docs.append(choose_candidate(
    prospectus_rows,
    '07_联邦制药_招股说明书_2007.pdf',
    '联邦制药国际控股有限公司 - 首次公开发行最终版招股说明书',
    '招股说明书',
    min_pages=120,
    validator=prospectus_validator,
    max_candidates=40,
))

interim_rows = []
recent_rows = []
for lang in ('zh', 'en'):
    try:
        interim_rows += query_hkex(lang, '20260101', TODAY, '40300', 'interim')
    except Exception as exc:
        print('INTERIM_QUERY_FAILED', lang, repr(exc))
    try:
        recent_rows += query_hkex(lang, '20260801', TODAY, '-2', 'recent')
    except Exception as exc:
        print('RECENT_QUERY_FAILED', lang, repr(exc))

full_rows = []
for row in interim_rows:
    text = title_of(row).upper()
    if '2026' in text and any(k in text for k in ('INTERIM REPORT', '中期報告', '中期报告')):
        if not any(x in text for x in ('NOTICE', '通知', 'LETTER')):
            full_rows.append(row)

latest_basis = ''
try:
    latest_path, latest_meta = choose_candidate(
        full_rows,
        '08_联邦制药_2026年中期报告_最新定期财报.pdf',
        '联邦制药国际控股有限公司 - 2026年中期报告',
        '最新定期财报',
        min_pages=20,
        validator=full_interim_validator,
        max_candidates=15,
    )
    latest_basis = '截至2026年9月22日已发现并收录2026年完整中期报告。'
except Exception as full_exc:
    print('FULL_INTERIM_NOT_AVAILABLE', repr(full_exc))
    result_rows = []
    for row in recent_rows:
        text = title_of(row).upper()
        if any(k in text for k in ('INTERIM RESULTS', '中期業績', '中期业绩')):
            if 'CLARIFICATION' not in text and '澄清' not in text:
                result_rows.append(row)
    latest_path, latest_meta = choose_candidate(
        result_rows,
        '08_联邦制药_2026年中期业绩公告_最新财务披露.pdf',
        '联邦制药国际控股有限公司 - 截至2026年6月30日止六个月中期业绩公告',
        '最新财务披露',
        min_pages=5,
        validator=interim_results_validator,
        max_candidates=20,
    )
    latest_basis = '截至2026年9月22日，港交所尚未检索到2026年完整中期报告；收录2026年8月31日中期业绩公告作为最新财务披露。'
latest_meta['latest_document_basis'] = latest_basis
docs.append((latest_path, latest_meta))

manifest = [meta for _, meta in docs]

note = f'''联邦制药国际控股有限公司（03933.HK）官方披露文件资料包

文件范围：
1. 2020年至2025年年度报告，共6份。
2. 2007年首次公开发行最终版招股说明书，共1份。
3. 最新中期财务披露，共1份。

最新财报口径：
- 香港主板发行人通常披露年度报告和中期报告，并不强制发布季度报告。
- {latest_basis}

文件说明：
- 年报优先采用公司投资者关系网站公开PDF；招股说明书及最新财务披露优先采用香港交易所披露易或公司投资者关系网站公开PDF。
- 未重复收录业绩补充公告、ESG报告、申请表或配售文件；招股说明书仅保留最终版。
- 每份PDF均已检查文件头、实际页数、加密状态和SHA-256校验值。
- 仅供个人研究与学习使用，请遵守原始文件的版权及免责声明。
'''
(OUT / '资料说明.txt').write_text(note, encoding='utf-8')
(OUT / '文件清单及校验值.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

zip_path = OUT / '联邦制药_2020-2025年报_招股说明书_最新财报.zip'
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for path, _ in docs:
        zf.write(path, path.name)
    zf.write(OUT / '资料说明.txt', '资料说明.txt')
    zf.write(OUT / '文件清单及校验值.json', '文件清单及校验值.json')
with zipfile.ZipFile(zip_path) as zf:
    bad = zf.testzip()
    if bad:
        raise RuntimeError(f'ZIP CRC validation failed at {bad}')

summary = {
    'zip': zip_path.name,
    'zip_bytes': zip_path.stat().st_size,
    'zip_sha256': hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    'documents': manifest,
}
(OUT / 'BUILD_SUMMARY.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print('FINAL_SUMMARY', json.dumps(summary, ensure_ascii=False, indent=2))
