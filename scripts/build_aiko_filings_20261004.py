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
REPORTS = OUT / "reports"
RENDERS = OUT / "renders"
REPORTS.mkdir(parents=True, exist_ok=True)
RENDERS.mkdir(parents=True, exist_ok=True)

STOCK_CODE = "600732"
COMPANY_SHORT = "爱旭股份"
COMPANY_FULL = "上海爱旭新能源股份有限公司"
AS_OF_DATE = "2026-10-04"
ANNUAL_LIST = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/ndbg.phtml"
Q1_LIST = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/{STOCK_CODE}/page_type/yjdbg.phtml"

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def fetch(url: str, *, referer: str | None = None, timeout: int = 120):
    last_error = None
    for attempt in range(5):
        try:
            headers = {"Referer": referer} if referer else {}
            response = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            print("FETCH", response.status_code, len(response.content), response.url, response.headers.get("content-type"))
            if response.status_code == 200:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {response.url}")
        except Exception as exc:
            last_error = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def parse_list(url: str):
    response = fetch(url, timeout=90)
    response.encoding = response.apparent_encoding or "gb18030"
    soup = BeautifulSoup(response.text, "html.parser")
    records = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(response.url, anchor["href"])
        if "vCB_AllBulletinDetail.php" not in href:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            continue
        records.append(
            {
                "title": title,
                "detail_url": href,
                "list_url": response.url,
            }
        )
    dedup = {}
    for record in records:
        dedup[record["detail_url"]] = record
    result = list(dedup.values())
    print("LIST_COUNT", url, len(result))
    for record in result:
        print("LIST_RECORD", json.dumps(record, ensure_ascii=False))
    return result


annual_records = parse_list(ANNUAL_LIST)
q1_records = parse_list(Q1_LIST)


def is_full_annual(record: dict, year: int):
    title = record["title"]
    if str(year) not in title or "年度报告" not in title:
        return False
    exclusions = (
        "摘要",
        "英文版",
        "审计报告",
        "业绩预告",
        "业绩快报",
        "问询函",
        "回复",
        "社会责任报告",
        "可持续发展报告",
    )
    return not any(term in title for term in exclusions)


def correction_rank(title: str):
    corrected = 3 if any(term in title for term in ("更正后", "更正版", "修订稿", "修订版")) else 0
    full_text = 1 if "全文" in title else 0
    return corrected, full_text, len(title)


selections = []
for index, year in enumerate(range(2020, 2026), 1):
    candidates = [record for record in annual_records if is_full_annual(record, year)]
    candidates.sort(key=lambda record: correction_rank(record["title"]), reverse=True)
    if not candidates:
        raise RuntimeError(f"No full annual report found for {year}")
    selected = candidates[0]
    corrected = any(term in selected["title"] for term in ("更正后", "更正版", "修订稿", "修订版"))
    selections.append(
        {
            **selected,
            "filename": f"{index:02d}_爱旭股份_{year}年年度报告" + ("_更正或修订版" if corrected else "") + ".pdf",
            "category": f"{year}年年度报告",
            "year": str(year),
            "kind": "annual",
            "min_pages": 80,
        }
    )

q1_candidates = []
for record in q1_records:
    title = record["title"]
    if "2026" not in title:
        continue
    if not ("一季度报告" in title or "第一季度报告" in title):
        continue
    if any(term in title for term in ("摘要", "业绩预告", "业绩快报")):
        continue
    q1_candidates.append(record)
q1_candidates.sort(key=lambda record: correction_rank(record["title"]), reverse=True)
if not q1_candidates:
    raise RuntimeError("No 2026 first-quarter report found")
selections.append(
    {
        **q1_candidates[0],
        "filename": "07_爱旭股份_2026年第一季度报告_最新季报.pdf",
        "category": f"2026年第一季度报告（截至{AS_OF_DATE}最新正式季报）",
        "year": "2026",
        "kind": "q1",
        "min_pages": 5,
    }
)


