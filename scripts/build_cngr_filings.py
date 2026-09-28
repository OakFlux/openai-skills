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

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

STOCK_CODE = "300919"
COMPANY_CURRENT = "中伟新材"
COMPANY_OLD = "中伟股份"
COMPANY_FULL = "中伟新材料股份有限公司"
AS_OF_DATE = "2026-09-28"

BASE = "https://vip.stock.finance.sina.com.cn"
ANNUAL_LIST = f"{BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/ndbg.phtml"
Q1_LIST = f"{BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/yjdbg.phtml"
Q3_LIST = f"{BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/sjdbg.phtml"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})


def get(url: str, *, referer: str | None = None, timeout: int = 180):
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
        time.sleep(min(12, 2 ** attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def decode_html(response) -> str:
    response.encoding = response.apparent_encoding or "gb18030"
    return response.text


def list_entries(url: str):
    response = get(url, timeout=90)
    soup = BeautifulSoup(decode_html(response), "html.parser")
    entries = []
    for a in soup.find_all("a", href=True):
        href = urljoin(response.url, a["href"])
        if "vCB_AllBulletinDetail.php" not in href:
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if not title:
            continue
        entries.append({"title": title, "detail_url": href})
    dedup = {entry["detail_url"]: entry for entry in entries}
    result = list(dedup.values())
    print("LIST", url, len(result), json.dumps(result, ensure_ascii=False))
    return result


def find_entry(entries, year: int, doc_kind: str):
    candidates = []
    for entry in entries:
        title = entry["title"]
        if str(year) not in title:
            continue
        if doc_kind == "annual":
            if "年度报告" not in title and "年报" not in title:
                continue
            if any(x in title for x in ("摘要", "审计报告", "英文版", "业绩")):
                continue
        elif doc_kind == "q1":
            if "第一季度报告" not in title and "一季度报告" not in title:
                continue
            if any(x in title for x in ("摘要", "业绩预告", "业绩快报")):
                continue
        elif doc_kind == "q3":
            if "第三季度报告" not in title and "三季度报告" not in title:
                continue
            if any(x in title for x in ("摘要", "业绩预告", "业绩快报")):
                continue
        candidates.append(entry)
    if not candidates:
        return None
    candidates.sort(key=lambda x: ("更新" in x["title"] or "更正" in x["title"], len(x["title"])), reverse=True)
    return candidates[0]


def extract_pdf(entry):
    response = get(entry["detail_url"], timeout=90)
    html = decode_html(response)
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(response.url, a["href"])
        blob = (text + " " + href).lower()
        if "下载公告" in text or ".pdf" in blob:
            candidates.append(href)
    candidates.extend(re.findall(r"https?://file\.finance\.sina\.com\.cn/[^\"'<>\s]+\.PDF", html, re.I))
    seen = []
    for url in candidates:
        url = url.replace("&amp;", "&")
        if url not in seen:
            seen.append(url)
    for url in seen:
        try:
            pdf_response = get(url, referer=entry["detail_url"], timeout=240)
            data = pdf_response.content
            if data.startswith(b"%PDF-") and len(data) > 50_000:
                return data, url, pdf_response.url
        except Exception as exc:
            print("PDF_CANDIDATE_FAILED", url, repr(exc))
    raise RuntimeError(f"No valid PDF found for {entry['title']} at {entry['detail_url']}")


def text_from_first_pages(path: Path, limit: int = 8):
    doc = fitz.open(str(path))
    text = " ".join(doc[index].get_text("text") for index in range(min(limit, doc.page_count)))
    doc.close()
    return re.sub(r"\s+", "", text)


def validate(path: Path, year: int, doc_kind: str, min_pages: int):
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = len(reader.pages)
    if pages < min_pages:
        raise RuntimeError(f"Unexpected page count for {path.name}: {pages}")
    text = text_from_first_pages(path)
    upper = text.upper()
    company_terms = (
        COMPANY_FULL,
        COMPANY_CURRENT,
        COMPANY_OLD,
        "CNGRADVANCEDMATERIAL",
        "CNGR",
    )
    if not any(term.replace(" ", "").upper() in upper for term in company_terms):
        raise RuntimeError(f"Company identity check failed: {path.name}")
    if str(year) not in text:
        raise RuntimeError(f"Report year check failed: {path.name}")
    if doc_kind == "annual":
        type_ok = "年度报告" in text or "ANNUALREPORT" in upper
    elif doc_kind == "q1":
        type_ok = "第一季度报告" in text or "一季度报告" in text or "FIRSTQUARTERLYREPORT" in upper
    else:
        type_ok = "第三季度报告" in text or "三季度报告" in text or "THIRDQUARTERLYREPORT" in upper
    if not type_ok:
        raise RuntimeError(f"Document type check failed: {path.name}")
    return pages, bool(reader.is_encrypted)


def disclosure_date_from_url(url: str):
    match = re.search(r"/(20\d{2}-\d{2}-\d{2})/", url)
    return match.group(1) if match else ""


annual_entries = list_entries(ANNUAL_LIST)
q1_entries = list_entries(Q1_LIST)
q3_entries = list_entries(Q3_LIST)

specs = []
for index, year in enumerate(range(2020, 2026), 1):
    entry = find_entry(annual_entries, year, "annual")
    if not entry:
        raise RuntimeError(f"Missing annual report for {year}")
    specs.append({
        "filename": f"{index:02d}_{COMPANY_CURRENT}_{year}年年度报告.pdf",
        "category": f"{year}年年度报告",
        "year": year,
        "doc_kind": "annual",
        "min_pages": 80,
        "entry": entry,
    })

q3_entry = find_entry(q3_entries, 2026, "q3")
if q3_entry:
    latest = {
        "filename": f"07_{COMPANY_CURRENT}_2026年第三季度报告_最新季报.pdf",
        "category": f"2026年第三季度报告（截至{AS_OF_DATE}最新正式季报）",
        "year": 2026,
        "doc_kind": "q3",
        "min_pages": 6,
        "entry": q3_entry,
    }
    latest_quarter = 3
else:
    q1_entry = find_entry(q1_entries, 2026, "q1")
    if not q1_entry:
        raise RuntimeError("Missing 2026 first-quarter report")
    latest = {
        "filename": f"07_{COMPANY_CURRENT}_2026年第一季度报告_最新季报.pdf",
        "category": f"2026年第一季度报告（截至{AS_OF_DATE}最新正式季报）",
        "year": 2026,
        "doc_kind": "q1",
        "min_pages": 6,
        "entry": q1_entry,
    }
    latest_quarter = 1
specs.append(latest)

manifest = []
render_paths = []
for spec in specs:
    data, source_url, final_url = extract_pdf(spec["entry"])
    path = OUT / spec["filename"]
    path.write_bytes(data)
    pages, encrypted = validate(path, spec["year"], spec["doc_kind"], spec["min_pages"])

    doc = fitz.open(str(path))
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
    render_path = VERIFY / f"{path.stem}_page1.png"
    pix.save(str(render_path))
    doc.close()
    render_paths.append(render_path)

    record = {
        "filename": path.name,
        "category": spec["category"],
        "year": spec["year"],
        "document_type": spec["doc_kind"],
        "source_title": spec["entry"]["title"],
        "disclosure_date": disclosure_date_from_url(source_url),
        "detail_url": spec["entry"]["detail_url"],
        "pdf_url": source_url,
        "final_url": final_url,
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
        "first_page_render": render_path.name,
    }
    manifest.append(record)
    print("VALIDATED", json.dumps(record, ensure_ascii=False))

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

latest_label = "2026年第三季度报告" if latest_quarter == 3 else "2026年第一季度报告"
note = f"""中伟新材料股份有限公司（证券简称：中伟新材，曾用简称：中伟股份；证券代码：{STOCK_CODE}）定期报告资料包

文件范围：
1. 2020年至2025年完整年度报告，共6份；不含年度报告摘要、业绩快报或英文重复版本。
2. {latest_label}，为截至{AS_OF_DATE}标题明确为“季度报告”的最新正式季报。

来源及口径：
- 报告标题和披露日期来自上市公司公告索引，PDF取自新浪财经保存的公告附件镜像。
- 2020年至2024年的部分报告使用公司原证券简称“中伟股份”，属于同一证券代码300919及同一上市主体。
- 公司已披露2026年半年度报告，但半年报不属于标题明确的季度报告，因此本包按“最新季报”口径收录{latest_label}。

完整性检查：
- 已逐份检查PDF文件头、实际页数、加密状态、公司名称、报告年份和报告类型。
- 每份PDF首页均完成渲染检查。
- ZIP已执行CRC完整性测试；另附文件来源、大小及SHA-256校验值。
- 文件仅供个人研究与学习使用，请遵守原始文件的版权及免责声明。
"""
(OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "00_文件清单及校验值.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

zip_name = f"中伟新材_2020-2025年报_{latest_label}.zip"
zip_path = OUT / zip_name
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
    archive.write(OUT / "00_文件清单及校验值.json", "00_文件清单及校验值.json")
    for spec in specs:
        pdf_path = OUT / spec["filename"]
        archive.write(pdf_path, pdf_path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f"ZIP CRC validation failed at {bad}")

summary = {
    "zip": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "pdf_count": len(specs),
    "total_pages": sum(item["pages"] for item in manifest),
    "latest_quarter": latest_quarter,
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))
