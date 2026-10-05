#!/usr/bin/env python3
import hashlib
import json
import math
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pymupdf
import requests
from PIL import Image, ImageDraw
from pypdf import PdfReader

STOCK_CODE = "603200"
COMPANY_NAME = "上海洗霸"
AS_OF_DATE = "2026-10-05"
YEARS = list(range(2020, 2026))

OUT = Path("output")
REPORTS = OUT / "reports"
RENDERS = OUT / "renders"
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


def request_with_retry(url, *, params=None, timeout=180, expect_pdf=False):
    last_error = None
    for attempt in range(6):
        try:
            response = session.get(url, params=params, timeout=timeout, allow_redirects=True)
            print(
                "FETCH",
                response.status_code,
                len(response.content),
                response.url,
                response.headers.get("content-type"),
            )
            if response.status_code == 200:
                if not expect_pdf or response.content.startswith(b"%PDF-"):
                    return response
            last_error = RuntimeError(
                f"HTTP {response.status_code}; content head={response.content[:48]!r}"
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def get_all_announcements():
    api = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    rows = []
    for page in range(1, 30):
        params = {
            "sr": "-1",
            "page_size": "100",
            "page_index": str(page),
            "ann_type": "A",
            "client_source": "web",
            "stock_list": STOCK_CODE,
        }
        response = request_with_retry(api, params=params, timeout=90)
        obj = response.json()
        data = obj.get("data") or {}
        page_rows = data.get("list") or []
        total_hits = int(data.get("total_hits") or 0)
        print("ANNOUNCEMENT_PAGE", page, "ROWS", len(page_rows), "TOTAL", total_hits)
        rows.extend(page_rows)
        if not page_rows or len(rows) >= total_hits:
            break
    if not rows:
        raise RuntimeError("No announcements returned for Shanghai Xiba")
    return rows


def row_title(row):
    return str(row.get("title") or row.get("title_ch") or "").strip()


def row_columns(row):
    return [str(col.get("column_name") or "") for col in (row.get("columns") or [])]


def parse_notice_date(row):
    raw = str(row.get("notice_date") or row.get("sort_date") or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return datetime.min


def correction_priority(title):
    if any(token in title for token in ("更正后", "修订版", "修订稿", "修订后")):
        return 3
    if "更新后" in title:
        return 2
    return 1


def is_full_annual(row, year):
    title = row_title(row)
    columns = row_columns(row)
    if f"{year}年年度报告" not in title:
        return False
    if any(token in title for token in ("摘要", "更正公告", "业绩说明会", "预告", "问询", "回复")):
        return False
    if columns and not any("年度报告全文" in col for col in columns):
        return False
    return True


def annual_candidates(rows, year):
    candidates = [row for row in rows if is_full_annual(row, year)]
    candidates.sort(
        key=lambda row: (
            parse_notice_date(row),
            correction_priority(row_title(row)),
            str(row.get("art_code") or ""),
        ),
        reverse=True,
    )
    return candidates


def quarter_period(title):
    match = re.search(r"(20\d{2})年", title)
    if not match:
        return None
    year = int(match.group(1))
    if "第三季度" in title or "三季度" in title:
        quarter = 3
    elif "第一季度" in title or "一季度" in title:
        quarter = 1
    else:
        return None
    return year, quarter


def is_full_quarter(row):
    title = row_title(row)
    columns = row_columns(row)
    period = quarter_period(title)
    if period is None:
        return False
    if any(token in title for token in ("摘要", "更正公告", "业绩说明会", "预告", "问询", "回复")):
        return False
    if columns and not any(
        ("一季度报告全文" in col or "三季度报告全文" in col)
        for col in columns
    ):
        return False
    return True


def select_filings(rows):
    selected = []
    selection_audit = {"annual_candidates": {}, "quarter_candidates": []}

    for year in YEARS:
        candidates = annual_candidates(rows, year)
        selection_audit["annual_candidates"][str(year)] = [
            {
                "title": row_title(row),
                "art_code": row.get("art_code"),
                "notice_date": row.get("notice_date"),
                "columns": row_columns(row),
            }
            for row in candidates
        ]
        if not candidates:
            raise RuntimeError(f"No full annual report found for {year}")
        selected.append(
            {
                "kind": "annual",
                "year": year,
                "row": candidates[0],
                "filename": f"{year - 2019:02d}_上海洗霸_{year}年年度报告"
                + ("_更正后" if correction_priority(row_title(candidates[0])) >= 3 else "")
                + ".pdf",
                "category": f"{year}年年度报告"
                + ("（更正/修订后的最新有效版本）" if correction_priority(row_title(candidates[0])) >= 3 else ""),
            }
        )

    quarters = [row for row in rows if is_full_quarter(row)]
    quarters.sort(
        key=lambda row: (
            quarter_period(row_title(row)) or (0, 0),
            parse_notice_date(row),
            correction_priority(row_title(row)),
            str(row.get("art_code") or ""),
        ),
        reverse=True,
    )
    selection_audit["quarter_candidates"] = [
        {
            "title": row_title(row),
            "period": quarter_period(row_title(row)),
            "art_code": row.get("art_code"),
            "notice_date": row.get("notice_date"),
            "columns": row_columns(row),
        }
        for row in quarters
    ]
    if not quarters:
        raise RuntimeError("No quarterly report found")
    latest_quarter = quarters[0]
    q_year, q_num = quarter_period(row_title(latest_quarter))
    q_label = "第一季度" if q_num == 1 else "第三季度"
    selected.append(
        {
            "kind": "quarter",
            "year": q_year,
            "quarter": q_num,
            "row": latest_quarter,
            "filename": f"07_上海洗霸_{q_year}年{q_label}报告_最新季报.pdf",
            "category": f"{q_year}年{q_label}报告（截至{AS_OF_DATE}最新正式季报）",
        }
    )
    return selected, selection_audit


def fetch_pdf_for_art_code(art_code):
    candidates = [
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{art_code}.pdf",
    ]
    errors = []
    for url in candidates:
        try:
            response = request_with_retry(url, expect_pdf=True)
            return response, url
        except Exception as exc:  # noqa: BLE001
            errors.append({"url": url, "error": repr(exc)})
    raise RuntimeError(f"All PDF URLs failed for {art_code}: {errors}")


def validate_and_record(item):
    row = item["row"]
    art_code = str(row.get("art_code") or "")
    if not art_code:
        raise RuntimeError(f"Missing art_code for {row_title(row)}")

    response, source_url = fetch_pdf_for_art_code(art_code)
    data = response.content
    if len(data) < 80_000:
        raise RuntimeError(f"PDF too small for {row_title(row)}: {len(data)} bytes")

    path = REPORTS / item["filename"]
    path.write_bytes(data)

    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    page_count = len(reader.pages)
    if page_count < 5:
        raise RuntimeError(f"Unexpected page count for {path.name}: {page_count}")

    doc = pymupdf.open(str(path))
    sample_indexes = sorted(set([0, min(1, doc.page_count - 1), min(3, doc.page_count - 1), doc.page_count - 1]))
    text = "".join(doc[index].get_text("text") for index in sample_indexes)
    normalized = re.sub(r"\s+", "", text)
    if COMPANY_NAME not in normalized and STOCK_CODE not in normalized and "上海洗霸科技" not in normalized:
        raise RuntimeError(f"Company identity validation failed: {path.name}")

    if item["kind"] == "annual" and str(item["year"]) not in normalized:
        first_ten = "".join(doc[index].get_text("text") for index in range(min(10, doc.page_count)))
        if str(item["year"]) not in re.sub(r"\s+", "", first_ten):
            raise RuntimeError(f"Report year validation failed: {path.name}")

    render_paths = []
    for label, index in (("first", 0), ("last", doc.page_count - 1)):
        pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        render_path = RENDERS / f"{path.stem}_{label}.png"
        pix.save(str(render_path))
        render_paths.append(render_path)
    doc.close()

    record = {
        "filename": path.name,
        "category": item["category"],
        "announcement_title": row_title(row),
        "art_code": art_code,
        "notice_date": str(row.get("notice_date") or ""),
        "announcement_page": f"https://data.eastmoney.com/notices/detail/{STOCK_CODE}/{art_code}.html",
        "source_url": source_url,
        "final_url": response.url,
        "pages": page_count,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": bool(reader.is_encrypted),
    }
    print("VERIFIED", json.dumps(record, ensure_ascii=False))
    return record, render_paths


def create_contact_sheet(render_paths):
    thumbs = []
    for image_path in render_paths:
        image = Image.open(image_path).convert("RGB")
        image.thumbnail((420, 580))
        canvas = Image.new("RGB", (440, 630), "white")
        canvas.paste(image, ((440 - image.width) // 2, 8))
        ImageDraw.Draw(canvas).text((10, 605), image_path.stem[:58], fill="black")
        thumbs.append(canvas)
        image.close()

    cols = 2
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * 440, rows * 630), "white")
    for index, canvas in enumerate(thumbs):
        sheet.paste(canvas, ((index % cols) * 440, (index // cols) * 630))
        canvas.close()
    contact_sheet = RENDERS / "contact_sheet.jpg"
    sheet.save(contact_sheet, "JPEG", quality=90)
    sheet.close()
    return contact_sheet


def main():
    rows = get_all_announcements()
    selected, selection_audit = select_filings(rows)

    print("SELECTED_FILINGS")
    for item in selected:
        print(
            json.dumps(
                {
                    "filename": item["filename"],
                    "category": item["category"],
                    "announcement_title": row_title(item["row"]),
                    "art_code": item["row"].get("art_code"),
                    "notice_date": item["row"].get("notice_date"),
                },
                ensure_ascii=False,
            )
        )

    manifest = []
    render_paths = []
    for item in selected:
        record, item_renders = validate_and_record(item)
        manifest.append(record)
        render_paths.extend(item_renders)

    create_contact_sheet(render_paths)

    readme_lines = [
        "上海洗霸（603200.SH）2020—2025年年度报告及最新季度报告资料包",
        "",
        f"核验日期：{AS_OF_DATE}",
        "范围：2020—2025年完整年度报告各1份，共6份；另收录截至核验日最新正式季度报告1份。",
        "筛选规则：排除年度报告摘要、更正公告和业绩说明会材料；如存在更正后或修订版本，优先采用最新有效全文。",
        "来源：东方财富公开上市公司公告PDF服务器及公告索引。",
        "",
        "文件清单：",
    ]
    for record in manifest:
        readme_lines.append(
            f"- {record['filename']} | {record['category']} | {record['pages']}页 | 公告日期：{record['notice_date'][:10]}"
        )
    readme_lines.extend(
        [
            "",
            "核验：已检查PDF文件头、实际页数、公司名称、报告年份、加密状态，并渲染各PDF首页和末页。",
            "压缩包已执行ZIP CRC完整性测试。",
            "文件仅供个人研究与学习使用，请遵守原始公告版权和免责声明。",
        ]
    )
    (REPORTS / "00_资料说明.txt").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    (REPORTS / "00_文件清单及校验值.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORTS / "00_筛选审计记录.json").write_text(
        json.dumps(selection_audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORTS / "00_SHA256SUMS.txt").write_text(
        "\n".join(f"{record['sha256']}  {record['filename']}" for record in manifest) + "\n",
        encoding="utf-8",
    )

    zip_path = OUT / "shanghai_xiba_2020_2025_annual_reports_and_latest_quarter.zip"
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
        "reports": manifest,
        "selected_latest_quarter": manifest[-1]["category"],
    }
    (OUT / "BUILD_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "FINAL_SUMMARY",
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "zip_filename",
                    "zip_bytes",
                    "zip_sha256",
                    "report_count",
                    "total_pages",
                    "selected_latest_quarter",
                )
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
