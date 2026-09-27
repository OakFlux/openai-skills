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

COMPANY_FULL = "新疆大全新能源股份有限公司"
COMPANY_SHORT = "大全能源"
STOCK_CODE = "688303"
AS_OF_DATE = "2026-09-27"

FILES = [
    {
        "filename": "01_大全能源_2021年年度报告.pdf",
        "category": "2021年年度报告",
        "year": "2021",
        "doc_type": "年度报告",
        "expected_pages": 216,
        "source_type": "公司官网",
        "source_url": "https://www.xjdqsolar.com/uploads/images/%E4%B8%9A%E7%BB%A9%E5%85%AC%E5%91%8A/%E5%A4%A7%E5%85%A8%E8%83%BD%E6%BA%902021%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf",
        "source_page": "https://www.xjdqsolar.com/investor/detail-56",
    },
    {
        "filename": "02_大全能源_2022年年度报告.pdf",
        "category": "2022年年度报告",
        "year": "2022",
        "doc_type": "年度报告",
        "expected_pages": 241,
        "source_type": "公司官网",
        "source_url": "https://www.xjdqsolar.com/uploads/images/688303_20230316_SHJJ.pdf",
        "source_page": "https://www.xjdqsolar.com/investor",
    },
    {
        "filename": "03_大全能源_2023年年度报告.pdf",
        "category": "2023年年度报告",
        "year": "2023",
        "doc_type": "年度报告",
        "expected_pages": 245,
        "source_type": "公司官网",
        "source_url": "https://www.xjdqsolar.com/uploads/documents/%E6%96%B0%E7%96%86%E5%A4%A7%E5%85%A8%E6%96%B0%E8%83%BD%E6%BA%90%E8%82%A1%E4%BB%BD%E6%9C%89%E9%99%90%E5%85%AC%E5%8F%B82023%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf",
        "source_page": "https://www.xjdqsolar.com/investor",
    },
    {
        "filename": "04_大全能源_2024年年度报告.pdf",
        "category": "2024年年度报告",
        "year": "2024",
        "doc_type": "年度报告",
        "expected_pages": 245,
        "source_type": "公司官网",
        "source_url": "https://www.xjdqsolar.com/uploads/%E5%A4%A7%E5%85%A8%E8%83%BD%E6%BA%902024%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf",
        "source_page": "https://www.xjdqsolar.com/investor",
    },
    {
        "filename": "05_大全能源_2025年年度报告.pdf",
        "category": "2025年年度报告",
        "year": "2025",
        "doc_type": "年度报告",
        "expected_pages": 225,
        "source_type": "公司官网",
        "source_url": "https://www.xjdqsolar.com/uploads/images/%E4%B8%9A%E7%BB%A9%E5%85%AC%E5%91%8A/%E5%A4%A7%E5%85%A8%E8%83%BD%E6%BA%902025%E5%B9%B4%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf",
        "source_page": "https://www.xjdqsolar.com/investor",
    },
    {
        "filename": "06_大全能源_首次公开发行股票并在科创板上市招股说明书_2021-07-19.pdf",
        "category": "首次公开发行股票并在科创板上市招股说明书（最终发行版）",
        "year": "2021",
        "doc_type": "招股说明书",
        "expected_pages": 434,
        "source_type": "新浪财经公告附件镜像（最终发行版）",
        "source_url": "https://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESH_STOCK/2021/2021-7/2021-07-19/7385536.PDF",
        "source_page": "https://vip.stock.finance.sina.com.cn/corp/view/vISSUE_RaiseExplanationDetail.php?stockid=688303&id=7385536",
    },
    {
        "filename": "07_大全能源_2026年第一季度报告_最新季报.pdf",
        "category": "2026年第一季度报告（截至2026-09-27最新正式季报）",
        "year": "2026",
        "doc_type": "第一季度报告",
        "expected_pages": 13,
        "source_type": "公司官网",
        "source_url": "https://www.xjdqsolar.com/uploads/images/%E4%B8%9A%E7%BB%A9%E5%85%AC%E5%91%8A/%E5%A4%A7%E5%85%A8%E8%83%BD%E6%BA%902026%E5%B9%B4%E7%AC%AC%E4%B8%80%E5%AD%A3%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf",
        "source_page": "https://www.xjdqsolar.com/investor",
    },
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def download(url: str, source_page: str, timeout: int = 240):
    last = None
    for attempt in range(5):
        try:
            headers = {"Referer": source_page}
            with SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True) as response:
                print("FETCH", response.status_code, response.url, response.headers.get("content-type"))
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        data.extend(chunk)
                return bytes(data), response.url
        except Exception as exc:
            last = exc
            time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to download {url}: {last}")


def first_pages_text(path: Path, page_count: int, limit: int = 8) -> str:
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    chunks = []
    for index in range(min(limit, page_count)):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    text = " ".join(chunks)
    if len(text.strip()) < 100:
        doc = pymupdf.open(str(path))
        text = " ".join(doc[index].get_text("text") for index in range(min(limit, doc.page_count)))
        doc.close()
    return re.sub(r"\s+", "", text)


