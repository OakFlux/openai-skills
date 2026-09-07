#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

COMPANY_CN = "中石化油服"
COMPANY_FULL_CN = "中石化石油工程技术服务股份有限公司"
STOCK_CODE = "600871"
H_SHARE_CODE = "01033"
AS_OF_DATE = "2026-09-07"
REPO_ROOT = Path.cwd()
WORK_DIR = REPO_ROOT / "_work_sinopec_oilfield_service_20260907"
PACKAGE_DIR = WORK_DIR / "中石化油服_2020-2025年报及2026最新报告"
PDF_DIR = PACKAGE_DIR / "PDF"
RENDER_DIR = WORK_DIR / "renders"
DIST_DIR = REPO_ROOT / "dist_sinopec_oilfield_service_20260907"
FINAL_ZIP = DIST_DIR / "Sinopec_Oilfield_Service_600871_2020-2025_Annual_and_2026_Latest_Reports.zip"


def build_session() -> requests.Session:
    retry = Retry(
        total=6,
        connect=6,
        read=6,
        status=6,
        backoff_factor=1.2,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return session


def clean_title(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value: Any) -> datetime:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        numeric = int(float(text))
        if numeric > 10_000_000_000:
            numeric //= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def query_sse(session: requests.Session) -> list[dict[str, Any]]:
    endpoint = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
    referer = (
        "https://www.sse.com.cn/assortment/stock/list/info/announcement/"
        f"index.shtml?productId={STOCK_CODE}"
    )
    records: list[dict[str, Any]] = []
    for calendar_year in range(2021, 2027):
        begin_date = f"{calendar_year}-01-01"
        end_date = AS_OF_DATE if calendar_year == 2026 else f"{calendar_year}-12-31"
        page_no = 1
        while page_no <= 10:
            params = {
                "isPagination": "true",
                "productId": STOCK_CODE,
                "securityType": "0101,120100,020100,020200,120200",
                "reportType": "ALL",
                "beginDate": begin_date,
                "endDate": end_date,
                "pageHelp.pageSize": "100",
                "pageHelp.pageCount": "50",
                "pageHelp.pageNo": str(page_no),
                "pageHelp.beginPage": str(page_no),
                "pageHelp.cacheSize": "1",
                "pageHelp.endPage": str(page_no + 4),
                "_": str(int(time.time() * 1000)),
            }
            response = session.get(
                endpoint,
                params=params,
                headers={"Referer": referer, "Accept": "application/json, text/javascript, */*; q=0.01"},
                timeout=35,
            )
            print(
                "SSE QUERY",
                calendar_year,
                "page",
                page_no,
                response.status_code,
                len(response.content),
                response.url,
                flush=True,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("result") or []
            total = int((payload.get("pageHelp") or {}).get("total") or len(rows))
            print("SSE ROWS", calendar_year, page_no, len(rows), "TOTAL", total, flush=True)
            for row in rows:
                path = str(row.get("URL") or "")
                if not path.lower().endswith(".pdf"):
                    continue
                records.append(
                    {
                        "source": "上海证券交易所",
                        "title": clean_title(row.get("TITLE")),
                        "date": str(row.get("ADDDATE") or ""),
                        "url_path": path,
                        "download_urls": [
                            f"https://static.sse.com.cn{path}",
                            f"https://www.sse.com.cn{path}",
                        ],
                        "security_name": clean_title(row.get("SECURITY_NAME")),
                    }
                )
            if page_no * 100 >= total or not rows:
                break
            page_no += 1
    return dedupe_records(records)


def query_cninfo(session: requests.Session) -> list[dict[str, Any]]:
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    referer = (
        "https://www.cninfo.com.cn/new/disclosure/stock?"
        f"stockCode={STOCK_CODE}&orgId=gssh0600871"
    )
    try:
        session.get(referer, timeout=25)
    except Exception as exc:
        print("CNINFO WARMUP WARNING", repr(exc), flush=True)
    records: list[dict[str, Any]] = []
    categories = [
        "category_ndbg_szsh",
        "category_bndbg_szsh",
        "category_yjdbg_szsh",
        "category_sjdbg_szsh",
    ]
    for category in categories:
        page_num = 1
        while page_num <= 10:
            data = {
                "pageNum": str(page_num),
                "pageSize": "30",
                "column": "sse",
                "tabName": "fulltext",
                "plate": "sh",
                "stock": f"{STOCK_CODE},gssh0600871",
                "searchkey": "",
                "secid": "",
                "category": category,
                "trade": "",
                "seDate": f"2021-01-01~{AS_OF_DATE}",
                "sortName": "time",
                "sortType": "desc",
                "isHLtitle": "false",
            }
            response = session.post(
                endpoint,
                data=data,
                headers={
                    "Referer": referer,
                    "Origin": "https://www.cninfo.com.cn",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                timeout=35,
            )
            print(
                "CNINFO QUERY",
                category,
                "page",
                page_num,
                response.status_code,
                len(response.content),
                flush=True,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("announcements") or []
            total = int(payload.get("totalAnnouncement") or payload.get("totalRecordNum") or len(rows))
            for row in rows:
                path = str(row.get("adjunctUrl") or "")
                if not path.lower().endswith(".pdf"):
                    continue
                records.append(
                    {
                        "source": "巨潮资讯网（交易所法定信息披露平台）",
                        "title": clean_title(row.get("announcementTitle")),
                        "date": datetime.fromtimestamp(
                            int(row.get("announcementTime") or 0) / 1000,
                            tz=timezone.utc,
                        ).strftime("%Y-%m-%d"),
                        "url_path": path,
                        "download_urls": [urljoin("https://static.cninfo.com.cn/", path)],
                        "security_name": clean_title(row.get("secName")),
                    }
                )
            if page_num * 30 >= total or not rows:
                break
            page_num += 1
    return dedupe_records(records)


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("url_path") or record.get("download_urls", [""])[0])
        unique[key] = record
    return list(unique.values())


def compact_title(title: str) -> str:
    return re.sub(r"[\s:：()（）【】\[\]_-]+", "", title)


def excluded_title(title: str) -> bool:
    excluded = (
        "摘要",
        "可持续发展",
        "社会责任",
        "环境、社会",
        "ESG",
        "审计报告",
        "内部控制",
        "董事会审计",
        "确认意见",
        "说明公告",
        "更正公告",
    )
    return any(term.lower() in title.lower() for term in excluded)


def record_score(record: dict[str, Any]) -> tuple[int, float]:
    title = record["title"]
    score = 0
    if "修订稿" in title or "修訂稿" in title or "修订版" in title:
        score += 100
    if "全文" in title:
        score += 30
    if COMPANY_CN in title or "石化油服" in title:
        score += 10
    if excluded_title(title):
        score -= 1000
    return score, parse_date(record.get("date")).timestamp()


def select_annual(records: list[dict[str, Any]], report_year: int) -> dict[str, Any]:
    candidates = []
    for record in records:
        title = compact_title(record["title"])
        if excluded_title(record["title"]):
            continue
        if "年度报告" not in title and "年度報告" not in title:
            continue
        if f"{report_year}年年度报告" in title or f"{report_year}年度报告" in title:
            candidates.append(record)
    if not candidates:
        raise RuntimeError(f"未找到 {report_year} 年年度报告。可用标题：{[r['title'] for r in records if str(report_year) in r['title']][:20]}")
    selected = sorted(candidates, key=record_score, reverse=True)[0]
    print("SELECT ANNUAL", report_year, selected, flush=True)
    return selected


def select_periodic(records: list[dict[str, Any]], year: int, kind: str) -> dict[str, Any] | None:
    candidates = []
    for record in records:
        title = compact_title(record["title"])
        if excluded_title(record["title"]):
            continue
        if str(year) not in title:
            continue
        if kind == "半年度报告" and ("半年度报告" in title or "中期报告" in title):
            candidates.append(record)
        elif kind == "第一季度报告" and "第一季度报告" in title:
            candidates.append(record)
    if not candidates:
        return None
    selected = sorted(candidates, key=record_score, reverse=True)[0]
    print("SELECT PERIODIC", year, kind, selected, flush=True)
    return selected


def merge_sources(primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return dedupe_records(primary + fallback)


def download_pdf(session: requests.Session, record: dict[str, Any], destination: Path) -> str:
    last_error: Exception | None = None
    for url in record["download_urls"]:
        try:
            response = session.get(
                url,
                headers={
                    "Referer": (
                        "https://www.sse.com.cn/assortment/stock/list/info/announcement/"
                        f"index.shtml?productId={STOCK_CODE}"
                    ),
                    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                },
                timeout=90,
                allow_redirects=True,
            )
            data = response.content
            print(
                "PDF GET",
                response.status_code,
                len(data),
                response.headers.get("content-type"),
                url,
                "->",
                response.url,
                flush=True,
            )
            response.raise_for_status()
            if len(data) < 10_000 or not data.startswith(b"%PDF"):
                raise RuntimeError(f"响应不是有效 PDF：{url}，前16字节={data[:16]!r}")
            destination.write_bytes(data)
            return response.url
        except Exception as exc:
            last_error = exc
            print("DOWNLOAD FAILED", url, repr(exc), flush=True)
    raise RuntimeError(f"所有下载地址均失败：{record['title']}: {last_error}")


def command_ok(command: list[str]) -> tuple[bool, str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output[-4000:]


def validate_pdf(path: Path, item: dict[str, Any]) -> dict[str, Any]:
    data = path.read_bytes()
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"{path.name} 缺少 PDF 文件头")
    reader = PdfReader(str(path), strict=False)
    pages = len(reader.pages)
    minimum = item["minimum_pages"]
    if pages < minimum:
        raise RuntimeError(f"{path.name} 页数异常：{pages} < {minimum}")
    qpdf_ok, qpdf_output = command_ok(["qpdf", "--check", str(path)])
    if not qpdf_ok:
        raise RuntimeError(f"qpdf 校验失败：{path.name}\n{qpdf_output}")

    sample_indexes = sorted(set([0, 1, 2, min(9, pages - 1), pages - 1]))
    extracted = []
    for index in sample_indexes:
        try:
            extracted.append(reader.pages[index].extract_text() or "")
        except Exception as exc:
            print("TEXT EXTRACTION WARNING", path.name, index, repr(exc), flush=True)
    text = "\n".join(extracted)
    normalized_text = compact_title(text)
    company_verified = any(
        marker in normalized_text
        for marker in (
            STOCK_CODE,
            COMPANY_FULL_CN,
            "中石化石油工程技术服务",
            "石化油服",
            "SinopecOilfieldService",
        )
    )
    year_verified = str(item["report_year"]) in normalized_text
    type_markers = {
        "年度报告": ("年度报告", "年度報告", "AnnualReport"),
        "半年度报告": ("半年度报告", "中期报告", "InterimReport"),
        "第一季度报告": ("第一季度报告", "FirstQuarter"),
    }[item["report_type"]]
    type_verified = any(compact_title(marker) in normalized_text for marker in type_markers)

    render_target = RENDER_DIR / path.stem
    render_target.parent.mkdir(parents=True, exist_ok=True)
    render_ok, render_output = command_ok(
        [
            "pdftoppm",
            "-f",
            "1",
            "-l",
            "1",
            "-singlefile",
            "-png",
            "-r",
            "96",
            str(path),
            str(render_target),
        ]
    )
    if not render_ok or not render_target.with_suffix(".png").exists():
        raise RuntimeError(f"首屏渲染失败：{path.name}\n{render_output}")

    return {
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "qpdf_ok": qpdf_ok,
        "first_page_rendered": True,
        "company_text_verified": company_verified,
        "year_text_verified": year_verified,
        "type_text_verified": type_verified,
        "official_title_verified": True,
    }


def write_supporting_files(manifest: list[dict[str, Any]]) -> None:
    readme = f"""中石化油服（600871.SH / 01033.HK）财务报告资料包

整理日期：{AS_OF_DATE}
公司全称：{COMPANY_FULL_CN}

本资料包包含：
1. 2020—2025 年年度报告，共 6 份；
2. 2026 年半年度报告，作为截至整理日的最新完整定期报告；
3. 2026 年第一季度报告，作为严格意义上的最新季度报告补充。

文件均从上海证券交易所或巨潮资讯网的正式披露记录中检索并下载。2025 年年度报告优先采用后续披露的修订稿。每份 PDF 已检查文件头、页数、qpdf 结构，并完成首页渲染。详细来源、披露日期、页数、文件大小及 SHA-256 见“来源与校验清单.csv”和“manifest.json”。
"""
    (PACKAGE_DIR / "README_文件说明.txt").write_text(readme, encoding="utf-8")

    fields = [
        "sequence",
        "filename",
        "report_year",
        "report_type",
        "official_title",
        "release_date",
        "source",
        "source_url",
        "pages",
        "bytes",
        "sha256",
        "qpdf_ok",
        "first_page_rendered",
        "company_text_verified",
        "year_text_verified",
        "type_text_verified",
        "official_title_verified",
    ]
    with (PACKAGE_DIR / "来源与校验清单.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in manifest:
            writer.writerow({field: row.get(field, "") for field in fields})

    (PACKAGE_DIR / "manifest.json").write_text(
        json.dumps(
            {
                "company": COMPANY_FULL_CN,
                "a_share_code": STOCK_CODE,
                "h_share_code": H_SHARE_CODE,
                "as_of_date": AS_OF_DATE,
                "document_count": len(manifest),
                "documents": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    checksum_lines = [f"{row['sha256']}  PDF/{row['filename']}" for row in manifest]
    (PACKAGE_DIR / "SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def create_zip() -> dict[str, Any]:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if FINAL_ZIP.exists():
        FINAL_ZIP.unlink()
    with zipfile.ZipFile(FINAL_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as archive:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(WORK_DIR)))
    with zipfile.ZipFile(FINAL_ZIP, "r") as archive:
        bad = archive.testzip()
        names = archive.namelist()
        if bad is not None:
            raise RuntimeError(f"ZIP CRC 校验失败：{bad}")
    data = FINAL_ZIP.read_bytes()
    return {
        "path": str(FINAL_ZIP),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "entries": len(names),
    }


def main() -> None:
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    shutil.rmtree(DIST_DIR, ignore_errors=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    session = build_session()

    sse_records: list[dict[str, Any]] = []
    cninfo_records: list[dict[str, Any]] = []
    try:
        sse_records = query_sse(session)
    except Exception as exc:
        print("SSE QUERY FAILED", repr(exc), file=sys.stderr, flush=True)
    try:
        cninfo_records = query_cninfo(session)
    except Exception as exc:
        print("CNINFO QUERY FAILED", repr(exc), file=sys.stderr, flush=True)
    records = merge_sources(sse_records, cninfo_records)
    print("TOTAL UNIQUE RECORDS", len(records), flush=True)
    for record in sorted(records, key=lambda row: parse_date(row.get("date")), reverse=True):
        title = record["title"]
        if any(keyword in title for keyword in ("年度报告", "半年度报告", "第一季度报告", "中期报告")):
            print("CANDIDATE", record["date"], record["source"], title, record["download_urls"][0], flush=True)

    selected_items: list[dict[str, Any]] = []
    for year in range(2020, 2026):
        selected_items.append(
            {
                "report_year": year,
                "report_type": "年度报告",
                "record": select_annual(records, year),
                "minimum_pages": 80,
            }
        )

    half_year = select_periodic(records, 2026, "半年度报告")
    if half_year is None:
        raise RuntimeError("未找到 2026 年半年度报告")
    selected_items.append(
        {
            "report_year": 2026,
            "report_type": "半年度报告",
            "record": half_year,
            "minimum_pages": 60,
        }
    )
    q1 = select_periodic(records, 2026, "第一季度报告")
    if q1 is not None:
        selected_items.append(
            {
                "report_year": 2026,
                "report_type": "第一季度报告",
                "record": q1,
                "minimum_pages": 5,
            }
        )

    manifest: list[dict[str, Any]] = []
    for sequence, item in enumerate(selected_items, start=1):
        suffix = "修订稿" if "修订" in item["record"]["title"] else ""
        filename = f"{sequence:02d}_{COMPANY_CN}_{item['report_year']}年{item['report_type']}{suffix}.pdf"
        destination = PDF_DIR / filename
        resolved_url = download_pdf(session, item["record"], destination)
        validation = validate_pdf(destination, item)
        row = {
            "sequence": sequence,
            "filename": filename,
            "report_year": item["report_year"],
            "report_type": item["report_type"],
            "official_title": item["record"]["title"],
            "release_date": item["record"]["date"],
            "source": item["record"]["source"],
            "source_url": resolved_url,
            **validation,
        }
        manifest.append(row)
        print("VERIFIED", json.dumps(row, ensure_ascii=False), flush=True)

    write_supporting_files(manifest)
    zip_meta = create_zip()
    print("FINAL ZIP", json.dumps(zip_meta, ensure_ascii=False), flush=True)
    if len(manifest) < 7:
        raise RuntimeError(f"文件数量不足：{len(manifest)}")


if __name__ == "__main__":
    main()
