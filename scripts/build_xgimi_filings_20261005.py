#!/usr/bin/env python3
import hashlib
import json
import re
import time
import zipfile
from pathlib import Path

import requests
import pymupdf
from PIL import Image, ImageDraw
from pypdf import PdfReader

STOCK_CODE = "688696"
COMPANY_NAME = "极米科技"
AS_OF_DATE = "2026-10-05"
YEARS = list(range(2020, 2026))
OUT = Path("output")
REPORTS = OUT / "reports"
RENDERS = OUT / "renders"
REPORTS.mkdir(parents=True, exist_ok=True)
RENDERS.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Referer": "https://data.eastmoney.com/notices/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})


def get_json(url, *, params=None, attempts=5):
    last = None
    for attempt in range(attempts):
        try:
            response = session.get(url, params=params, timeout=120)
            print("FETCH_JSON", response.status_code, len(response.content), response.url)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            time.sleep(min(10, 2 ** attempt))
    raise RuntimeError(f"Failed JSON request {url}: {last}")


def get_pdf(url, attempts=5):
    last = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=240, allow_redirects=True)
            print("FETCH_PDF", response.status_code, len(response.content), response.url, response.headers.get("content-type"))
            if response.status_code == 200 and response.content.startswith(b"%PDF-"):
                return response
            last = RuntimeError(f"HTTP {response.status_code}; head={response.content[:40]!r}")
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2 ** attempt))
    raise RuntimeError(f"Failed PDF request {url}: {last}")


def column_names(row):
    return " ".join(str(c.get("column_name") or "") for c in (row.get("columns") or []))


def title_of(row):
    return str(row.get("title") or row.get("title_ch") or "")


def date_of(row):
    return str(row.get("notice_date") or row.get("display_time") or "")[:10]


def revision_score(title):
    score = 0
    if any(term in title for term in ("更正后", "修订版", "修订稿", "更新后")):
        score += 100
    if any(term in title for term in ("已取消", "取消")):
        score -= 500
    return score


def query_announcements():
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
        obj = get_json(api, params=params)
        data = obj.get("data") or {}
        page_rows = data.get("list") or []
        total_hits = int(data.get("total_hits") or 0)
        print("ANNOUNCEMENT_PAGE", page, "ROWS", len(page_rows), "TOTAL", total_hits)
        rows.extend(page_rows)
        if not page_rows or len(rows) >= total_hits:
            break
    if not rows:
        raise RuntimeError("No announcements returned")
    return rows


def select_annual(rows, year):
    candidates = []
    needle = f"{year}年年度报告"
    for row in rows:
        title = title_of(row)
        cols = column_names(row)
        if needle not in title:
            continue
        if "摘要" in title:
            continue
        if "年度报告全文" not in cols and not any(term in title for term in ("更正后", "修订版", "修订稿")):
            continue
        if any(term in title for term in ("更正公告", "说明会", "业绩快报", "业绩预告", "问询", "回复", "提示性公告")) and not any(term in title for term in ("更正后", "修订版", "修订稿")):
            continue
        candidates.append(row)
    if not candidates:
        raise RuntimeError(f"No full annual report found for {year}")
    candidates.sort(key=lambda r: (revision_score(title_of(r)), date_of(r), str(r.get("art_code") or "")), reverse=True)
    return candidates[0], candidates


def select_prospectus(rows):
    candidates = []
    excluded = ("申报稿", "上会稿", "注册稿", "招股意向书", "摘要", "问询", "回复", "审核", "更正公告")
    for row in rows:
        title = title_of(row)
        cols = column_names(row)
        if "招股说明书" not in title:
            continue
        if any(term in title for term in excluded):
            continue
        if "招股说明书" not in cols and "首发" not in cols:
            # Final prospectus rows normally have a prospectus-related column; allow plain final titles only.
            if not any(term in title for term in ("首次公开发行", "科创板上市", "上市招股说明书")):
                continue
        candidates.append(row)
    if not candidates:
        raise RuntimeError("No final prospectus found")
    candidates.sort(key=lambda r: (date_of(r), str(r.get("art_code") or "")), reverse=True)
    return candidates[0], candidates


