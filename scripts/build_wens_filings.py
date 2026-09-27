#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path

import fitz
import requests
from PIL import Image, ImageDraw

OUT = Path('output')
VERIFY = OUT / 'verification'
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

FILES = [
    ('01_温氏股份_2020年年度报告.pdf', 'https://www.wens.com.cn/uploadfiles/2021/04/20210422181223818.pdf', 345, '2020', '年度报告'),
    ('02_温氏股份_2021年年度报告.pdf', 'https://www.wens.com.cn/uploadfiles/2022/04/20220416103035134.pdf', 428, '2021', '年度报告'),
    ('03_温氏股份_2022年年度报告.pdf', 'https://www.wens.com.cn/uploadfiles/2023/04/20230426103745999.pdf', 373, '2022', '年度报告'),
    ('04_温氏股份_2023年年度报告.pdf', 'https://www.wens.com.cn/uploadfiles/2024/04/20240429155306097.pdf', 409, '2023', '年度报告'),
    ('05_温氏股份_2024年年度报告.pdf', 'https://www.wens.com.cn/uploadfiles/2025/04/20250424085426118.pdf', 417, '2024', '年度报告'),
    ('06_温氏股份_2025年年度报告.pdf', 'https://www.wens.com.cn/uploadfiles/2026/04/20260422104344852.pdf', 394, '2025', '年度报告'),
    ('07_温氏股份_2026年第一季度报告_最新季报.pdf', 'https://www.wens.com.cn/uploadfiles/2026/04/20260422104359416.pdf', 17, '2026', '第一季度报告'),
]

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://www.wens.com.cn/Investor/index.aspx',
})


def download(url):
    last = None
    for attempt in range(4):
        try:
            response = session.get(url, timeout=90, allow_redirects=True)
            print('FETCH', response.status_code, len(response.content), response.url, response.headers.get('content-type'), flush=True)
            response.raise_for_status()
            return response.content, response.url
        except Exception as exc:
            last = exc
            time.sleep(min(8, 2 ** attempt))
    raise RuntimeError(f'Failed to download {url}: {last}')


manifest = []
renders = []
for filename, url, expected_pages, year, doc_type in FILES:
    data, final_url = download(url)
    if not data.startswith(b'%PDF-'):
        raise RuntimeError(f'{filename} is not PDF: {data[:40]!r}')
    if len(data) < 100_000:
        raise RuntimeError(f'{filename} is unexpectedly small: {len(data)}')
    path = OUT / filename
    path.write_bytes(data)

    doc = fitz.open(str(path))
    encrypted = bool(doc.needs_pass)
    if encrypted and not doc.authenticate(''):
        doc.close()
        raise RuntimeError(f'{filename} is password protected')
    pages = doc.page_count
    if pages != expected_pages:
        doc.close()
        raise RuntimeError(f'{filename} page mismatch: {pages} != {expected_pages}')

    text = ''
    for page_index in range(min(3, pages)):
        text += doc[page_index].get_text('text') or ''
    normalized = re.sub(r'\s+', '', text)
    if '温氏食品集团股份有限公司' not in normalized and '温氏股份' not in normalized:
        doc.close()
        raise RuntimeError(f'{filename} company identity check failed')
    if year not in normalized:
        doc.close()
        raise RuntimeError(f'{filename} report year check failed')
    if doc_type not in normalized:
        doc.close()
        raise RuntimeError(f'{filename} document type check failed')

    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.15, 1.15), alpha=False)
    render = VERIFY / f'{path.stem}_page1.png'
    pix.save(str(render))
    doc.close()
    renders.append(render)

    record = {
        'filename': filename,
        'source_url': url,
        'final_url': final_url,
        'year': year,
        'document_type': doc_type,
        'pages': pages,
        'bytes': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
        'encrypted': encrypted,
        'first_page_render': render.name,
    }
    manifest.append(record)
    print('VALIDATED', json.dumps(record, ensure_ascii=False), flush=True)

# Contact sheet for visual inspection.
thumbs = []
for render in renders:
    image = Image.open(render).convert('RGB')
    image.thumbnail((440, 600))
    canvas = Image.new('RGB', (460, 650), 'white')
    canvas.paste(image, ((460 - image.width) // 2, 8))
    ImageDraw.Draw(canvas).text((10, 622), render.stem[:68], fill='black')
    thumbs.append(canvas)
    image.close()
cols = 2
rows_n = (len(thumbs) + cols - 1) // cols
sheet = Image.new('RGB', (cols * 460, rows_n * 650), 'white')
for index, canvas in enumerate(thumbs):
    sheet.paste(canvas, ((index % cols) * 460, (index // cols) * 650))
    canvas.close()
contact_sheet = VERIFY / 'contact_sheet.jpg'
sheet.save(contact_sheet, 'JPEG', quality=88)
sheet.close()

note = '''温氏食品集团股份有限公司（证券简称：温氏股份，证券代码：300498）定期报告资料包

文件范围：
1. 2020年至2025年年度报告全文，共6份。
2. 2026年第一季度报告，为截至2026年9月27日温氏股份投资者关系页面列示的最新季度报告。

来源与核验：
- 全部PDF直接下载自温氏股份官方网站“投资者关系-定期报告”页面。
- 已逐份核验PDF文件头、实际页数、加密状态、公司名称、报告年份和报告类型。
- 已渲染每份PDF首页进行可视化检查；ZIP已执行CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始文件的版权和免责声明。
'''
(OUT / '00_资料说明.txt').write_text(note, encoding='utf-8')
(OUT / '00_文件清单及校验值.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

zip_path = OUT / '温氏股份_2020-2025年报_2026年第一季度报告.zip'
# PDFs are already compressed; storing avoids expensive and ineffective recompression.
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as archive:
    archive.write(OUT / '00_资料说明.txt', '00_资料说明.txt')
    archive.write(OUT / '00_文件清单及校验值.json', '00_文件清单及校验值.json')
    for filename, *_ in FILES:
        archive.write(OUT / filename, filename)
with zipfile.ZipFile(zip_path) as archive:
    bad_file = archive.testzip()
    if bad_file:
        raise RuntimeError(f'ZIP CRC validation failed at {bad_file}')

summary = {
    'zip': zip_path.name,
    'zip_bytes': zip_path.stat().st_size,
    'zip_sha256': hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    'pdf_count': len(FILES),
    'total_pages': sum(record['pages'] for record in manifest),
    'reports': manifest,
}
(OUT / 'BUILD_SUMMARY.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print('FINAL_SUMMARY', json.dumps({k: summary[k] for k in ('zip', 'zip_bytes', 'zip_sha256', 'pdf_count', 'total_pages')}, ensure_ascii=False), flush=True)
