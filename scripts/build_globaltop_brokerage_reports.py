#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path

import pymupdf
import requests
from PIL import Image, ImageDraw
from pypdf import PdfReader

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

REPORTS = [
    {
        "filename": "01_新时代证券_跨境通_深耕跨境电商细分领域_提高平台优势降本增效_2018-06-01_19页.pdf",
        "broker": "新时代证券",
        "title": "深耕跨境电商细分领域 提高平台优势降本增效",
        "date": "2018-06-01",
        "authors": "陈文倩",
        "rating": "增持",
        "pages": 19,
        "classification": "公司深度研究报告",
        "info_code": "AP201806011151657961",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP201806011151657961_1.pdf",
    },
    {
        "filename": "02_西南证券_跨境通_新平台促业绩大增_经营改善稳固龙头地位_2018-08-28_8页.pdf",
        "broker": "西南证券",
        "title": "新平台促业绩大增，经营改善稳固龙头地位",
        "date": "2018-08-28",
        "authors": "蔡欣",
        "rating": "增持",
        "pages": 8,
        "classification": "公司研究报告",
        "info_code": "AP201808281183930140",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP201808281183930140_1.pdf",
    },
    {
        "filename": "03_东吴证券_跨境通_现金流状况改善业绩稳步增长_运营效率提升竞争力增强_2018-08-28_8页.pdf",
        "broker": "东吴证券",
        "title": "现金流状况改善业绩稳步增长，运营效率提升竞争力增强",
        "date": "2018-08-28",
        "authors": "马莉、陈腾曦、林骥川",
        "rating": "买入",
        "pages": 8,
        "classification": "公司研究报告",
        "info_code": "AP201808281183786995",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP201808281183786995_1.pdf",
    },
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://data.eastmoney.com/report/002640.html",
    }
)


def download(url: str, timeout: int = 180):
    last = None
    for attempt in range(5):
        try:
            r = SESSION.get(url, timeout=timeout, allow_redirects=True)
            print("FETCH", r.status_code, len(r.content), r.url, r.headers.get("content-type"))
            if r.status_code == 200:
                return r.content, r.url
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2 ** attempt))
    raise RuntimeError(f"Failed to download {url}: {last}")


def extract_text(path: Path, max_pages: int = 6) -> str:
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    chunks = []
    for idx in range(min(max_pages, len(reader.pages))):
        try:
            chunks.append(reader.pages[idx].extract_text() or "")
        except Exception:
            pass
    text = " ".join(chunks)
    if len(text.strip()) < 100:
        doc = pymupdf.open(str(path))
        text = " ".join(doc[idx].get_text("text") for idx in range(min(max_pages, doc.page_count)))
        doc.close()
    return re.sub(r"\s+", "", text)


manifest = []
render_paths = []
for spec in REPORTS:
    data, final_url = download(spec["url"])
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"Not a PDF: {spec['filename']} head={data[:32]!r}")
    if len(data) < 150_000:
        raise RuntimeError(f"PDF too small: {spec['filename']} bytes={len(data)}")

    path = OUT / spec["filename"]
    path.write_bytes(data)
    reader = PdfReader(str(path))
    encrypted = reader.is_encrypted
    if encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = len(reader.pages)
    if pages != spec["pages"]:
        raise RuntimeError(f"Page count mismatch for {path.name}: {pages} != {spec['pages']}")

    text = extract_text(path)
    normalized = text.upper()
    if "跨境通" not in text and "002640" not in text:
        raise RuntimeError(f"Company identity validation failed: {path.name}")
    broker_terms = [spec["broker"], spec["broker"].replace("证券", "")]
    if not any(term in text for term in broker_terms):
        raise RuntimeError(f"Broker identity validation failed: {path.name}")
    if not any(token in text for token in (spec["title"].replace("，", ""), spec["title"][:8])):
        raise RuntimeError(f"Title validation failed: {path.name}")

    doc = pymupdf.open(str(path))
    for page_index, suffix in ((0, "page1"), (doc.page_count - 1, "lastpage")):
        pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(1.35, 1.35), alpha=False)
        render = VERIFY / f"{path.stem}_{suffix}.png"
        pix.save(str(render))
        render_paths.append(render)
    doc.close()

    record = {
        "filename": path.name,
        "broker": spec["broker"],
        "title": spec["title"],
        "date": spec["date"],
        "authors": spec["authors"],
        "rating": spec["rating"],
        "classification": spec["classification"],
        "info_code": spec["info_code"],
        "source_url": spec["url"],
        "final_url": final_url,
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
    }
    manifest.append(record)
    print("VALIDATED", json.dumps(record, ensure_ascii=False))

# Visual contact sheet outside the user ZIP.
thumbs = []
for render_path in render_paths:
    img = Image.open(render_path).convert("RGB")
    img.thumbnail((420, 560))
    canvas = Image.new("RGB", (440, 610), "white")
    canvas.paste(img, ((440 - img.width) // 2, 8))
    ImageDraw.Draw(canvas).text((10, 580), render_path.stem[:62], fill="black")
    thumbs.append(canvas)
    img.close()
cols = 2
rows = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 440, rows * 610), "white")
for idx, canvas in enumerate(thumbs):
    sheet.paste(canvas, ((idx % cols) * 440, (idx // cols) * 610))
    canvas.close()
contact_sheet = VERIFY / "contact_sheet.jpg"
sheet.save(contact_sheet, "JPEG", quality=90)
sheet.close()

readme = """跨境通（002640.SZ）券商研究报告资料包

收录标准：
1. 文件必须为可公开直接取得的完整券商原始PDF，而不是网页摘要、截图拼接或付费预览页。
2. 已核对公司名称/证券代码、券商名称、报告标题和实际页数。
3. 公开数据库中，跨境通近年的券商覆盖较少，完整报告主要集中在2017-2018年。本包选择公开完整文件中篇幅最大的3份。

文件性质说明：
- 新时代证券报告共19页，属于公司深度研究报告。
- 西南证券和东吴证券报告各8页，属于较完整的公司研究/半年报跟踪报告；它们是除19页深度报告外，公开可直接下载文件中篇幅最长的两份。
- 三份报告均为2018年材料，观点和盈利预测具有历史时点属性，不代表当前投资判断。

完整性检查：
- PDF文件头、加密状态和实际页数均已核验。
- 每份报告首页和末页均已渲染检查。
- ZIP已执行CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始报告版权及免责声明。
"""
(OUT / "00_资料说明.txt").write_text(readme, encoding="utf-8")
(OUT / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = OUT / "kuajingtong_brokerage_research_3reports.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
    archive.write(OUT / "00_文件清单及校验值.json", "00_文件清单及校验值.json")
    for spec in REPORTS:
        path = OUT / spec["filename"]
        archive.write(path, path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f"ZIP CRC validation failed at {bad}")

summary = {
    "zip": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "pdf_count": len(REPORTS),
    "total_pages": sum(item["pages"] for item in manifest),
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))