def select_latest_quarter(rows):
    candidates = []
    for row in rows:
        title = title_of(row)
        cols = column_names(row)
        is_quarter_title = any(term in title for term in ("第一季度报告", "一季度报告", "第三季度报告", "三季度报告"))
        is_quarter_column = any(term in cols for term in ("一季度报告全文", "第一季度报告全文", "三季度报告全文", "第三季度报告全文"))
        if not (is_quarter_title and is_quarter_column):
            continue
        if "摘要" in title or any(term in title for term in ("更正公告", "提示性公告")):
            continue
        candidates.append(row)
    if not candidates:
        raise RuntimeError("No formal quarterly report found")
    candidates.sort(key=lambda r: (date_of(r), str(r.get("art_code") or "")), reverse=True)
    return candidates[0], candidates


def safe_name(text):
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", "", text)
    return text


def build_selection(rows):
    selected = []
    audit = {"annual_candidates": {}, "prospectus_candidates": [], "quarter_candidates": []}
    for idx, year in enumerate(YEARS, start=1):
        row, candidates = select_annual(rows, year)
        audit["annual_candidates"][str(year)] = [
            {"title": title_of(r), "art_code": r.get("art_code"), "date": date_of(r), "columns": column_names(r)}
            for r in candidates
        ]
        title = title_of(row)
        suffix = "_更正后" if any(term in title for term in ("更正后", "修订版", "修订稿", "更新后")) else ""
        selected.append({
            "filename": f"{idx:02d}_极米科技_{year}年年度报告{suffix}.pdf",
            "category": f"{year}年年度报告" + ("（更正/修订后的最新有效版本）" if suffix else ""),
            "row": row,
            "expected_terms": [COMPANY_NAME, str(year), "年度报告"],
        })

    prospectus, prospectus_candidates = select_prospectus(rows)
    audit["prospectus_candidates"] = [
        {"title": title_of(r), "art_code": r.get("art_code"), "date": date_of(r), "columns": column_names(r)}
        for r in prospectus_candidates
    ]
    selected.append({
        "filename": f"{len(selected)+1:02d}_极米科技_首次公开发行股票并在科创板上市招股说明书_最终版.pdf",
        "category": "首次公开发行股票并在科创板上市招股说明书（最终发行版）",
        "row": prospectus,
        "expected_terms": [COMPANY_NAME, "招股说明书"],
    })

    quarter, quarter_candidates = select_latest_quarter(rows)
    audit["quarter_candidates"] = [
        {"title": title_of(r), "art_code": r.get("art_code"), "date": date_of(r), "columns": column_names(r)}
        for r in quarter_candidates
    ]
    qtitle = title_of(quarter)
    qlabel_match = re.search(r"(20\d{2}年(?:第一季度|一季度|第三季度|三季度)报告)", qtitle)
    qlabel = qlabel_match.group(1) if qlabel_match else qtitle.split(":")[-1].split("：")[-1]
    selected.append({
        "filename": f"{len(selected)+1:02d}_极米科技_{safe_name(qlabel)}_最新季报.pdf",
        "category": f"{qlabel}（截至{AS_OF_DATE}最新正式季报）",
        "row": quarter,
        "expected_terms": [COMPANY_NAME, qlabel[:4], "季度报告"],
    })
    return selected, audit


