#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
import time
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

COMPANY_CN = "信和置业有限公司"
COMPANY_TC = "信和置業有限公司"
COMPANY_EN = "Sino Land Company Limited"
STOCK_CODE = "00083"
STOCK_ID = "137"
AS_OF_DATE = "2026-09-07"

ROOT = Path.cwd()
WORK = ROOT / "_work_sino_land_20260907"
PACKAGE = WORK / "信和置业_2020-2025年报及最新财务披露_截至2026-09-07"
PDF_DIR = PACKAGE / "PDF"
RENDER_DIR = WORK / "renders"
DIST = ROOT / "dist_sino_land_20260907"
FINAL_ZIP = DIST / "Sino_Land_00083_2020-2025_Annual_and_Latest_Financial_Reports_2026-09-07.zip"

BASE = "https://www1.hkexnews.hk"
SEARCH_PAGE = BASE + "/search/titlesearch.xhtml"
SEARCH_API = BASE + "/search/titleSearchServlet.do"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/152 Safari/537.36"

YEAR_CN = {
    2020: "二零二零",
    2021: "二零二一",
    2022: "二零二二",
    2023: "二零二三",
    2024: "二零二四",
    2025: "二零二五",
    2026: "二零二六",
}


def build_session() -> requests.Session:
    retry = Retry(
        total=7,
        connect=7,
        read=7,
        status=7,
        backoff_factor=1.0,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
    })
    return session


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact(value: object) -> str:
    return re.sub(r"[^0-9A-Z\u4e00-\u9fff]+", "", normalize(value).upper())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_datetime(value: object) -> datetime:
    text = normalize(value)
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return datetime.min


def normalize_url(value: object) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).replace("\\/", "/")
    text = unquote(text).strip()
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):
        return urljoin(BASE, text)
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if "listedco/" in text:
        return urljoin(BASE + "/", text)
    return ""


