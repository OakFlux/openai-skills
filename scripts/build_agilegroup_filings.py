#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import pymupdf
import requests
import urllib3
from PIL import Image, ImageDraw

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

COMPANY_CN_TRAD = "雅居樂集團控股有限公司"
COMPANY_CN_SIMP = "雅居乐集团控股有限公司"
COMPANY_EN = "AGILE GROUP HOLDINGS LIMITED"
STOCK_CODE = "03383.HK"
AS_OF_DATE = "2026-09-27"
REPORT_PAGE = "https://www.agile.com.cn/invest/report"

FILES = [
    {
        "filename": "01_雅居乐集团_2020年年度报告.pdf",
        "category": "2020年年度报告",
        "year": "2020",
        "doc_type": "annual",
        "min_pages": 150,
        "source_page": "https://www.agile.com.cn/invest/report?year=2021",
        "source_url": "https://www.agile.com.cn/datas/large_file/file/c-report.pdf",
    },
    {
        "filename": "02_雅居乐集团_2021年年度报告.pdf",
        "category": "2021年年度报告",
        "year": "2021",
        "doc_type": "annual",
        "min_pages": 150,
        "source_page": "https://www.agile.com.cn/invest/report?year=2022",
        "source_url": "https://www.agile.com.cn/datas/large_file/file/1CW03383-AR21.pdf",
    },
    {
        "filename": "03_雅居乐集团_2022年年度报告.pdf",
        "category": "2022年年度报告",
        "year": "2022",
        "doc_type": "annual",
        "min_pages": 150,
        "source_page": "https://www.agile.com.cn/invest/report?year=2023",
        "source_url": "https://www.agile.com.cn/datas/large_file/file/AR2022c.pdf",
    },
    {
        "filename": "04_雅居乐集团_2023年年度报告.pdf",
        "category": "2023年年度报告",
        "year": "2023",
        "doc_type": "annual",
        "min_pages": 150,
        "source_page": "https://www.agile.com.cn/invest/report?year=2024",
        "source_url": "https://www.agile.com.cn/datas/large_file/file/c_annual%20report.pdf",
    },
    {
        "filename": "05_雅居乐集团_2024年年度报告.pdf",
        "category": "2024年年度报告",
        "year": "2024",
        "doc_type": "annual",
        "min_pages": 150,
        "source_page": "https://www.agile.com.cn/invest/report?year=2025",
        "source_url": "https://www.agile.com.cn/datas/large_file/file/CW03383AR.pdf",
    },
    {
        "filename": "06_雅居乐集团_2025年年度报告.pdf",
        "category": "2025年年度报告",
        "year": "2025",
        "doc_type": "annual",
        "min_pages": 150,
        "source_page": REPORT_PAGE,
        "source_url": "https://www.agile.com.cn/datas/large_file/file/CW03383-AR25.pdf",
    },
    {
        "filename": "07_雅居乐集团_2026年中期报告_最新定期财报.pdf",
        "category": "2026年中期报告（截至2026-09-27最新正式定期财报）",
        "year": "2026",
        "doc_type": "interim",
        "min_pages": 40,
        "source_page": REPORT_PAGE,
        "source_url": "https://www.agile.com.cn/datas/large_file/file/CW03383-IR26.pdf",
    },
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def download(url: str, referer: str, timeout: int = 300):
    last = None
    for attempt in range(5):
        try:
            with SESSION.get(
                url,
                headers={"Referer": referer},
                timeout=timeout,
                allow_redirects=True,
                stream=True,
                verify=False,
            ) as response:
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


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").upper()


def extract_first_pages_text(doc: pymupdf.Document, max_pages: int = 10) -> str:
    chunks = []
    for index in range(min(max_pages, doc.page_count)):
        try:
            chunks.append(doc[index].get_text("text") or "")
        except Exception:
            pass
    return normalize(" ".join(chunks))


def validate_document(text: str, item: dict):
    company_tokens = [COMPANY_CN_TRAD, COMPANY_CN_SIMP, COMPANY_EN]
    if not any(normalize(token) in text for token in company_tokens):
        raise RuntimeError(f"Company identity validation failed: {item['filename']}")
    if item["year"] not in text:
        raise RuntimeError(f"Report-year validation failed: {item['filename']}")
    if item["doc_type"] == "annual":
        type_tokens = ["年度報告", "年度报告", "年報", "年报", "ANNUALREPORT"]
    else:
        type_tokens = ["中期報告", "中期报告", "INTERIMREPORT"]
    if not any(normalize(token) in text for token in type_tokens):
        raise RuntimeError(f"Document-type validation failed: {item['filename']}")


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
    doc = pymupdf.open(str(path))
    encrypted = bool(doc.needs_pass)
    if encrypted and not doc.authenticate(""):
        doc.close()
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = doc.page_count
    if pages < item["min_pages"]:
        doc.close()
        raise RuntimeError(f"Unexpected page count for {path.name}: {pages}")

    text = extract_first_pages_text(doc)
    validate_document(text, item)

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

# Build a first-page contact sheet for visual verification.
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

note = f"""雅居樂集團控股有限公司（股份代號：3383 / {STOCK_CODE}）定期財務報告資料包

文件範圍：
1. 2020年至2025年完整年度報告，共6份。
2. 2026年中期報告，為截至{AS_OF_DATE}公司已刊發的最新正式定期財務報告。

口徑說明：
- 香港上市公司通常披露年度報告及中期報告，而不一定刊發獨立季度報告；因此本資料包以2026年中期報告作為“最新季報／最新定期財報”收錄。
- 所有PDF均直接下載自雅居樂集團官方“財務報表”頁面，使用中文正式版本。
- 已逐份核驗PDF文件頭、實際頁數、加密狀態、公司名稱、報告年份及報告類型。
- 每份PDF首頁均完成渲染檢查；ZIP已執行CRC完整性測試。
- 文件僅供個人研究與學習使用，請遵守原始文件的版權及免責聲明。
"""
(OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = OUT / "雅居乐集团_2020-2025年报_2026年中期报告.zip"
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
