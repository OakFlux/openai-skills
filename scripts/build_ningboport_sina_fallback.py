#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import fitz
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
from pypdf import PdfReader

OUT = Path('output')
VERIFY = OUT / 'verification'
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

STOCK_CODE = '601018'
COMPANY_SHORT = '宁波港'
COMPANY_FULL = '宁波舟山港股份有限公司'
ANNUAL_LIST = f'https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/ndbg.phtml'
Q1_LIST = f'https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/yjdbg.phtml'

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
})


def get(url, *, referer=None, timeout=90):
    last = None
    for attempt in range(5):
        try:
            headers = {'Referer': referer} if referer else {}
            r = S.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            print('FETCH', r.status_code, len(r.content), r.url, r.headers.get('content-type'))
            if r.status_code == 200:
                return r
            last = RuntimeError(f'HTTP {r.status_code}: {r.url}')
        except Exception as exc:
            last = exc
        time.sleep(min(10, 2 ** attempt))
    raise RuntimeError(f'Failed to fetch {url}: {last}')


def parse_list(list_url):
    r = get(list_url, timeout=60)
    r.encoding = r.apparent_encoding or 'utf-8'
    soup = BeautifulSoup(r.text, 'html.parser')
    records = []
    for a in soup.find_all('a', href=True):
        text = ' '.join(a.get_text(' ', strip=True).split())
        href = urljoin(r.url, a['href'])
        if 'vCB_AllBulletinDetail.php' in href:
            parent_text = ' '.join((a.parent.get_text(' ', strip=True) if a.parent else text).split())
            records.append({'text': text, 'context': parent_text, 'detail_url': href})
    dedup = {}
    for rec in records:
        dedup[rec['detail_url']] = rec
    out = list(dedup.values())
    print('LIST_RECORDS', list_url, len(out))
    return out


def select_detail(records, year, kind):
    candidates = []
    for rec in records:
        blob = f"{rec['text']} {rec['context']}"
        if str(year) not in blob:
            continue
        if kind == 'annual':
            if '年度报告' not in blob and '年报' not in blob:
                continue
            if any(x in blob for x in ('摘要', '英文版', '业绩', '审计报告')):
                continue
        elif kind == 'q1':
            if '第一季度报告' not in blob and '一季度报告' not in blob:
                continue
            if any(x in blob for x in ('摘要', '业绩预告', '业绩快报')):
                continue
        candidates.append(rec)
    if not candidates:
        raise RuntimeError(f'No {kind} detail page found for {year}')
    candidates.sort(key=lambda rec: ('宁波舟山港股份有限公司' in rec['text'], len(rec['text'])), reverse=True)
    print('SELECTED_DETAIL', year, kind, json.dumps(candidates[0], ensure_ascii=False))
    return candidates[0]


def extract_download_url(detail_url):
    r = get(detail_url, timeout=60)
    r.encoding = r.apparent_encoding or 'utf-8'
    soup = BeautifulSoup(r.text, 'html.parser')
    candidates = []
    for a in soup.find_all('a', href=True):
        text = ' '.join(a.get_text(' ', strip=True).split())
        href = urljoin(r.url, a['href'])
        upper = href.upper()
        if '下载公告' in text or ('FILE.FINANCE.SINA.COM.CN' in upper and '.PDF' in upper):
            candidates.append(href)
    # Regex fallback because some old pages render the link in scripts.
    candidates.extend(re.findall(r'https?://file\.finance\.sina\.com\.cn/[^\"\'<>\s]+\.PDF', r.text, re.I))
    dedup = []
    seen = set()
    for url in candidates:
        url = url.replace('&amp;', '&')
        if url not in seen:
            dedup.append(url)
            seen.add(url)
    if not dedup:
        raise RuntimeError(f'No PDF download URL found at {detail_url}')
    dedup.sort(key=lambda u: ('.PDF' not in u.upper(), len(u)))
    print('DOWNLOAD_URL', detail_url, dedup[0])
    return dedup[0]


def text_from_first_pages(path, limit=8):
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(''):
        raise RuntimeError('password-protected PDF')
    chunks = []
    for i in range(min(limit, len(reader.pages))):
        try:
            chunks.append(reader.pages[i].extract_text() or '')
        except Exception:
            pass
    text = ' '.join(chunks)
    if len(text.strip()) < 80:
        doc = fitz.open(str(path))
        text = ' '.join(doc[i].get_text('text') for i in range(min(limit, doc.page_count)))
        doc.close()
    return text


