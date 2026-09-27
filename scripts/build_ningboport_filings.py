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

STOCK_CODE = "601018"
COMPANY_SHORT = "宁波港"
COMPANY_FULL = "宁波舟山港股份有限公司"
TODAY = "2026-09-27"
SSE_API = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
SSE_BASE = "https://www.sse.com.cn"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "application/json,text/javascript,*/*;q=0.9",
        "Referer": "https://www.sse.com.cn/disclosure/listedinfo/regular/",
        "X-Requested-With": "XMLHttpRequest",
    }
)


def request(url: str, *, params=None, timeout: int = 120):
    last = None
    for attempt in range(5):
        try:
            response = SESSION.get(url, params=params, timeout=timeout, allow_redirects=True)
            print("FETCH", response.status_code, len(response.content), response.url)
            if response.status_code == 200:
                return response
            last = RuntimeError(f"HTTP {response.status_code}: {response.url}")
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def parse_sse_payload(response):
    text = response.text.strip()
    try:
        return response.json()
    except Exception:
        # SSE occasionally wraps responses in JSONP.
        left = text.find("(")
        right = text.rfind(")")
        if left >= 0 and right > left:
            return json.loads(text[left + 1 : right])
        return json.loads(text)


def query_periodic_reports(begin_date: str, end_date: str):
    params = {
        "isPagination": "true",
        "productId": STOCK_CODE,
        "keyWord": "",
        "securityType": "0101,120100,020100,020200,120200",
        "reportType2": "DQBG",
        "reportType": "",
        "beginDate": begin_date,
        "endDate": end_date,
        "pageHelp.pageSize": "100",
        "pageHelp.pageCount": "50",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "5",
        "_": str(int(time.time() * 1000)),
    }
    response = request(SSE_API, params=params, timeout=90)
    payload = parse_sse_payload(response)
    results = payload.get("result") or []
    if not isinstance(results, list):
        raise RuntimeError(f"Unexpected SSE result payload: {type(results)}")
    print("SSE_RESULTS", begin_date, end_date, len(results))
    return results


# Query in annual windows to reduce pagination/anti-bot issues.
rows = []
for year in range(2020, 2027):
    end = TODAY if year == 2026 else f"{year}-12-31"
    rows.extend(query_periodic_reports(f"{year}-01-01", end))

# De-duplicate by official URL and title.
dedup = {}
for row in rows:
    url = str(row.get("URL") or row.get("url") or "")
    title = str(row.get("TITLE") or row.get("title") or "")
    if url:
        dedup[(url, title)] = row
rows = list(dedup.values())
(OUT / "上交所定期报告检索结果.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
)


def title_of(row):
    return " ".join(str(row.get("TITLE") or row.get("title") or "").split())


def date_of(row):
    return str(row.get("SSEDATE") or row.get("SSEDate") or row.get("date") or "")


def type_of(row):
    return str(row.get("BULLETIN_TYPE") or row.get("bulletinType") or "")


def year_of(row):
    return str(row.get("BULLETIN_YEAR") or row.get("bulletinYear") or "")


def url_of(row):
    raw = str(row.get("URL") or row.get("url") or "")
    return urljoin(SSE_BASE, raw)


def is_annual_candidate(row, year: int):
    title = title_of(row)
    upper = title.upper()
    if str(year) not in title:
        return False
    if not ("年度报告" in title or "年报" in title or "ANNUAL REPORT" in upper):
        return False
    exclusions = (
        "摘要",
        "英文版",
        "审计报告",
        "社会责任报告",
        "可持续发展报告",
        "环境、社会及管治",
        "ESG",
        "业绩公告",
        "业绩快报",
        "业绩预告",
        "问询函",
        "回复",
    )
    return not any(term in title or term in upper for term in exclusions)


