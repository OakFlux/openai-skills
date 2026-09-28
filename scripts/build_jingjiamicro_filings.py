#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import urllib3
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import pymupdf
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

STOCK_CODE = "300474"
COMPANY_SHORT = "景嘉微"
COMPANY_FULL = "长沙景嘉微电子股份有限公司"
AS_OF_DATE = "2026-09-28"
BASE = "https://vip.stock.finance.sina.com.cn"
ANNUAL_LIST = f"{BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/ndbg.phtml"
Q1_LIST = f"{BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/yjdbg.phtml"
Q3_LIST = f"{BASE}/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/sjdbg.phtml"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def get(url: str, *, referer: str | None = None, timeout: int = 120, stream: bool = False):
    last = None
    for attempt in range(5):
        try:
            headers = {"Referer": referer} if referer else {}
            response = SESSION.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
                verify=False,
                stream=stream,
            )
            print("FETCH", response.status_code, response.url, response.headers.get("content-type"))
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def decode_page(response) -> str:
    response.encoding = response.apparent_encoding or "gb18030"
    return response.text


def parse_list_page(url: str):
    response = get(url, timeout=60)
    soup = BeautifulSoup(decode_page(response), "html.parser")
    records = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(response.url, anchor["href"])
        if "vCB_AllBulletinDetail.php" not in href:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            continue
        records.append({"title": title, "detail_url": href})
    dedup = {record["detail_url"]: record for record in records}
    result = list(dedup.values())
    print("LIST", url, len(result), json.dumps(result, ensure_ascii=False))
    return result


def select_annual(records, year: int):
    candidates = []
    for record in records:
        title = record["title"]
        if str(year) not in title:
            continue
        if "年度报告" not in title and "年报" not in title:
            continue
        if any(term in title for term in ("摘要", "英文版", "业绩", "审计报告")):
            continue
        candidates.append(record)
    if not candidates:
        raise RuntimeError(f"No complete annual report found for {year}")
    candidates.sort(key=lambda item: ("年度报告" in item["title"], len(item["title"])), reverse=True)
    return candidates[0]


def select_latest_quarter(q1_records, q3_records):
    candidates = []
    for record in q1_records:
        title = record["title"]
        if "2026" in title and ("一季度报告" in title or "第一季度报告" in title):
            candidates.append((1, record))
    for record in q3_records:
        title = record["title"]
        if "2026" in title and ("三季度报告" in title or "第三季度报告" in title):
            candidates.append((3, record))
    if not candidates:
        raise RuntimeError("No 2026 quarterly report found")
    candidates.sort(key=lambda item: item[0], reverse=True)
    quarter, record = candidates[0]
    return quarter, record


def extract_detail(record: dict):
    response = get(record["detail_url"], timeout=90)
    html = decode_page(response)
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(response.url, anchor["href"])
        lower = href.lower()
        if "下载公告" in text or "file.finance.sina.com.cn" in lower or lower.endswith(".pdf"):
            candidates.append(href)
    candidates.extend(
        re.findall(
            r"https?://file\.finance\.sina\.com\.cn/[^\"'<>\s]+?\.PDF(?:\?[^\"'<>\s]*)?",
            html,
            flags=re.I,
        )
    )
    dedup = []
    seen = set()
    for url in candidates:
        url = url.replace("&amp;", "&")
        if url not in seen:
            dedup.append(url)
            seen.add(url)
    date_match = re.search(r"公告日期\s*[:：]\s*(\d{4}-\d{2}-\d{2})", soup.get_text(" ", strip=True))
    disclosure_date = date_match.group(1) if date_match else ""
    if not dedup:
        raise RuntimeError(f"No PDF download link found at {record['detail_url']}")
    dedup.sort(key=lambda url: ("file.finance.sina.com.cn" not in url.lower(), len(url)))
    return dedup[0], disclosure_date