def download_one(item):
    row = item["row"]
    art_code = str(row.get("art_code") or "")
    if not art_code:
        raise RuntimeError(f"Missing art_code for {title_of(row)}")
    urls = [
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}.pdf",
    ]
    response = None
    used_url = None
    errors = []
    for url in urls:
        try:
            response = get_pdf(url)
            used_url = url
            break
        except Exception as exc:
            errors.append({"url": url, "error": repr(exc)})
    if response is None:
        raise RuntimeError(f"All PDF URLs failed for {title_of(row)}: {errors}")

    data = response.content
    path = REPORTS / item["filename"]
    path.write_bytes(data)
    reader = PdfReader(str(path))
    encrypted = reader.is_encrypted
    if encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = len(reader.pages)
    if pages < 5:
        raise RuntimeError(f"Unexpectedly short PDF: {path.name}, {pages} pages")

    doc = pymupdf.open(str(path))
    sample_indices = sorted(set([0, min(1, doc.page_count - 1), min(2, doc.page_count - 1), min(5, doc.page_count - 1), min(10, doc.page_count - 1)]))
    text = "".join(doc[i].get_text("text") for i in sample_indices)
    normalized = re.sub(r"\s+", "", text)
    if COMPANY_NAME not in normalized and STOCK_CODE not in normalized:
        raise RuntimeError(f"Company identity check failed: {path.name}")
    for term in item["expected_terms"]:
        if term and term not in normalized:
            # Some cover pages use abbreviated wording; annual/prospectus identity and page count remain authoritative.
            print("TERM_WARNING", path.name, term)

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
        "announcement_title": title_of(row),
        "art_code": art_code,
        "notice_date": date_of(row),
        "announcement_page": f"https://data.eastmoney.com/notices/detail/{STOCK_CODE}/{art_code}.html",
        "source_url": used_url,
        "final_url": response.url,
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
    }
    print("VERIFIED", json.dumps(record, ensure_ascii=False))
    return record, render_paths


def make_contact_sheet(render_paths):
    thumbs = []
    for image_path in render_paths:
        image = Image.open(image_path).convert("RGB")
        image.thumbnail((420, 580))
        canvas = Image.new("RGB", (440, 630), "white")
        canvas.paste(image, ((440 - image.width) // 2, 8))
        ImageDraw.Draw(canvas).text((10, 605), image_path.stem[:62], fill="black")
        thumbs.append(canvas)
        image.close()
    cols = 2
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 440, rows * 630), "white")
    for idx, canvas in enumerate(thumbs):
        sheet.paste(canvas, ((idx % cols) * 440, (idx // cols) * 630))
        canvas.close()
    contact = RENDERS / "contact_sheet.jpg"
    sheet.save(contact, "JPEG", quality=90)
    sheet.close()
    return contact


def main():
    rows = query_announcements()
    selected, audit = build_selection(rows)
    print("SELECTED_FILINGS")
    for item in selected:
        print(json.dumps({
            "filename": item["filename"],
            "category": item["category"],
            "title": title_of(item["row"]),
            "art_code": item["row"].get("art_code"),
            "date": date_of(item["row"]),
            "columns": column_names(item["row"]),
        }, ensure_ascii=False))

    manifest = []
    all_renders = []
    for item in selected:
        record, renders = download_one(item)
        manifest.append(record)
        all_renders.extend(renders)
    make_contact_sheet(all_renders)

    readme = [
        "极米科技（688696.SH）年报、招股说明书及最新季报资料包",
        "",
        f"检索截止日期：{AS_OF_DATE}",
        "本包收录2020-2025年完整年度报告、最终发行版招股说明书，以及公告库中截至检索日最新的正式季度报告。",
        "如同一年度存在后续更正/修订全文，优先采用最新有效版本；未收录年报摘要、招股说明书申报稿/上会稿/注册稿或更正公告。",
        "",
        "文件清单：",
    ]
    for record in manifest:
        readme.append(f"- {record['filename']} | {record['pages']}页 | 公告日 {record['notice_date']}")
    readme += [
        "",
        "核验：已检查PDF文件头、实际页数、公司名称/证券代码、加密状态，并渲染每份文件首页与末页。",
        "压缩包已执行ZIP CRC完整性测试。",
        "文件仅供个人研究与学习使用，请遵守原始文件版权与免责声明。",
    ]
    (REPORTS / "00_资料说明.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")
    (REPORTS / "00_文件清单及校验值.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / "00_筛选审计记录.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / "00_SHA256SUMS.txt").write_text(
        "\n".join(f"{r['sha256']}  {r['filename']}" for r in manifest) + "\n", encoding="utf-8"
    )

    zip_path = OUT / "xgimi_all_annual_reports_prospectus_latest_quarter.zip"
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
        "total_pages": sum(r["pages"] for r in manifest),
        "reports": manifest,
    }
    (OUT / "BUILD_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FINAL_SUMMARY", json.dumps({k: summary[k] for k in ("zip_filename", "zip_bytes", "zip_sha256", "report_count", "total_pages")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