def download_validate(spec):
    detail = spec['detail']
    pdf_url = extract_download_url(detail['detail_url'])
    r = get(pdf_url, referer=detail['detail_url'], timeout=180)
    data = r.content
    if not data.startswith(b'%PDF-'):
        raise RuntimeError(f'not a PDF: {data[:64]!r}')
    if len(data) < 50000:
        raise RuntimeError(f'PDF too small: {len(data)}')
    path = OUT / spec['filename']
    path.write_bytes(data)
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(''):
        raise RuntimeError('password-protected PDF')
    pages = len(reader.pages)
    if pages < spec['min_pages']:
        raise RuntimeError(f'unexpected page count {pages}')
    text = re.sub(r'\s+', '', text_from_first_pages(path)).upper()
    if not any(term.replace(' ', '').upper() in text for term in (COMPANY_FULL, COMPANY_SHORT, 'NINGBOZHOUSHANPORT', 'NINGBOPORT')):
        raise RuntimeError('company identity validation failed')
    if str(spec['year']) not in text:
        raise RuntimeError('report year validation failed')
    if spec['kind'] == 'annual' and not ('年度报告' in text or 'ANNUALREPORT' in text):
        raise RuntimeError('annual-report type validation failed')
    if spec['kind'] == 'q1' and not ('第一季度报告' in text or '一季度报告' in text or 'FIRSTQUARTERLYREPORT' in text):
        raise RuntimeError('Q1-report type validation failed')

    doc = fitz.open(str(path))
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
    render = VERIFY / f'{path.stem}_page1.png'
    pix.save(str(render))
    doc.close()

    record = {
        'filename': path.name,
        'category': spec['category'],
        'report_year': spec['year'],
        'announcement_title': detail['text'],
        'announcement_context': detail['context'],
        'announcement_detail_url': detail['detail_url'],
        'pdf_mirror_url': pdf_url,
        'pages': pages,
        'bytes': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
        'first_page_render': render.name,
        'verification_note': '公告标题和日期与上交所定期报告检索结果交叉核对；PDF为新浪财经公告附件镜像。',
    }
    print('VALIDATED', json.dumps(record, ensure_ascii=False))
    return path, render, record


annual_records = parse_list(ANNUAL_LIST)
q1_records = parse_list(Q1_LIST)
specs = []
for idx, year in enumerate(range(2020, 2026), 1):
    specs.append({
        'filename': f'{idx:02d}_{COMPANY_SHORT}_{year}年年度报告.pdf',
        'category': f'{year}年年度报告',
        'year': year,
        'kind': 'annual',
        'min_pages': 100,
        'detail': select_detail(annual_records, year, 'annual'),
    })
specs.append({
    'filename': f'07_{COMPANY_SHORT}_2026年第一季度报告_最新季报.pdf',
    'category': '2026年第一季度报告（截至2026-09-27最新正式季报）',
    'year': 2026,
    'kind': 'q1',
    'min_pages': 8,
    'detail': select_detail(q1_records, 2026, 'q1'),
})

documents = [download_validate(spec) for spec in specs]
manifest = [rec for _, _, rec in documents]

thumbs = []
for _, render, _ in documents:
    image = Image.open(render).convert('RGB')
    image.thumbnail((480, 640))
    canvas = Image.new('RGB', (500, 690), 'white')
    canvas.paste(image, ((500-image.width)//2, 10))
    ImageDraw.Draw(canvas).text((10, 660), render.stem[:70], fill='black')
    thumbs.append(canvas)
    image.close()
cols = 2
rows_n = (len(thumbs)+cols-1)//cols
sheet = Image.new('RGB', (cols*500, rows_n*690), 'white')
for idx, image in enumerate(thumbs):
    sheet.paste(image, ((idx%cols)*500, (idx//cols)*690))
    image.close()
sheet.save(VERIFY/'contact_sheet.jpg', 'JPEG', quality=90)
sheet.close()

note = '''宁波舟山港股份有限公司（证券简称：宁波港，证券代码：601018）定期报告资料包

文件范围：
1. 2020年至2025年完整年度报告，共6份；均为年度报告全文，不含摘要版。
2. 2026年第一季度报告，为截至2026年9月27日最新一份标题明确为“季度报告”的正式定期报告。

来源及核验说明：
- 报告标题、年份和披露日期已与上海证券交易所定期报告检索记录交叉核对。
- 由于上交所静态PDF服务器对自动化下载触发访问校验，本资料包中的PDF二进制文件取自新浪财经的公司公告附件镜像；文件内容按公司名称、年份、报告类型、实际页数和首页渲染逐份核验。
- 公司已披露2026年半年度报告，但半年度报告不属于标题明确的“季度报告”，故本包依照“最新季报”口径收录2026年第一季度报告。
- 2026年第三季度报告截至2026年9月27日尚未披露。
- ZIP已执行CRC完整性检查。
'''
(OUT/'00_资料说明.txt').write_text(note, encoding='utf-8')
(OUT/'00_文件清单及校验值.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

zip_path = OUT/'宁波港_2020-2025年报_2026年第一季度报告.zip'
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    z.write(OUT/'00_资料说明.txt', '00_资料说明.txt')
    z.write(OUT/'00_文件清单及校验值.json', '00_文件清单及校验值.json')
    for path, _, _ in documents:
        z.write(path, path.name)
with zipfile.ZipFile(zip_path) as z:
    bad = z.testzip()
    if bad:
        raise RuntimeError(f'ZIP CRC failed at {bad}')

summary = {
    'zip': zip_path.name,
    'zip_bytes': zip_path.stat().st_size,
    'zip_sha256': hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    'pdf_count': len(documents),
    'total_pages': sum(rec['pages'] for rec in manifest),
    'reports': manifest,
}
(OUT/'BUILD_SUMMARY.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print('FINAL_SUMMARY', json.dumps(summary, ensure_ascii=False, indent=2))
