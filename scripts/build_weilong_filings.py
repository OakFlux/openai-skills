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
from PIL import Image, ImageDraw
from pypdf import PdfReader

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

STOCK_ID = "1000126167"
API = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
BASE = "https://www1.hkexnews.hk/"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def parse_json_response(resp):
    text = resp.text.strip()
    try:
        return resp.json()
    except Exception:
        left = text.find("(")
        right = text.rfind(")")
        if left >= 0 and right > left:
            return json.loads(text[left + 1 : right])
        return json.loads(text)


def collect_rows(obj, out):
    if isinstance(obj, str):
        try:
            collect_rows(json.loads(obj), out)
        except Exception:
            return
    elif isinstance(obj, list):
        for item in obj:
            collect_rows(item, out)
    elif isinstance(obj, dict):
        keys = {str(k).upper() for k in obj.keys()}
        if "TITLE" in keys and ("FILE_LINK" in keys or "FILELINK" in keys):
            out.append(obj)
        for value in obj.values():
            collect_rows(value, out)


def query_hkex(lang):
    params = {
        "sortDir": "1",
        "sortByOptions": "DateTime",
        "category": "0",
        "market": "SEHK",
        "stockId": STOCK_ID,
        "documentType": "-1",
        "fromDate": "20220101",
        "toDate": "20260927",
        "title": "",
        "searchType": "1",
        "t1code": "-2",
        "t2Gcode": "-2",
        "t2code": "-2",
        "rowRange": "2000",
        "lang": lang,
    }
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang={lang.upper()}&market=SEHK&stockId={STOCK_ID}",
        "X-Requested-With": "XMLHttpRequest",
    }
    last = None
    for attempt in range(5):
        try:
            r = SESSION.get(API, params=params, headers=headers, timeout=90)
            print("HKEX_QUERY", lang, r.status_code, len(r.content), r.url)
            r.raise_for_status()
            obj = parse_json_response(r)
            rows = []
            collect_rows(obj, rows)
            print("HKEX_ROWS", lang, len(rows))
            return rows
        except Exception as exc:
            last = exc
            time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"HKEX query failed for {lang}: {last}")


def title_of(row):
    return " ".join(str(row.get("TITLE") or row.get("title") or "").split())


def link_of(row):
    link = row.get("FILE_LINK") or row.get("fileLink") or row.get("FILELINK") or ""
    return urljoin(BASE, str(link))


def date_of(row):
    return str(row.get("DATE_TIME") or row.get("dateTime") or row.get("RELEASE_TIME") or "")


def info_of(row):
    return str(row.get("FILE_INFO") or row.get("fileInfo") or "")


def size_score(info):
    match = re.search(r"([0-9.]+)\s*(MB|KB)", info, re.I)
    if not match:
        return 0.0
    value = float(match.group(1))
    return value * 1024 if match.group(2).upper() == "MB" else value


def preference(row):
    title = title_of(row)
    link = link_of(row).lower()
    chinese = "_c.pdf" in link or any(ch in title for ch in ("年報", "年度報告", "中期報告", "全球發售"))
    return (1 if chinese else 0, size_score(info_of(row)), date_of(row))


def annual_candidates(rows, year):
    out = []
    for row in rows:
        title = title_of(row)
        upper = title.upper()
        if str(year) not in title:
            continue
        if not ("ANNUAL REPORT" in upper or "年報" in title or "年度報告" in title):
            continue
        if any(term in upper for term in ("RESULTS", "NOTIFICATION", "LETTER", "ESG", "ENVIRONMENTAL", "FORM OF PROXY")):
            continue
        if ".pdf" not in link_of(row).lower():
            continue
        out.append(row)
    out.sort(key=preference, reverse=True)
    return out


def interim_candidates(rows, year):
    out = []
    for row in rows:
        title = title_of(row)
        upper = title.upper()
        if str(year) not in title:
            continue
        if not ("INTERIM REPORT" in upper or "中期報告" in title or "中期报告" in title):
            continue
        if any(term in upper for term in ("RESULTS", "ANNOUNCEMENT", "NOTIFICATION", "LETTER", "DIVIDEND")):
            continue
        if ".pdf" not in link_of(row).lower():
            continue
        out.append(row)
    out.sort(key=preference, reverse=True)
    return out


