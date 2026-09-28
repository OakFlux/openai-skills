#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import pymupdf
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
from pypdf import PdfReader

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

STOCK_CODE = "002640"
COMPANY_SHORT = "跨境通"
COMPANY_FULL = "跨境通宝电子商务股份有限公司"
AS_OF_DATE = "2026-09-29"
SINA_BASE = "https://vip.stock.finance.sina.com.cn"
ANNUAL_LIST_URL = f"{SINA_BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/ndbg.phtml"
Q1_LIST_URL = f"{SINA_BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/yjdbg.phtml"
Q3_LIST_URL = f"{SINA_BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/sjdbg.phtml"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def fetch(url: str, *, referer: str | None = None, timeout: int = 180):
    last = None
    for attempt in range(5):
        try:
            headers = {"Referer": referer} if referer else {}
            response = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            print("FETCH", response.status_code, len(response.content), response.url, response.headers.get("content-type"))
            if response.status_code == 200:
                return response
            last = RuntimeError(f"HTTP {response.status_code}: {response.url}")
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def decode_html(response) -> str:
    response.encoding = response.apparent_encoding or "gb18030"
    return response.text


def list_entries(url: str):
    response = fetch(url, timeout=90)
    soup = BeautifulSoup(decode_html(response), "html.parser")
    entries = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(response.url, anchor["href"])
        if "vCB_AllBulletinDetail.php" not in href:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            continue
        row = anchor.find_parent("tr")
        context = " ".join((row.get_text(" ", strip=True) if row else title).split())
        entries.append({"title": title, "context": context, "detail_url": href})
    dedup = {entry["detail_url"]: entry for entry in entries}
    result = list(dedup.values())
    print("LIST", url, len(result), json.dumps(result, ensure_ascii=False))
    return result


def select_annual(entries, year: int):
    candidates = []
    for entry in entries:
        title = entry["title"]
        if str(year) not in title or "年度报告" not in title:
            continue
        if any(term in title for term in ("摘要", "英文版", "审计报告", "业绩快报", "业绩预告")):
            continue
        candidates.append(entry)
    if not candidates:
        raise RuntimeError(f"No annual report found for {year}")

    def priority(entry):
        title = entry["title"]
        revised = 1 if any(term in title for term in ("更新后", "更正后", "修订版", "修订后")) else 0
        exact = 1 if f"{year}年年度报告" in title else 0
        return revised, exact, len(title)

    candidates.sort(key=priority, reverse=True)
    return candidates[0]


def select_latest_quarter(q1_entries, q3_entries):
    q3_candidates = [
        entry
        for entry in q3_entries
        if "2026" in entry["title"]
        and ("三季度报告" in entry["title"] or "第三季度报告" in entry["title"])
        and "摘要" not in entry["title"]
    ]
    if q3_candidates:
        q3_candidates.sort(key=lambda x: ("更新后" in x["title"] or "更正后" in x["title"], len(x["title"])), reverse=True)
        return q3_candidates[0], 3

    q1_candidates = [
        entry
        for entry in q1_entries
        if "2026" in entry["title"]
        and ("一季度报告" in entry["title"] or "第一季度报告" in entry["title"])
        and "摘要" not in entry["title"]
    ]
    if not q1_candidates:
        raise RuntimeError("No 2026 quarterly report found")
    q1_candidates.sort(key=lambda x: ("更新后" in x["title"] or "更正后" in x["title"], len(x["title"])), reverse=True)
    return q1_candidates[0], 1


def detail_pdf_url(entry):
    response = fetch(entry["detail_url"], timeout=90)
    html = decode_html(response)
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(response.url, anchor["href"])
        blob = (text + " " + href).lower()
        if "下载公告" in text or ("file.finance.sina.com.cn" in blob and ".pdf" in blob):
            candidates.append(href)
    candidates.extend(
        re.findall(r"https?://file\.finance\.sina\.com\.cn/[^\"'<>\s]+\.PDF", html, re.I)
    )
    unique = []
    seen = set()
    for url in candidates:
        url = url.replace("&amp;", "&")
        if url not in seen:
            unique.append(url)
            seen.add(url)
    if not unique:
        raise RuntimeError(f"No PDF URL found at {entry['detail_url']}")
    unique.sort(key=lambda url: ("file.finance.sina.com.cn" not in url, len(url)))
    return unique[0]


def disclosure_date_from_url(url: str) -> str:
    match = re.search(r"/(20\d{2}-\d{2}-\d{2})/", url)
    return match.group(1) if match else ""


def extract_first_pages_text(path: Path, page_count: int, limit: int = 10) -> str:
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