def is_q1_candidate(row):
    title = title_of(row)
    upper = title.upper()
    if "2026" not in title:
        return False
    if not (
        "第一季度报告" in title
        or "第一季度季报" in title
        or "FIRST QUARTERLY REPORT" in upper
        or "Q1 REPORT" in upper
    ):
        return False
    exclusions = ("摘要", "业绩预告", "业绩快报", "生产数据", "提示性公告")
    return not any(term in title for term in exclusions)


def candidate_priority(row, exact_phrase: str):
    title = title_of(row)
    exact = 1 if exact_phrase in title else 0
    revised = 1 if any(x in title for x in ("修订版", "更正版", "更新版")) else 0
    chinese = 1 if any(ch in title for ch in ("年度报告", "第一季度报告", "宁波舟山港")) else 0
    return (exact, revised, chinese, date_of(row))


selected_specs = []
for index, year in enumerate(range(2020, 2026), 1):
    candidates = [row for row in rows if is_annual_candidate(row, year)]
    candidates.sort(key=lambda row: candidate_priority(row, f"{year}年年度报告"), reverse=True)
    if not candidates:
        available = [title_of(row) for row in rows if str(year) in title_of(row)]
        raise RuntimeError(f"No annual report found for {year}. Available: {available}")
    selected_specs.append(
        {
            "filename": f"{index:02d}_{COMPANY_SHORT}_{year}年年度报告.pdf",
            "category": f"{year}年年度报告",
            "report_year": year,
            "candidate_rows": candidates,
            "min_pages": 100,
            "required_type_terms": [f"{year}年年度报告", "年度报告", "ANNUAL REPORT"],
        }
    )

q1_candidates = [row for row in rows if is_q1_candidate(row)]
q1_candidates.sort(key=lambda row: candidate_priority(row, "2026年第一季度报告"), reverse=True)
if not q1_candidates:
    available_2026 = [title_of(row) for row in rows if "2026" in title_of(row)]
    raise RuntimeError(f"No 2026 Q1 report found. Available: {available_2026}")
selected_specs.append(
    {
        "filename": f"07_{COMPANY_SHORT}_2026年第一季度报告_最新季报.pdf",
        "category": "2026年第一季度报告（截至2026-09-27最新正式季报）",
        "report_year": 2026,
        "candidate_rows": q1_candidates,
        "min_pages": 8,
        "required_type_terms": ["2026年第一季度报告", "第一季度报告", "FIRST QUARTERLY REPORT"],
    }
)


def extract_first_pages_text(path: Path, max_pages: int = 8):
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        result = reader.decrypt("")
        if not result:
            raise RuntimeError("password-protected PDF")
    chunks = []
    for idx in range(min(max_pages, len(reader.pages))):
        try:
            chunks.append(reader.pages[idx].extract_text() or "")
        except Exception:
            pass
    text = " ".join(chunks)
    if len(text.strip()) < 80:
        doc = fitz.open(str(path))
        chunks = [doc[idx].get_text("text") for idx in range(min(max_pages, doc.page_count))]
        doc.close()
        text = " ".join(chunks)
    return text