def prospectus_candidates(rows):
    out = []
    exclusions = (
        "FORMAL NOTICE",
        "APPLICATION FORM",
        "WHITE FORM",
        "YELLOW FORM",
        "GREEN FORM",
        "ALLOTMENT",
        "RESULTS OF ALLOCATION",
        "PRICE RANGE",
        "LIST OF DIRECTORS",
        "DOCUMENTS ON DISPLAY",
    )
    for row in rows:
        title = title_of(row)
        upper = title.upper()
        if not ("GLOBAL OFFERING" in upper or "全球發售" in title or "全球发售" in title):
            continue
        if any(term in upper for term in exclusions):
            continue
        if ".pdf" not in link_of(row).lower():
            continue
        out.append(row)
    out.sort(key=preference, reverse=True)
    return out


def download(url, timeout=180):
    headers = {"Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml"}
    last = None
    for attempt in range(5):
        try:
            r = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            print("DOWNLOAD", r.status_code, len(r.content), r.url)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Download failed: {url}: {last}")


def extract_text(path, pages=5):
    chunks = []
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError("password-protected PDF")
    for index in range(min(pages, len(reader.pages))):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    text = " ".join(chunks)
    if len(text.strip()) < 50:
        doc = fitz.open(str(path))
        chunks = [doc[index].get_text("text") for index in range(min(pages, doc.page_count))]
        doc.close()
        text = " ".join(chunks)
    return text


rows = query_hkex("en") + query_hkex("zh")
dedup = {}
for row in rows:
    link = row.get("FILE_LINK") or row.get("fileLink") or row.get("FILELINK") or ""
    title = row.get("TITLE") or row.get("title") or ""
    if link:
        dedup[(str(link), str(title))] = row