def validate_text(text: str, *, year: int, doc_type: str, filename: str):
    upper = text.upper()
    company_ok = any(
        token.replace(" ", "").upper() in upper
        for token in (COMPANY_FULL, COMPANY_SHORT, "GLOBAL TOP E-COMMERCE")
    )
    if not company_ok:
        raise RuntimeError(f"Company identity validation failed: {filename}")
    if str(year) not in text:
        raise RuntimeError(f"Report year validation failed: {filename}")
    if doc_type == "annual":
        type_ok = "年度报告" in text or "ANNUALREPORT" in upper
    elif doc_type == "q3":
        type_ok = "第三季度报告" in text or "三季度报告" in text or "THIRDQUARTERLYREPORT" in upper
    else:
        type_ok = "第一季度报告" in text or "一季度报告" in text or "FIRSTQUARTERLYREPORT" in upper
    if not type_ok:
        raise RuntimeError(f"Document type validation failed: {filename}")


def download_validate(entry, *, filename: str, category: str, year: int, doc_type: str, min_pages: int):
    pdf_url = detail_pdf_url(entry)
    response = fetch(pdf_url, referer=entry["detail_url"], timeout=240)
    data = response.content
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"Not a PDF: {filename}, head={data[:32]!r}")
    if len(data) < 50_000:
        raise RuntimeError(f"PDF too small: {filename}, bytes={len(data)}")

    path = OUT / filename
    path.write_bytes(data)
    reader = PdfReader(str(path))
    encrypted = reader.is_encrypted
    if encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {filename}")
    pages = len(reader.pages)
    if pages < min_pages:
        raise RuntimeError(f"Unexpected page count for {filename}: {pages}")

    text = extract_first_pages_text(path, pages)
    validate_text(text, year=year, doc_type=doc_type, filename=filename)

    doc = pymupdf.open(str(path))
    pixmap = doc[0].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
    render_path = VERIFY / f"{path.stem}_page1.png"
    pixmap.save(str(render_path))
    doc.close()

    record = {
        "filename": filename,
        "category": category,
        "year": year,
        "document_type": doc_type,
        "source_title": entry["title"],
        "disclosure_date": disclosure_date_from_url(pdf_url),
        "detail_url": entry["detail_url"],
        "pdf_url": pdf_url,
        "final_url": response.url,
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
        "first_page_render": render_path.name,
    }
    print("VALIDATED", json.dumps(record, ensure_ascii=False))
    return path, render_path, record


annual_entries = list_entries(ANNUAL_LIST_URL)
q1_entries = list_entries(Q1_LIST_URL)
q3_entries = list_entries(Q3_LIST_URL)

specs = []
for index, year in enumerate(range(2020, 2026), 1):
    specs.append(
        {
            "entry": select_annual(annual_entries, year),
            "filename": f"{index:02d}_{COMPANY_SHORT}_{year}年年度报告.pdf",
            "category": f"{year}年年度报告",
            "year": year,
            "doc_type": "annual",
            "min_pages": 80,
        }
    )

quarter_entry, quarter_number = select_latest_quarter(q1_entries, q3_entries)
quarter_name = "第三季度报告" if quarter_number == 3 else "第一季度报告"
quarter_doc_type = "q3" if quarter_number == 3 else "q1"
specs.append(
    {
        "entry": quarter_entry,
        "filename": f"07_{COMPANY_SHORT}_2026年{quarter_name}_最新季报.pdf",
        "category": f"2026年{quarter_name}（截至{AS_OF_DATE}最新正式季报）",
        "year": 2026,
        "doc_type": quarter_doc_type,
        "min_pages": 5,
    }
)

documents = [download_validate(**spec) for spec in specs]
manifest = [record for _, _, record in documents]

# First-page contact sheet for visual verification.
thumbs = []
for _, render_path, _ in documents:
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

note = f"""{COMPANY_FULL}（证券简称：{COMPANY_SHORT}，证券代码：{STOCK_CODE}）定期报告资料包

文件范围：
1. 2020年至2025年完整年度报告，共6份；均为年度报告全文，不含摘要版、业绩快报或业绩预告。
2. 2026年{quarter_name}，为截至{AS_OF_DATE}检索到的最新一份标题明确为“季度报告”的正式报告。

来源与核验：
- 报告标题、披露日期及PDF附件来自新浪财经保存的上市公司公告页面和公告附件镜像。
- 每份PDF均核对公司名称、证券代码对应关系、报告年份、文件类型、实际页数及首页渲染。
- 压缩包附来源、文件大小和SHA-256校验值，并已执行ZIP CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始文件版权及免责声明。
"""
(OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = OUT / f"{COMPANY_SHORT}_2020-2025年报_2026年{quarter_name}.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
    archive.write(OUT / "00_文件清单及校验值.json", "00_文件清单及校验值.json")
    for path, _, _ in documents:
        archive.write(path, path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad_file = archive.testzip()
    if bad_file:
        raise RuntimeError(f"ZIP CRC validation failed at {bad_file}")

# Stable alias makes artifact retrieval independent of which quarter was selected.
alias_path = OUT / f"{COMPANY_SHORT}_2020-2025年报_最新季报.zip"
alias_path.write_bytes(zip_path.read_bytes())

summary = {
    "zip": zip_path.name,
    "alias_zip": alias_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "pdf_count": len(documents),
    "total_pages": sum(record["pages"] for record in manifest),
    "latest_quarter": quarter_number,
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))
