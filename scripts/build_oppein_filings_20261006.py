from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pymupdf
import requests
from PIL import Image, ImageDraw
from pypdf import PdfReader

STOCK_CODE = "603833"
COMPANY_TOKENS = ("欧派家居", "欧派家居集团", STOCK_CODE)
AS_OF_DATE = "2026-10-06"

OUT = Path("output")
REPORTS = OUT / "reports"
RENDERS = OUT / "renders"
OUT.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)
RENDERS.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Referer": "https://data.eastmoney.com/notices/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def get_json(url: str, *, params: dict[str, str]) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = session.get(url, params=params, timeout=120)
            print("FETCH_JSON", response.status_code, len(response.content), response.url)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(min(10, 2**attempt))
    raise RuntimeError(f"Unable to fetch JSON after retries: {url}: {last_error}")


def fetch_pdf(url: str) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = session.get(url, timeout=240, allow_redirects=True)
            print("FETCH_PDF", response.status_code, len(response.content), response.url, response.headers.get("content-type"))
            if response.status_code == 200 and response.content.startswith(b"%PDF-"):
                return response
            last_error = RuntimeError(
                f"HTTP {response.status_code}; content type={response.headers.get('content-type')}; head={response.content[:40]!r}"
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(min(10, 2**attempt))
    raise RuntimeError(f"Unable to fetch PDF after retries: {url}: {last_error}")


def parse_notice_date(row: dict[str, Any]) -> datetime:
    raw = str(row.get("notice_date") or row.get("display_time") or "1900-01-01")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return datetime(1900, 1, 1)


def title_of(row: dict[str, Any]) -> str:
    return str(row.get("title") or row.get("title_ch") or "").strip()


def column_names(row: dict[str, Any]) -> str:
    cols = row.get("columns") or []
    names: list[str] = []
    for col in cols:
        if isinstance(col, dict):
            name = str(col.get("column_name") or "").strip()
            if name:
                names.append(name)
    return " / ".join(names)


def revision_priority(title: str) -> int:
    if "更正后" in title or "修订版" in title or "修订后" in title:
        return 3
    if "更新后" in title:
        return 2
    return 1


def is_full_annual_report(title: str, year: int) -> bool:
    if f"{year}年年度报告" not in title:
        return False
    blocked = ("摘要", "更正公告", "修订公告", "取消", "业绩说明会", "预约", "英文版")
    return not any(term in title for term in blocked)


def quarter_period(title: str) -> tuple[int, int] | None:
    match = re.search(r"(20\d{2})年", title)
    if not match:
        return None
    year = int(match.group(1))
    if "第三季度报告" in title or "三季度报告" in title:
        return year, 3
    if "第一季度报告" in title or "一季度报告" in title:
        return year, 1
    return None


def is_full_quarter_report(title: str) -> bool:
    if quarter_period(title) is None:
        return False
    blocked = ("摘要", "更正公告", "修订公告", "取消", "预约", "业绩预告", "业绩说明会", "主要经营数据")
    return not any(term in title for term in blocked)


# 1) Retrieve the complete public announcement history.
api = "https://np-anotice-stock.eastmoney.com/api/security/ann"
all_rows: list[dict[str, Any]] = []
for page in range(1, 40):
    params = {
        "sr": "-1",
        "page_size": "100",
        "page_index": str(page),
        "ann_type": "A",
        "client_source": "web",
        "stock_list": STOCK_CODE,
    }
    obj = get_json(api, params=params)
    data = obj.get("data") or {}
    rows = data.get("list") or []
    total_hits = int(data.get("total_hits") or 0)
    print("ANNOUNCEMENT_PAGE", page, "ROWS", len(rows), "TOTAL", total_hits)
    all_rows.extend(rows)
    if not rows or len(all_rows) >= total_hits:
        break

if not all_rows:
    raise RuntimeError("No announcements returned for 欧派家居")

# 2) Select the latest effective full annual report for each requested year.
selected: list[dict[str, Any]] = []
audit: dict[str, Any] = {"as_of_date": AS_OF_DATE, "stock_code": STOCK_CODE, "annual_candidates": {}, "quarter_candidates": []}

for year in range(2020, 2026):
    candidates = [row for row in all_rows if is_full_annual_report(title_of(row), year)]
    if not candidates:
        raise RuntimeError(f"No full annual report found for {year}")
    candidates.sort(key=lambda row: (parse_notice_date(row), revision_priority(title_of(row))), reverse=True)
    chosen = candidates[0]
    audit["annual_candidates"][str(year)] = [
        {
            "title": title_of(row),
            "art_code": row.get("art_code"),
            "notice_date": row.get("notice_date"),
            "columns": column_names(row),
        }
        for row in candidates
    ]
    selected.append(
        {
            "filename": f"{year - 2019:02d}_欧派家居_{year}年年度报告" + ("_修订版.pdf" if revision_priority(title_of(chosen)) >= 2 else ".pdf"),
            "category": f"{year}年年度报告" + ("（修订/更正后的最新有效版本）" if revision_priority(title_of(chosen)) >= 2 else ""),
            "row": chosen,
        }
    )

# 3) Select the latest formally filed first- or third-quarter report by reporting period.
quarter_candidates = [row for row in all_rows if is_full_quarter_report(title_of(row))]
if not quarter_candidates:
    raise RuntimeError("No quarterly report candidates found")
quarter_candidates.sort(
    key=lambda row: (
        quarter_period(title_of(row)) or (0, 0),
        parse_notice_date(row),
        revision_priority(title_of(row)),
    ),
    reverse=True,
)
latest_quarter = quarter_candidates[0]
latest_period = quarter_period(title_of(latest_quarter))
assert latest_period is not None
q_year, q_no = latest_period
q_label = "第一季度" if q_no == 1 else "第三季度"
audit["quarter_candidates"] = [
    {
        "title": title_of(row),
        "art_code": row.get("art_code"),
        "notice_date": row.get("notice_date"),
        "period": quarter_period(title_of(row)),
        "columns": column_names(row),
    }
    for row in quarter_candidates[:20]
]
selected.append(
    {
        "filename": f"07_欧派家居_{q_year}年{q_label}报告_最新季报.pdf",
        "category": f"{q_year}年{q_label}报告（截至{AS_OF_DATE}最新正式季报）",
        "row": latest_quarter,
    }
)

print("SELECTED_FILINGS")
for item in selected:
    row = item["row"]
    print(
        json.dumps(
            {
                "filename": item["filename"],
                "category": item["category"],
                "title": title_of(row),
                "art_code": row.get("art_code"),
                "date": str(row.get("notice_date") or "")[:10],
                "columns": column_names(row),
            },
            ensure_ascii=False,
        )
    )

# 4) Download, validate, render, and record each selected PDF.
manifest: list[dict[str, Any]] = []
render_paths: list[Path] = []
for item in selected:
    row = item["row"]
    art_code = str(row.get("art_code") or "").strip()
    if not art_code:
        raise RuntimeError(f"Missing art_code for {title_of(row)}")

    urls = [
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}.pdf",
    ]
    response: requests.Response | None = None
    source_url = ""
    errors: list[str] = []
    for url in urls:
        try:
            response = fetch_pdf(url)
            source_url = url
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc!r}")
    if response is None:
        raise RuntimeError(f"All PDF URLs failed for {title_of(row)}: {errors}")

    content = response.content
    if len(content) < 100_000:
        raise RuntimeError(f"Suspiciously small PDF for {title_of(row)}: {len(content)} bytes")

    path = REPORTS / item["filename"]
    path.write_bytes(content)

    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = len(reader.pages)
    if pages < 5:
        raise RuntimeError(f"Unexpectedly short report: {path.name}, {pages} pages")

    doc = pymupdf.open(str(path))
    sample_pages = min(8, doc.page_count)
    text = "".join(doc[i].get_text("text") for i in range(sample_pages))
    normalized = re.sub(r"\s+", "", text)
    if not any(token in normalized for token in COMPANY_TOKENS):
        raise RuntimeError(f"Company identity check failed: {path.name}")

    # Ensure annual/quarter type and reporting period can be found in the report text or title metadata.
    title = title_of(row)
    category = item["category"]
    if "年度报告" in category:
        year_match = re.search(r"(20\d{2})年", category)
        if year_match and year_match.group(1) not in normalized and year_match.group(1) not in title:
            raise RuntimeError(f"Year check failed: {path.name}")
    else:
        if "季度报告" not in normalized and "季度报告" not in title and "一季度报告" not in title and "三季度报告" not in title:
            raise RuntimeError(f"Quarter report type check failed: {path.name}")

    for label, index in (("first", 0), ("last", doc.page_count - 1)):
        pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(1.35, 1.35), alpha=False)
        render_path = RENDERS / f"{path.stem}_{label}.png"
        pix.save(str(render_path))
        render_paths.append(render_path)
    doc.close()

    record = {
        "filename": path.name,
        "category": category,
        "announcement_title": title,
        "art_code": art_code,
        "notice_date": str(row.get("notice_date") or "")[:10],
        "announcement_page": f"https://data.eastmoney.com/notices/detail/{STOCK_CODE}/{art_code}.html",
        "source_url": source_url,
        "final_url": response.url,
        "pages": pages,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "encrypted": reader.is_encrypted,
    }
    manifest.append(record)
    print("VERIFIED", json.dumps(record, ensure_ascii=False))