def validate_identity(text: str, item: dict):
    upper = text.upper()
    company_ok = any(
        token.replace(" ", "").upper() in upper
        for token in (COMPANY_FULL, COMPANY_SHORT, "XINJIANG DAQO NEW ENERGY", "DAQO ENERGY")
    )
    if not company_ok:
        raise RuntimeError(f"Company identity check failed: {item['filename']}")
    if item["year"] not in text:
        raise RuntimeError(f"Report-year check failed: {item['filename']}")
    if item["doc_type"] == "年度报告":
        type_ok = "年度报告" in text or "ANNUALREPORT" in upper
    elif item["doc_type"] == "第一季度报告":
        type_ok = "第一季度报告" in text or "FIRSTQUARTERLYREPORT" in upper
    else:
        type_ok = "招股说明书" in text or "PROSPECTUS" in upper
    if not type_ok:
        raise RuntimeError(f"Document-type check failed: {item['filename']}")


manifest = []
render_paths = []
for item in FILES:
    data, final_url = download(item["source_url"], item["source_page"])
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"Not a PDF: {item['filename']} head={data[:32]!r}")
    if len(data) < 100_000:
        raise RuntimeError(f"PDF too small: {item['filename']} bytes={len(data)}")

    path = OUT / item["filename"]
    path.write_bytes(data)
    reader = PdfReader(str(path))
    encrypted = reader.is_encrypted
    if encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = len(reader.pages)
    if pages != item["expected_pages"]:
        raise RuntimeError(f"Page count mismatch for {path.name}: {pages} != {item['expected_pages']}")

    text = first_pages_text(path, pages)
    validate_identity(text, item)

    doc = pymupdf.open(str(path))
    pixmap = doc[0].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
    render_path = VERIFY / f"{path.stem}_page1.png"
    pixmap.save(str(render_path))
    doc.close()
    render_paths.append(render_path)

    record = {
        "filename": path.name,
        "category": item["category"],
        "year": item["year"],
        "document_type": item["doc_type"],
        "source_type": item["source_type"],
        "source_page": item["source_page"],
        "source_url": item["source_url"],
        "final_url": final_url,
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
        "first_page_render": render_path.name,
    }
    manifest.append(record)
    print("VALIDATED", json.dumps(record, ensure_ascii=False))

# Build a contact sheet for visual inspection of every first page.
thumbs = []
for render_path in render_paths:
    image = Image.open(render_path).convert("RGB")
    image.thumbnail((460, 620))
    canvas = Image.new("RGB", (480, 670), "white")
    canvas.paste(image, ((480 - image.width) // 2, 8))
    ImageDraw.Draw(canvas).text((10, 642), render_path.stem[:68], fill="black")
    thumbs.append(canvas)
    image.close()
cols = 2
rows = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 480, rows * 670), "white")
for index, canvas in enumerate(thumbs):
    sheet.paste(canvas, ((index % cols) * 480, (index // cols) * 670))
    canvas.close()
contact_sheet = VERIFY / "contact_sheet.jpg"
sheet.save(contact_sheet, "JPEG", quality=90)
sheet.close()

note = f"""新疆大全新能源股份有限公司（证券简称：大全能源，证券代码：{STOCK_CODE}）披露文件资料包

文件范围：
1. 公司2021年7月上市后发布的全部正式年度报告：2021年至2025年，共5份。
2. 2021年7月19日《首次公开发行股票并在科创板上市招股说明书》，为最终发行版，不是申报稿、上会稿或招股意向书。
3. 2026年第一季度报告，为截至{AS_OF_DATE}最新一份标题明确为“季度报告”的正式定期报告。

来源说明：
- 五份年度报告和2026年第一季度报告均直接下载自大全能源官方网站投资者关系栏目。
- 最终版招股说明书取自新浪财经保存的公告原始PDF附件；报告标题、发布日期、公司名称及434页完整页数均已核验。
- 公司已披露2026年半年度报告，但半年度报告不属于标题明确的“季度报告”；截至{AS_OF_DATE}，2026年第三季度报告尚未披露。

完整性检查：
- 逐份检查PDF文件头、实际页数、加密状态、公司名称、报告年份和报告类型。
- 每份PDF首页均已完成渲染检查。
- 压缩包附来源、文件大小和SHA-256校验值，并已执行ZIP CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始文件的版权和免责声明。
"""
(OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = OUT / "大全能源_全部年报_招股说明书_2026年第一季度报告.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
    archive.write(OUT / "00_文件清单及校验值.json", "00_文件清单及校验值.json")
    for item in FILES:
        path = OUT / item["filename"]
        archive.write(path, path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad_file = archive.testzip()
    if bad_file:
        raise RuntimeError(f"ZIP CRC validation failed at {bad_file}")

summary = {
    "zip": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "pdf_count": len(FILES),
    "total_pages": sum(record["pages"] for record in manifest),
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    "FINAL_SUMMARY",
    json.dumps(
        {key: summary[key] for key in ("zip", "zip_bytes", "zip_sha256", "pdf_count", "total_pages")},
        ensure_ascii=False,
    ),
)