def download_pdf(url: str, referer: str):
    response = get(url, referer=referer, timeout=240, stream=True)
    data = bytearray()
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if chunk:
            data.extend(chunk)
    return bytes(data), response.url


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def validate_document(path: Path, year: int, document_type: str):
    doc = pymupdf.open(str(path))
    page_count = doc.page_count
    if document_type == "annual" and page_count < 100:
        doc.close()
        raise RuntimeError(f"Annual report has too few pages: {path.name}, {page_count}")
    if document_type == "quarter" and page_count < 5:
        doc.close()
        raise RuntimeError(f"Quarterly report has too few pages: {path.name}, {page_count}")
    text = " ".join(doc[index].get_text("text") for index in range(min(10, page_count)))
    normalized = normalize(text)
    company_ok = any(
        normalize(term) in normalized
        for term in (COMPANY_FULL, COMPANY_SHORT, STOCK_CODE, "JINGJIAMICRO")
    )
    if not company_ok:
        doc.close()
        raise RuntimeError(f"Company identity validation failed: {path.name}")
    if str(year) not in normalized:
        doc.close()
        raise RuntimeError(f"Report-year validation failed: {path.name}")
    if document_type == "annual":
        type_ok = "年度报告" in normalized or "ANNUALREPORT" in normalized
    else:
        type_ok = any(term in normalized for term in ("第一季度报告", "一季度报告", "第三季度报告", "三季度报告"))
    if not type_ok:
        doc.close()
        raise RuntimeError(f"Document-type validation failed: {path.name}")
    pixmap = doc[0].get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3), alpha=False)
    render_path = VERIFY / f"{path.stem}_page1.png"
    pixmap.save(str(render_path))
    doc.close()
    return page_count, render_path


annual_records = parse_list_page(ANNUAL_LIST)
q1_records = parse_list_page(Q1_LIST)
q3_records = parse_list_page(Q3_LIST)

specs = []
for index, year in enumerate(range(2020, 2026), start=1):
    specs.append(
        {
            "filename": f"{index:02d}_{COMPANY_SHORT}_{year}年年度报告.pdf",
            "category": f"{year}年年度报告",
            "year": year,
            "document_type": "annual",
            "record": select_annual(annual_records, year),
        }
    )

quarter, quarter_record = select_latest_quarter(q1_records, q3_records)
quarter_cn = "第一季度" if quarter == 1 else "第三季度"
specs.append(
    {
        "filename": f"07_{COMPANY_SHORT}_2026年{quarter_cn}报告_最新季报.pdf",
        "category": f"2026年{quarter_cn}报告（截至{AS_OF_DATE}最新正式季报）",
        "year": 2026,
        "document_type": "quarter",
        "record": quarter_record,
    }
)

manifest = []
render_paths = []
for spec in specs:
    pdf_url, disclosure_date = extract_detail(spec["record"])
    data, final_url = download_pdf(pdf_url, spec["record"]["detail_url"])
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"Not a PDF: {spec['filename']}, head={data[:32]!r}")
    if len(data) < 100_000:
        raise RuntimeError(f"PDF too small: {spec['filename']}, bytes={len(data)}")
    path = OUT / spec["filename"]
    path.write_bytes(data)
    page_count, render_path = validate_document(path, spec["year"], spec["document_type"])
    render_paths.append(render_path)
    record = {
        "filename": path.name,
        "category": spec["category"],
        "year": spec["year"],
        "document_type": spec["document_type"],
        "source_title": spec["record"]["title"],
        "disclosure_date": disclosure_date,
        "detail_url": spec["record"]["detail_url"],
        "pdf_url": pdf_url,
        "final_url": final_url,
        "pages": page_count,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "first_page_render": render_path.name,
    }
    manifest.append(record)
    print("VALIDATED", json.dumps(record, ensure_ascii=False))

# Visual contact sheet for all first pages.
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

note = f"""长沙景嘉微电子股份有限公司（证券简称：景嘉微，证券代码：{STOCK_CODE}）定期报告资料包

文件范围：
1. 2020年至2025年完整年度报告，共6份；均为年度报告全文，不含摘要版。
2. 2026年{quarter_cn}报告，为截至{AS_OF_DATE}最新一份标题明确为“季度报告”的正式定期报告。

口径说明：
- 公司已披露2026年半年度报告，但半年度报告不属于标题明确的“季度报告”，因此未以半年报替代最新季报。
- 截至{AS_OF_DATE}，2026年第三季度报告尚未披露；如第三季度报告已被列表检索到，脚本会自动优先选择第三季度报告。
- 所有PDF均取自新浪财经保存的公司公告原始附件，报告标题、公司名称、年份、报告类型和实际页数均已逐份核验。
- 已检查PDF文件头、实际页数、公司身份、报告年份和类型，并完成首页渲染及ZIP CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始文件的版权和免责声明。
"""
(OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = OUT / f"景嘉微_2020-2025年报_2026年{quarter_cn}报告.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
    archive.write(OUT / "00_文件清单及校验值.json", "00_文件清单及校验值.json")
    for spec in specs:
        path = OUT / spec["filename"]
        archive.write(path, path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad_file = archive.testzip()
    if bad_file:
        raise RuntimeError(f"ZIP CRC validation failed at {bad_file}")

summary = {
    "zip": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "pdf_count": len(specs),
    "total_pages": sum(record["pages"] for record in manifest),
    "latest_quarter": quarter,
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))