rows = list(dedup.values())
(OUT / "HKEX检索结果.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

selected = []
for year in (2022, 2023, 2024, 2025):
    candidates = annual_candidates(rows, year)
    if not candidates:
        raise RuntimeError(f"No annual report candidate found for {year}")
    selected.append(
        {
            "filename": f"{year - 2021:02d}_卫龙美味_{year}年年度报告.pdf",
            "title": f"卫龙美味全球控股有限公司 - {year}年年度报告",
            "category": f"{year}年年度报告",
            "candidates": candidates,
            "min_pages": 100,
            "type_terms": ["ANNUAL REPORT", "年報", "年度報告"],
        }
    )

prospectus = prospectus_candidates(rows)
if not prospectus:
    raise RuntimeError("No final prospectus candidate found")
selected.append(
    {
        "filename": "05_卫龙美味_最终版招股说明书_2022-12-05.pdf",
        "title": "卫龙美味全球控股有限公司 - 全球发售（最终版招股说明书）",
        "category": "最终版招股说明书",
        "candidates": prospectus,
        "min_pages": 300,
        "type_terms": ["GLOBAL OFFERING", "全球發售", "全球发售"],
    }
)

interim = interim_candidates(rows, 2026)
if not interim:
    raise RuntimeError("No 2026 interim report candidate found")
selected.append(
    {
        "filename": "06_卫龙美味_2026年中期报告_最新定期财报.pdf",
        "title": "卫龙美味全球控股有限公司 - 2026年中期报告",
        "category": "最新定期财报",
        "candidates": interim,
        "min_pages": 40,
        "type_terms": ["INTERIM REPORT", "中期報告", "中期报告"],
    }
)

manifest = []
pdf_paths = []
render_paths = []
for spec in selected:
    errors = []
    saved = None
    for row in spec["candidates"]:
        url = link_of(row)
        try:
            response = download(url)
            data = response.content
            if not data.startswith(b"%PDF-"):
                raise RuntimeError(f"not a PDF: {data[:24]!r}")
            if len(data) < 50000:
                raise RuntimeError(f"PDF too small: {len(data)}")
            path = OUT / spec["filename"]
            path.write_bytes(data)
            reader = PdfReader(str(path))
            if reader.is_encrypted and not reader.decrypt(""):
                raise RuntimeError("password-protected PDF")
            page_count = len(reader.pages)
            if page_count < spec["min_pages"]:
                raise RuntimeError(f"unexpected page count: {page_count}")
            text = extract_text(path, 5)
            upper = text.upper()
            if not any(term.upper() in upper for term in ("WEILONG", "衛龍美味", "卫龙美味")):
                raise RuntimeError("company identity validation failed")
            if not any(term.upper() in upper for term in spec["type_terms"]):
                raise RuntimeError(f"document type validation failed: {spec['type_terms']}")

            doc = fitz.open(str(path))
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            render = VERIFY / (path.stem + "_page1.png")
            pix.save(str(render))
            doc.close()

            record = {
                "filename": path.name,
                "title": spec["title"],
                "category": spec["category"],
                "source_title": title_of(row),
                "source_date": date_of(row),
                "source_file_info": info_of(row),
                "source_url": url,
                "final_url": response.url,
                "pages": page_count,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "first_page_render": render.name,
            }
            manifest.append(record)
            pdf_paths.append(path)
            render_paths.append(render)
            saved = record
            print("VALIDATED", json.dumps(record, ensure_ascii=False))
            break
        except Exception as exc:
            error = {"url": url, "title": title_of(row), "error": repr(exc)}
            errors.append(error)
            print("CANDIDATE_FAILED", json.dumps(error, ensure_ascii=False))
            try:
                (OUT / spec["filename"]).unlink(missing_ok=True)
            except Exception:
                pass
    if saved is None:
        raise RuntimeError(f"No valid candidate for {spec['category']}: {json.dumps(errors, ensure_ascii=False)}")

# Visual verification contact sheet.
thumbs = []
for render in render_paths:
    image = Image.open(render).convert("RGB")
    image.thumbnail((500, 650))
    canvas = Image.new("RGB", (520, 700), "white")
    canvas.paste(image, ((520 - image.width) // 2, 10))
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 670), render.stem[:65], fill="black")
    thumbs.append(canvas)
    image.close()
cols = 2
rows_n = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 520, rows_n * 700), "white")
for index, image in enumerate(thumbs):
    sheet.paste(image, ((index % cols) * 520, (index // cols) * 700))
    image.close()
sheet_path = VERIFY / "contact_sheet.jpg"
sheet.save(sheet_path, "JPEG", quality=90)
sheet.close()

note = """卫龙美味全球控股有限公司（09985.HK）官方披露文件资料包

文件范围：
1. 2022年、2023年、2024年、2025年全部正式年度报告。
2. 2022年12月5日最终版招股说明书（全球发售）。
3. 2026年中期报告，为截至2026年9月27日最新正式定期财务报告。

说明：
- 卫龙美味于2022年12月15日在香港联交所主板上市，因此上市后正式年报从2022年度开始，本资料包已全部收录。
- 香港主板发行人通常披露年度报告和中期报告，并不强制发布季度报告，因此以2026年中期报告对应“最新季报/最新定期财报”。
- 文件均取自香港交易所披露易公开系统；未重复收录历年中期报告、业绩公告、聆讯后资料集或申请版本招股书。
- 所有PDF均完成文件头、实际页数、加密状态、公司名称、报告类型及首页渲染检查。
- 文件仅供个人研究与学习使用，请遵守原始文件的版权及免责声明。
"""
(OUT / "资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "文件清单及校验值.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

zip_path = OUT / "卫龙美味_全部年报_招股说明书_最新定期财报.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in pdf_paths:
        archive.write(path, path.name)
    archive.write(OUT / "资料说明.txt", "资料说明.txt")
    archive.write(OUT / "文件清单及校验值.json", "文件清单及校验值.json")
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f"ZIP CRC validation failed at {bad}")

summary = {
    "zip": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "total_pdf_pages": sum(item["pages"] for item in manifest),
    "reports": manifest,
    "verification_contact_sheet": str(sheet_path),
}
(OUT / "BUILD_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))