def extract_pdf_urls(detail_url: str):
    response = fetch(detail_url, timeout=90)
    response.encoding = response.apparent_encoding or "gb18030"
    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(response.url, anchor["href"]).replace("&amp;", "&")
        blob = (text + " " + href).lower()
        if "下载公告" in text or ".pdf" in blob or "file.finance.sina.com.cn" in blob or "download" in blob:
            candidates.append(href)
    patterns = [
        r"https?://file\.finance\.sina\.com\.cn/[^\"'<>\s]+?\.PDF(?:\?[^\"'<>\s]*)?",
        r"https?://[^\"'<>\s]+?\.pdf(?:\?[^\"'<>\s]*)?",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, response.text, re.I))
    dedup = []
    seen = set()
    for url in candidates:
        url = url.replace("&amp;", "&")
        if url not in seen:
            seen.add(url)
            dedup.append(url)
    dedup.sort(key=lambda url: (0 if "file.finance.sina.com.cn" in url else 1, 0 if ".pdf" in url.lower() else 1, len(url)))
    print("DETAIL_PDF_URLS", detail_url, json.dumps(dedup, ensure_ascii=False))
    return response.url, dedup


def extract_first_pages_text(path: Path, page_limit: int = 10):
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    chunks = []
    for index in range(min(page_limit, len(reader.pages))):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    text = " ".join(chunks)
    if len(text.strip()) < 100:
        doc = pymupdf.open(str(path))
        text = " ".join(doc[index].get_text("text") for index in range(min(page_limit, doc.page_count)))
        doc.close()
    return re.sub(r"\s+", "", text)


manifest = []
render_paths = []
for selection in selections:
    detail_final_url, pdf_urls = extract_pdf_urls(selection["detail_url"])
    if not pdf_urls:
        raise RuntimeError(f"No PDF URL found for {selection['title']}")

    success_path = None
    candidate_errors = []
    for pdf_url in pdf_urls:
        try:
            response = fetch(pdf_url, referer=detail_final_url, timeout=240)
            data = response.content
            if not data.startswith(b"%PDF-"):
                raise RuntimeError(f"Not a PDF: head={data[:48]!r}")
            if len(data) < 80_000:
                raise RuntimeError(f"PDF too small: {len(data)} bytes")

            path = REPORTS / selection["filename"]
            path.write_bytes(data)
            reader = PdfReader(str(path))
            encrypted = reader.is_encrypted
            if encrypted and not reader.decrypt(""):
                raise RuntimeError("Password-protected PDF")
            pages = len(reader.pages)
            if pages < selection["min_pages"]:
                raise RuntimeError(f"Unexpected page count: {pages}")

            normalized_text = extract_first_pages_text(path)
            identity_tokens = (
                COMPANY_SHORT,
                COMPANY_FULL,
                "上海爱旭新能源",
                "ST爱旭",
                "*ST爱旭",
                STOCK_CODE,
                "AIKOSOLAR",
            )
            if not any(token.replace(" ", "").upper() in normalized_text.upper() for token in identity_tokens):
                raise RuntimeError("Company identity validation failed")
            if selection["year"] not in normalized_text:
                raise RuntimeError("Report-year validation failed")
            if selection["kind"] == "annual":
                type_ok = "年度报告" in normalized_text or "ANNUALREPORT" in normalized_text.upper()
            else:
                type_ok = (
                    "第一季度报告" in normalized_text
                    or "一季度报告" in normalized_text
                    or "FIRSTQUARTERLYREPORT" in normalized_text.upper()
                )
            if not type_ok:
                raise RuntimeError("Document-type validation failed")

            doc = pymupdf.open(str(path))
            for label, page_index in (("first", 0), ("last", doc.page_count - 1)):
                pixmap = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
                render_path = RENDERS / f"{path.stem}_{label}.png"
                pixmap.save(str(render_path))
                render_paths.append(render_path)
            doc.close()

            record = {
                "filename": path.name,
                "category": selection["category"],
                "source_title": selection["title"],
                "source_list_url": selection["list_url"],
                "source_detail_url": selection["detail_url"],
                "download_url": pdf_url,
                "final_url": response.url,
                "pages": pages,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "encrypted": encrypted,
            }
            manifest.append(record)
            print("VERIFIED", json.dumps(record, ensure_ascii=False))
            success_path = path
            break
        except Exception as exc:
            candidate_errors.append({"url": pdf_url, "error": repr(exc)})
            print("PDF_CANDIDATE_FAILED", json.dumps(candidate_errors[-1], ensure_ascii=False))
            try:
                (REPORTS / selection["filename"]).unlink(missing_ok=True)
            except Exception:
                pass

    if success_path is None:
        raise RuntimeError(
            f"All PDF candidates failed for {selection['title']}: {json.dumps(candidate_errors, ensure_ascii=False)}"
        )