# 5) Create a visual contact sheet used for manual inspection.
thumbs: list[Image.Image] = []
for image_path in render_paths:
    image = Image.open(image_path).convert("RGB")
    image.thumbnail((420, 580))
    canvas = Image.new("RGB", (440, 630), "white")
    canvas.paste(image, ((440 - image.width) // 2, 8))
    ImageDraw.Draw(canvas).text((10, 605), image_path.stem[:58], fill="black")
    thumbs.append(canvas)
    image.close()

cols = 2
rows = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 440, rows * 630), "white")
for idx, canvas in enumerate(thumbs):
    sheet.paste(canvas, ((idx % cols) * 440, (idx // cols) * 630))
    canvas.close()
contact_sheet = RENDERS / "contact_sheet.jpg"
sheet.save(contact_sheet, "JPEG", quality=90)
sheet.close()

# 6) Add documentation and checksums.
readme = [
    "欧派家居（603833.SH）2020—2025年年度报告及最新季度报告资料包",
    "",
    f"检索截止日期：{AS_OF_DATE}",
    "年度报告按年度逐一筛选完整全文；如存在修订版或更正后全文，优先采用最新有效版本。",
    "“最新季报”按标题明确为第一季度或第三季度报告的正式全文口径筛选，不以半年度报告替代。",
    "",
    "文件清单：",
]
for record in manifest:
    readme.append(
        f"- {record['filename']} | {record['category']} | {record['notice_date']} | {record['pages']}页 | {record['bytes']}字节"
    )
readme += [
    "",
    "核验项目：PDF文件头、公司名称/证券代码、报告期、实际页数、加密状态、首页及末页渲染。",
    "压缩包已执行ZIP CRC完整性检测。",
    "资料仅供个人研究与学习使用，请遵守原始文件版权与免责声明。",
]
(REPORTS / "00_资料说明.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")
(REPORTS / "00_文件清单及校验值.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
(REPORTS / "00_筛选审计记录.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
(REPORTS / "00_SHA256SUMS.txt").write_text(
    "\n".join(f"{record['sha256']}  {record['filename']}" for record in manifest) + "\n",
    encoding="utf-8",
)

# 7) Package in a single-layer ZIP and run CRC integrity test.
zip_path = OUT / "oppein_2020_2025_annual_reports_latest_quarter.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(REPORTS.iterdir(), key=lambda p: p.name):
        archive.write(path, arcname=path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f"ZIP CRC failure: {bad}")

summary = {
    "zip_filename": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "report_count": len(manifest),
    "total_pages": sum(record["pages"] for record in manifest),
    "latest_quarter_category": manifest[-1]["category"],
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    "FINAL_SUMMARY",
    json.dumps(
        {key: summary[key] for key in ("zip_filename", "zip_bytes", "zip_sha256", "report_count", "total_pages", "latest_quarter_category")},
        ensure_ascii=False,
    ),
)
