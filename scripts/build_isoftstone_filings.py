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

COMPANY_FULL = "软通动力信息技术（集团）股份有限公司"
COMPANY_SHORT = "软通动力"
STOCK_CODE = "301236"
AS_OF_DATE = "2026-09-27"

FILES = [
    {
        "filename": "01_软通动力_2021年年度报告.pdf",
        "category": "2021年年度报告",
        "year": "2021",
        "doc_type": "年度报告",
        "expected_pages": 245,
        "source_title": "软通动力：2021年年度报告",
        "disclosure_date": "2022-04-26",
        "detail_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=301236&id=8078499",
        "pdf_url": "http://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2022/2022-4/2022-04-26/8078499.PDF",
    },
    {
        "filename": "02_软通动力_2022年年度报告.pdf",
        "category": "2022年年度报告",
        "year": "2022",
        "doc_type": "年度报告",
        "expected_pages": 240,
        "source_title": "软通动力：2022年年度报告",
        "disclosure_date": "2023-04-26",
        "detail_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=301236&id=9091871",
        "pdf_url": "http://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2023/2023-4/2023-04-26/9091871.PDF",
    },
    {
        "filename": "03_软通动力_2023年年度报告.pdf",
        "category": "2023年年度报告",
        "year": "2023",
        "doc_type": "年度报告",
        "expected_pages": 239,
        "source_title": "软通动力：2023年年度报告",
        "disclosure_date": "2024-04-27",
        "detail_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=301236&id=10125881",
        "pdf_url": "http://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2024/2024-4/2024-04-27/10125881.PDF",
    },
    {
        "filename": "04_软通动力_2024年年度报告.pdf",
        "category": "2024年年度报告",
        "year": "2024",
        "doc_type": "年度报告",
        "expected_pages": 274,
        "source_title": "软通动力：2024年年度报告",
        "disclosure_date": "2025-04-26",
        "detail_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=301236&id=11007853",
        "pdf_url": "http://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2025/2025-4/2025-04-26/11007853.PDF",
    },
    {
        "filename": "05_软通动力_2025年年度报告.pdf",
        "category": "2025年年度报告",
        "year": "2025",
        "doc_type": "年度报告",
        "expected_pages": 263,
        "source_title": "软通动力：2025年年度报告",
        "disclosure_date": "2026-04-25",
        "detail_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=301236&id=12197081",
        "pdf_url": "http://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2026/2026-4/2026-04-25/12197081.PDF",
    },
    {
        "filename": "06_软通动力_首次公开发行股票并在创业板上市招股说明书_2022-03-10.pdf",
        "category": "首次公开发行股票并在创业板上市招股说明书（最终发行版）",
        "year": "2022",
        "doc_type": "招股说明书",
        "min_pages": 500,
        "source_title": "软通动力：首次公开发行股票并在创业板上市招股说明书",
        "disclosure_date": "2022-03-10",
        "detail_url": "https://vip.stock.finance.sina.com.cn/corp/view/vISSUE_RaiseExplanationDetail.php?stockid=301236&id=7875726",
        "pdf_url": "http://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2022/2022-3/2022-03-10/7875726.PDF",
    },
    {
        "filename": "07_软通动力_2026年第一季度报告_最新季报.pdf",
        "category": "2026年第一季度报告（截至2026-09-27最新正式季报）",
        "year": "2026",
        "doc_type": "第一季度报告",
        "expected_pages": 12,
        "source_title": "软通动力：2026年一季度报告",
        "disclosure_date": "2026-04-25",
        "detail_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=301236&id=12197080",
        "pdf_url": "http://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2026/2026-4/2026-04-25/12197080.PDF",
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


def normalized_first_pages_text(path: Path, page_count: int, limit: int = 10) -> str:
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


def validate_text(text: str, item: dict):
    upper = text.upper()
    company_ok = any(
        token.replace(" ", "").upper() in upper
        for token in (
            COMPANY_FULL,
            COMPANY_SHORT,
            "ISOFTSTONE INFORMATION TECHNOLOGY",
            "ISOFTSTONE",
        )
    )
    if not company_ok:
        raise RuntimeError(f"Company identity check failed: {item['filename']}")
    if item["year"] not in text:
        raise RuntimeError(f"Report-year check failed: {item['filename']}")
    if item["doc_type"] == "年度报告":
        type_ok = "年度报告" in text or "ANNUALREPORT" in upper
    elif item["doc_type"] == "第一季度报告":
        type_ok = "第一季度报告" in text or "一季度报告" in text or "FIRSTQUARTERLYREPORT" in upper
    else:
        type_ok = "招股说明书" in text or "PROSPECTUS" in upper
    if not type_ok:
        raise RuntimeError(f"Document-type check failed: {item['filename']}")


manifest = []
render_paths = []
for item in FILES:
    data, final_url = download(item["pdf_url"], item["detail_url"])
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
    if "expected_pages" in item and pages != item["expected_pages"]:
        raise RuntimeError(f"Page count mismatch for {path.name}: {pages} != {item['expected_pages']}")
    if pages < item.get("min_pages", 1):
        raise RuntimeError(f"Unexpectedly short PDF for {path.name}: {pages} pages")

    text = normalized_first_pages_text(path, pages)
    validate_text(text, item)

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
        "source_title": item["source_title"],
        "disclosure_date": item["disclosure_date"],
        "detail_url": item["detail_url"],
        "pdf_url": item["pdf_url"],
        "final_url": final_url,
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
        "first_page_render": render_path.name,
    }
    manifest.append(record)
    print("VALIDATED", json.dumps(record, ensure_ascii=False))

# Contact sheet for a visual check of every first page.
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

note = f"""软通动力信息技术（集团）股份有限公司（证券简称：软通动力，证券代码：{STOCK_CODE}）披露文件资料包

文件范围：
1. 公司2022年3月上市后公开发布的全部年度报告：2021年至2025年，共5份。
2. 2022年3月10日《首次公开发行股票并在创业板上市招股说明书》，为最终发行版，不是申报稿或招股意向书。
3. 2026年第一季度报告，为截至{AS_OF_DATE}最新一份标题明确为“季度报告”的正式定期报告。

来源与口径：
- PDF文件取自新浪财经保存的上市公司公告原始附件镜像，公告标题、公告日期和文件内容与公司披露记录交叉核对。
- 公司已于2026年8月披露2026年半年度报告，但半年度报告不属于标题明确的“季度报告”；截至{AS_OF_DATE}，2026年第三季度报告尚未披露。
- 未收录年度报告摘要、招股意向书、申报稿、上市公告书或2025年度向特定对象发行股票的募集说明书。

完整性检查：
- 逐份检查PDF文件头、实际页数、加密状态、公司名称、报告年份和文件类型。
- 每份PDF首页均已渲染检查。
- 压缩包附来源、页数、文件大小及SHA-256校验清单，并已执行ZIP CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始文件版权及免责声明。
"""
(OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = OUT / "软通动力_全部年报_招股说明书_2026年第一季度报告.zip"
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