# Build contact sheet from first/last pages for visual verification.
thumbs = []
for image_path in render_paths:
    image = Image.open(image_path).convert("RGB")
    image.thumbnail((410, 570))
    canvas = Image.new("RGB", (430, 620), "white")
    canvas.paste(image, ((430 - image.width) // 2, 8))
    ImageDraw.Draw(canvas).text((10, 596), image_path.stem[:58], fill="black")
    thumbs.append(canvas)
    image.close()
cols = 2
rows_n = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 430, rows_n * 620), "white")
for index, canvas in enumerate(thumbs):
    sheet.paste(canvas, ((index % cols) * 430, (index // cols) * 620))
    canvas.close()
contact_sheet = RENDERS / "contact_sheet.jpg"
sheet.save(contact_sheet, "JPEG", quality=90)
sheet.close()

corrected = [r["category"] for r in manifest if any(term in r["source_title"] for term in ("更正后", "更正版", "修订稿", "修订版"))]
note = f"""上海爱旭新能源股份有限公司（证券简称：爱旭股份，证券代码：{STOCK_CODE}）定期报告资料包

文件范围：
1. 2020年至2025年完整年度报告，共6份；不含年度报告摘要。
2. 若公告列表存在更正后、修订稿或修订版，优先收录最新完整版本。本包采用更正/修订版本的年度报告：{', '.join(corrected) if corrected else '无'}。
3. 2026年第一季度报告，为截至{AS_OF_DATE}最新一份标题明确为“季度报告”的正式定期报告。

口径说明：
- 公司已披露2026年半年度报告，但半年度报告不属于标题明确的季度报告，因此未替代一季度报告。
- 截至{AS_OF_DATE}，2026年第三季度报告尚未披露。
- PDF取自新浪财经保存的上市公司公告附件镜像，公告标题按公开列表核对。

完整性检查：
- 已逐份检查PDF文件头、实际页数、加密状态、公司名称、报告年份和报告类型。
- 已渲染并检查每份PDF的首页和末页。
- 压缩包附来源、文件大小和SHA-256校验值，并已执行ZIP CRC完整性测试。
"""
(REPORTS / "00_资料说明.txt").write_text(note, encoding="utf-8")
(REPORTS / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)
(REPORTS / "00_SHA256SUMS.txt").write_text(
    "\n".join(f"{record['sha256']}  {record['filename']}" for record in manifest) + "\n",
    encoding="utf-8",
)

zip_path = OUT / "aiko_solar_2020_2025_annual_reports_and_2026_q1.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(REPORTS.iterdir(), key=lambda p: p.name):
        archive.write(path, arcname=path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad_file = archive.testzip()
    if bad_file:
        raise RuntimeError(f"ZIP CRC failure: {bad_file}")

summary = {
    "zip_filename": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "report_count": len(manifest),
    "total_pages": sum(record["pages"] for record in manifest),
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    "FINAL_SUMMARY",
    json.dumps(
        {key: summary[key] for key in ("zip_filename", "zip_bytes", "zip_sha256", "report_count", "total_pages")},
        ensure_ascii=False,
    ),
)