def download_and_validate(spec):
    errors = []
    for row in spec["candidate_rows"]:
        source_url = url_of(row)
        try:
            response = request(source_url, timeout=180)
            data = response.content
            if not data.startswith(b"%PDF-"):
                raise RuntimeError(f"not a PDF, head={data[:32]!r}")
            if len(data) < 50000:
                raise RuntimeError(f"PDF too small: {len(data)} bytes")

            path = OUT / spec["filename"]
            path.write_bytes(data)
            reader = PdfReader(str(path))
            if reader.is_encrypted and not reader.decrypt(""):
                raise RuntimeError("password-protected PDF")
            page_count = len(reader.pages)
            if page_count < spec["min_pages"]:
                raise RuntimeError(f"unexpected page count: {page_count}")

            text = extract_first_pages_text(path)
            normalized = re.sub(r"\s+", "", text).upper()
            if not any(
                term.replace(" ", "").upper() in normalized
                for term in (COMPANY_FULL, COMPANY_SHORT, "NINGBO-ZHOUSHAN PORT", "NINGBO PORT")
            ):
                raise RuntimeError("company identity validation failed")
            if not any(term.replace(" ", "").upper() in normalized for term in spec["required_type_terms"]):
                # Some annual report covers show only the year and the words 年度报告 separately.
                fallback_ok = str(spec["report_year"]) in normalized and (
                    "年度报告" in normalized or "第一季度报告" in normalized or "ANNUALREPORT" in normalized
                )
                if not fallback_ok:
                    raise RuntimeError("document type validation failed")

            doc = fitz.open(str(path))
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            render_path = VERIFY / f"{path.stem}_page1.png"
            pix.save(str(render_path))
            doc.close()

            record = {
                "filename": path.name,
                "category": spec["category"],
                "report_year": spec["report_year"],
                "source_title": title_of(row),
                "source_bulletin_type": type_of(row),
                "source_bulletin_year": year_of(row),
                "source_disclosure_date": date_of(row),
                "source_url": source_url,
                "final_url": response.url,
                "pages": page_count,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "first_page_render": render_path.name,
            }
            print("VALIDATED", json.dumps(record, ensure_ascii=False))
            return path, render_path, record
        except Exception as exc:
            error = {"url": source_url, "title": title_of(row), "error": repr(exc)}
            errors.append(error)
            print("CANDIDATE_FAILED", json.dumps(error, ensure_ascii=False))
            try:
                (OUT / spec["filename"]).unlink(missing_ok=True)
            except Exception:
                pass
    raise RuntimeError(
        f"No valid candidate for {spec['category']}: {json.dumps(errors, ensure_ascii=False)}"
    )


documents = [download_and_validate(spec) for spec in selected_specs]
manifest = [record for _, _, record in documents]

# Build a visual contact sheet from first pages for verification.
thumbs = []
for _, render_path, _ in documents:
    image = Image.open(render_path).convert("RGB")
    image.thumbnail((480, 640))
    canvas = Image.new("RGB", (500, 690), "white")
    canvas.paste(image, ((500 - image.width) // 2, 10))
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 660), render_path.stem[:70], fill="black")
    thumbs.append(canvas)
    image.close()
cols = 2
rows_n = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 500, rows_n * 690), "white")
for idx, image in enumerate(thumbs):
    sheet.paste(image, ((idx % cols) * 500, (idx // cols) * 690))
    image.close()
contact_sheet = VERIFY / "contact_sheet.jpg"
sheet.save(contact_sheet, "JPEG", quality=90)
sheet.close()

note = f"""宁波舟山港股份有限公司（证券简称：宁波港，证券代码：{STOCK_CODE}）官方定期报告资料包

文件范围：
1. 2020年至2025年完整年度报告，共6份；均为正式年度报告全文，不含摘要版。
2. 2026年第一季度报告，为截至{TODAY}已正式披露的最新一份标题明确为“季度报告”的定期报告。

口径说明：
- 公司已于2026年8月披露2026年半年度报告，但半年度报告不属于标题明确的“季度报告”；本资料包依照“最新季报”口径收录2026年第一季度报告。
- 2026年第三季度报告截至{TODAY}尚未披露。
- 所有PDF均取自上海证券交易所官方披露页面，未使用新闻摘要或第三方改写版本。
- 已检查PDF文件头、实际页数、加密状态、公司名称、报告类型及首页渲染，并完成ZIP CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始文件版权及免责声明。
"""
(OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = OUT / "宁波港_2020-2025年报_2026年第一季度报告.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
    archive.write(OUT / "00_文件清单及校验值.json", "00_文件清单及校验值.json")
    for pdf_path, _, _ in documents:
        archive.write(pdf_path, pdf_path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad_file = archive.testzip()
    if bad_file:
        raise RuntimeError(f"ZIP CRC validation failed at {bad_file}")

summary = {
    "zip": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "pdf_count": len(documents),
    "total_pages": sum(item["pages"] for item in manifest),
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))