def query_rows(session: requests.Session, start: str, end: str, lang: str) -> list[dict]:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/html,*/*",
        "Referer": SEARCH_PAGE + "?category=0&market=SEHK&stockId=" + STOCK_ID,
        "X-Requested-With": "XMLHttpRequest",
    }
    payload = {
        "lang": lang,
        "category": "0",
        "market": "SEHK",
        "searchType": "0",
        "documentType": "-1",
        "t1code": "-2",
        "t2Gcode": "-2",
        "t2code": "-2",
        "stockId": STOCK_ID,
        "from": start,
        "to": end,
        "MB-Daterange": "0",
        "title": "",
    }
    warmup = session.post(SEARCH_PAGE, data=payload, headers=headers, timeout=(30, 180))
    print("HKEX POST", start, end, lang, warmup.status_code, len(warmup.content), flush=True)

    params = {
        "sortDir": "0",
        "sortByOptions": "DateTime",
        "category": "0",
        "market": "SEHK",
        "stockId": STOCK_ID,
        "documentType": "-1",
        "fromDate": start,
        "toDate": end,
        "title": "",
        "searchType": "0",
        "t1code": "-2",
        "t2Gcode": "-2",
        "t2code": "-2",
        "rowRange": "9999",
        "lang": lang,
    }
    response = session.get(SEARCH_API, params=params, headers=headers, timeout=(30, 180))
    print("HKEX GET", start, end, lang, response.status_code, len(response.content), flush=True)
    response.raise_for_status()
    outer = response.json()
    rows = outer.get("result", "[]") if isinstance(outer, dict) else outer
    if isinstance(rows, str):
        rows = json.loads(rows)
    if isinstance(rows, dict):
        for key in ("data", "rows", "items", "result"):
            if isinstance(rows.get(key), list):
                rows = rows[key]
                break
    if not isinstance(rows, list):
        rows = [rows]

    result: list[dict] = []
    for raw in rows:
        title = normalize(raw.get("TITLE") or raw.get("LONG_TEXT") or "")
        url = normalize_url(raw.get("FILE_LINK") or "")
        if not url.lower().split("?")[0].endswith(".pdf"):
            continue
        date_text = normalize(raw.get("DATE_TIME") or "")
        result.append({
            "lang": lang,
            "title": title,
            "title_norm": compact(title),
            "release_text": date_text,
            "release_dt": parse_datetime(date_text),
            "file_size": normalize(raw.get("FILE_SIZE") or ""),
            "url": url,
        })
    print("ROWS", start, end, lang, len(result), flush=True)
    return result


def gather_records(session: requests.Session) -> list[dict]:
    rows: list[dict] = []
    for year in range(2020, 2027):
        start = f"{year}0101"
        end = AS_OF_DATE.replace("-", "") if year == 2026 else f"{year}1231"
        for lang in ("ZH", "EN"):
            rows.extend(query_rows(session, start, end, lang))
    unique: dict[tuple[str, str], dict] = {}
    for row in rows:
        unique[(row["lang"], row["url"])] = row
    rows = list(unique.values())
    print("TOTAL RECORDS", len(rows), flush=True)
    for row in sorted(rows, key=lambda r: r["release_dt"]):
        if any(token in row["title_norm"] for token in (
            "ANNUALREPORT", "年報", "年报", "INTERIMREPORT", "中期報告", "中期报告", "FINALRESULTS", "全年業績", "全年业绩"
        )):
            print("RELEVANT", row["lang"], row["release_text"], row["title"], row["url"], flush=True)
    return rows


def has_year(row: dict, year: int) -> bool:
    title = row["title_norm"]
    return str(year) in title or compact(YEAR_CN[year]) in title


def lang_priority(row: dict) -> int:
    if row["lang"] == "ZH":
        return 3
    if "_c.pdf" in row["url"].lower():
        return 2
    return 1


def is_annual_report(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in ("ANNUALREPORT", "年報", "年报"))
    excluded = any(marker in title for marker in (
        "INTERIM", "中期", "RESULTS", "業績", "业绩", "SUMMARY", "摘要",
        "SUPPLEMENT", "補充", "补充", "NOTIFICATION", "PUBLICATION", "LETTER",
        "通告", "通知", "函件", "展示文件", "企業年度報告書", "企业年度报告书",
        "SUSTAINABILITY", "ESG", "ENVIRONMENTAL", "CIRCULAR", "PROXY", "DIVIDEND",
    ))
    return positive and not excluded


def is_formal_interim_report(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in ("INTERIMREPORT", "中期報告", "中期报告"))
    excluded = any(marker in title for marker in (
        "RESULTS", "業績", "业绩", "SUMMARY", "摘要", "NOTIFICATION", "PUBLICATION",
        "LETTER", "通告", "通知", "函件", "CIRCULAR", "DIVIDEND",
    ))
    return positive and not excluded


def is_final_results(row: dict) -> bool:
    title = row["title_norm"]
    exact_or_clear = title == "FINALRESULTS" or any(marker in title for marker in (
        "FINALRESULTSFOR", "ANNUALRESULTS", "全年業績", "全年业绩", "末期業績", "末期业绩"
    ))
    excluded = any(marker in title for marker in (
        "CLARIFICATION", "SUPPLEMENT", "補充", "补充", "DIVIDEND", "NOTICE", "通告", "LETTER", "函件"
    ))
    return exact_or_clear and not excluded


def choose(candidates: list[dict], prefer_latest: bool = True) -> dict:
    if not candidates:
        raise RuntimeError("候选列表为空")
    def key(row: dict) -> tuple:
        date_value = row["release_dt"].timestamp() if row["release_dt"] != datetime.min else 0
        return (lang_priority(row), date_value if prefer_latest else -date_value, -len(row["title"]))
    return sorted(candidates, key=key, reverse=True)[0]


def select_documents(rows: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for year in range(2020, 2026):
        candidates = [row for row in rows if is_annual_report(row) and has_year(row, year)]
        candidates = [row for row in candidates if row["release_dt"].year in (year, year + 1)]
        print("ANNUAL CANDIDATES", year, [(x["lang"], x["release_text"], x["title"], x["url"]) for x in candidates], flush=True)
        if not candidates:
            raise RuntimeError(f"未找到信和置业 {year} 年完整年报")
        # Prefer Chinese, then the latest official full report for that report year.
        row = choose(candidates, prefer_latest=True)
        selected.append({
            "kind": "年度报告",
            "year": year,
            "filename": f"{len(selected)+1:02d}_信和置业_{year}年年度报告.pdf",
            "row": row,
            "minimum_pages": 120,
        })

    interim_candidates = [row for row in rows if is_formal_interim_report(row)]
    if not interim_candidates:
        raise RuntimeError("未找到信和置业完整中期报告")
    latest_interim_date = max(row["release_dt"] for row in interim_candidates)
    latest_interim = choose([row for row in interim_candidates if row["release_dt"] == latest_interim_date])
    selected.append({
        "kind": "中期报告",
        "year": 2026,
        "filename": f"{len(selected)+1:02d}_信和置业_2025-2026年中期报告_最新完整中期报告.pdf",
        "row": latest_interim,
        "minimum_pages": 25,
    })

    results_candidates = [
        row for row in rows
        if is_final_results(row)
        and row["release_dt"].year == 2026
        and row["release_dt"] <= datetime.strptime(AS_OF_DATE, "%Y-%m-%d")
    ]
    print("FINAL RESULTS CANDIDATES", [(x["lang"], x["release_text"], x["title"], x["url"]) for x in results_candidates], flush=True)
    if not results_candidates:
        raise RuntimeError("未找到信和置业截至整理日的 2026 财年全年业绩公告")
    latest_results_date = max(row["release_dt"] for row in results_candidates)
    latest_results = choose([row for row in results_candidates if row["release_dt"] == latest_results_date])
    selected.append({
        "kind": "全年业绩公告",
        "year": 2026,
        "filename": f"{len(selected)+1:02d}_信和置业_2026财年全年业绩公告_最新财务披露.pdf",
        "row": latest_results,
        "minimum_pages": 10,
    })

    print("FINAL SELECTED DOCUMENTS", flush=True)
    for item in selected:
        row = item["row"]
        print(item["kind"], item["year"], row["lang"], row["release_text"], row["title"], row["url"], flush=True)
    return selected


def fetch_pdf(session: requests.Session, url: str) -> tuple[bytes, str]:
    variants = [url]
    if "www1.hkexnews.hk" in url:
        variants.append(url.replace("www1.hkexnews.hk", "www.hkexnews.hk"))
    elif "www.hkexnews.hk" in url:
        variants.append(url.replace("www.hkexnews.hk", "www1.hkexnews.hk"))
    variants = list(dict.fromkeys(variants))
    errors: list[str] = []
    for attempt in range(1, 5):
        for value in variants:
            try:
                response = session.get(
                    value,
                    headers={
                        "User-Agent": UA,
                        "Referer": SEARCH_PAGE,
                        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                    },
                    timeout=(30, 900),
                    allow_redirects=True,
                )
                print("PDF GET", response.status_code, len(response.content), response.headers.get("content-type"), value, "->", response.url, flush=True)
                if response.status_code == 200 and response.content.startswith(b"%PDF-") and len(response.content) > 20_000:
                    return response.content, response.url
                errors.append(f"{value}: {response.status_code}/{len(response.content)}")
            except Exception as exc:
                errors.append(f"{value}: {exc!r}")
        time.sleep(attempt * 2)
    raise RuntimeError("PDF 下载失败：" + "; ".join(errors[-10:]))


def command_ok(command: list[str]) -> tuple[bool, str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=240)
    return result.returncode in (0, 3), ((result.stdout or "") + (result.stderr or ""))[-4000:]


def extract_probe(reader: PdfReader, pages: int) -> str:
    indexes = list(range(min(30, pages)))
    if pages > 35:
        indexes.extend(range(max(30, pages - 6), pages))
    chunks: list[str] = []
    for index in sorted(set(indexes)):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception as exc:
            print("TEXT WARNING", index, repr(exc), flush=True)
    return "\n".join(chunks)


def validate_pdf(path: Path, item: dict) -> dict:
    data = path.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"{path.name} 缺少 PDF 文件头")
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError(f"{path.name} 为加密 PDF") from exc
    pages = len(reader.pages)
    if pages < item["minimum_pages"]:
        raise RuntimeError(f"{path.name} 页数异常：{pages} < {item['minimum_pages']}")

    qpdf_ok, qpdf_output = command_ok(["qpdf", "--check", str(path)])
    if not qpdf_ok:
        raise RuntimeError(f"qpdf 校验失败：{path.name}\n{qpdf_output}")

    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    rendered_files: list[str] = []
    for label, page_no in (("first", 1), ("last", pages)):
        prefix = RENDER_DIR / f"{path.stem}_{label}"
        subprocess.run(
            ["pdftoppm", "-f", str(page_no), "-l", str(page_no), "-singlefile", "-png", "-r", "90", str(path), str(prefix)],
            check=True,
            capture_output=True,
            timeout=240,
        )
        image = Path(str(prefix) + ".png")
        if not image.exists() or image.stat().st_size < 1000:
            raise RuntimeError(f"{path.name} {label} page render failed")
        rendered_files.append(image.name)

    text = compact(extract_probe(reader, pages))
    company_ok = any(compact(marker) in text for marker in (
        COMPANY_CN, COMPANY_TC, COMPANY_EN, "信和置業", "信和置业", "SINOLAND", "00083"
    ))
    year_ok = str(item["year"]) in text or compact(YEAR_CN[item["year"]]) in text
    if item["kind"] == "年度报告":
        type_ok = any(compact(marker) in text for marker in ("ANNUALREPORT", "年報", "年报"))
    elif item["kind"] == "中期报告":
        type_ok = any(compact(marker) in text for marker in ("INTERIMREPORT", "中期報告", "中期报告"))
    else:
        type_ok = any(compact(marker) in text for marker in ("FINALRESULTS", "ANNUALRESULTS", "全年業績", "全年业绩", "末期業績", "末期业绩"))

    print("VALIDATE", path.name, pages, company_ok, year_ok, type_ok, "text_chars", len(text), flush=True)
    if len(text) > 1200:
        if not company_ok:
            raise RuntimeError(f"{path.name} 未在可提取文本中识别到信和置业名称或股份代码")
        if item["kind"] == "年度报告" and not year_ok:
            raise RuntimeError(f"{path.name} 未在可提取文本中识别到报告年度")
        if not type_ok:
            raise RuntimeError(f"{path.name} 未在可提取文本中识别到报告类型")

    return {
        "pages": pages,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "qpdf_ok": True,
        "first_last_rendered": True,
        "rendered_files": rendered_files,
        "company_text_verified": company_ok,
        "year_text_verified": year_ok,
        "type_text_verified": type_ok,
    }


def make_package(records: list[dict]) -> None:
    PACKAGE.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    csv_path = PACKAGE / "来源与校验清单.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "序号", "文件名", "报告年度", "报告类型", "港交所原公告标题", "披露时间", "语言",
            "页数", "文件大小_字节", "SHA256", "PDF结构校验", "首尾页渲染校验",
            "公司名称文本校验", "年度文本校验", "类型文本校验", "港交所PDF地址",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({
                "序号": record["sequence"],
                "文件名": record["filename"],
                "报告年度": record["year"],
                "报告类型": record["kind"],
                "港交所原公告标题": record["official_title"],
                "披露时间": record["release_time"],
                "语言": record["language"],
                "页数": record["pages"],
                "文件大小_字节": record["bytes"],
                "SHA256": record["sha256"],
                "PDF结构校验": record["qpdf_ok"],
                "首尾页渲染校验": record["first_last_rendered"],
                "公司名称文本校验": record["company_text_verified"],
                "年度文本校验": record["year_text_verified"],
                "类型文本校验": record["type_text_verified"],
                "港交所PDF地址": record["resolved_url"],
            })

    readme = "\n".join([
        f"{COMPANY_CN}（{COMPANY_EN}）",
        f"香港交易所股份代号：{STOCK_CODE}",
        f"整理日期：{AS_OF_DATE}",
        "",
        "一、收录内容",
        "1. 2020、2021、2022、2023、2024、2025年度报告全文，共6份。信和置业财政年度于每年6月30日结束。",
        "2. 最新完整定期报告：2025-2026中期报告，覆盖截至2025年12月31日止六个月。",
        "3. 最新财务披露：2026财政年度全年业绩公告，覆盖截至2026年6月30日止年度。",
        "4. 截至2026年9月7日，2026年度完整年报尚未在港交所发布，因此以全年业绩公告补充最新数据。",
        "5. 信和置业按港股规则主要发布年度及中期报告，不发布A股口径的第一季度、第三季度报告。",
        "",
        "二、来源与校验",
        "所有PDF均来自香港交易所披露易官方网站。每份文件均检查PDF文件头、实际页数、qpdf结构、首尾页渲染，并生成SHA-256。",
        "详细来源、披露时间、页数及校验结果见《来源与校验清单.csv》。",
        "",
        "三、文件夹结构",
        "PDF/：正式报告PDF",
        "来源与校验清单.csv：逐份来源与校验信息",
        "SHA256SUMS.txt：逐份文件哈希",
        "manifest.json：机器可读清单",
        "",
    ])
    (PACKAGE / "README_说明.txt").write_text(readme, encoding="utf-8")

    manifest = {
        "company_cn": COMPANY_CN,
        "company_tc": COMPANY_TC,
        "company_en": COMPANY_EN,
        "stock_code": STOCK_CODE,
        "hkex_stock_id": STOCK_ID,
        "as_of_date": AS_OF_DATE,
        "document_count": len(records),
        "documents": records,
    }
    (PACKAGE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    hash_lines = [f"{record['sha256']}  PDF/{record['filename']}" for record in records]
    (PACKAGE / "SHA256SUMS.txt").write_text("\n".join(hash_lines) + "\n", encoding="utf-8")

    if FINAL_ZIP.exists():
        FINAL_ZIP.unlink()
    with zipfile.ZipFile(FINAL_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(PACKAGE.name / path.relative_to(PACKAGE)))

    with zipfile.ZipFile(FINAL_ZIP, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC 校验失败：{bad}")
        names = archive.namelist()
        pdf_names = [name for name in names if name.lower().endswith(".pdf")]
        if len(pdf_names) != len(records):
            raise RuntimeError(f"ZIP 内 PDF 数量异常：{len(pdf_names)} != {len(records)}")

    zip_hash = sha256(FINAL_ZIP)
    (DIST / "PACKAGE_SHA256.txt").write_text(f"{zip_hash}  {FINAL_ZIP.name}\n", encoding="utf-8")
    print("FINAL ZIP", json.dumps({
        "path": str(FINAL_ZIP),
        "bytes": FINAL_ZIP.stat().st_size,
        "sha256": zip_hash,
        "entries": len(names),
        "pdf_count": len(pdf_names),
    }, ensure_ascii=False), flush=True)


def main() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    if DIST.exists():
        shutil.rmtree(DIST)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    session = build_session()
    rows = gather_records(session)
    selected = select_documents(rows)

    records: list[dict] = []
    for sequence, item in enumerate(selected, 1):
        row = item["row"]
        destination = PDF_DIR / item["filename"]
        content, resolved_url = fetch_pdf(session, row["url"])
        destination.write_bytes(content)
        metadata = validate_pdf(destination, item)
        record = {
            "sequence": sequence,
            "filename": item["filename"],
            "year": item["year"],
            "kind": item["kind"],
            "official_title": row["title"],
            "release_time": row["release_text"],
            "language": "繁体中文" if row["lang"] == "ZH" else "英文",
            "requested_url": row["url"],
            "resolved_url": resolved_url,
            **metadata,
        }
        records.append(record)
        print("VERIFIED", json.dumps(record, ensure_ascii=False), flush=True)

    make_package(records)


if __name__ == "__main__":
    main()
