#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import time
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests
from pypdf import PdfReader

CURRENT_DATE = "2026-09-06"
CODE = "01672"
COMPANY_CN = "歌礼制药有限公司"
COMPANY_TRAD = "歌禮製藥有限公司"
COMPANY_EN = "Ascletis Pharma Inc."
BASE = "https://www1.hkexnews.hk"
SEARCH_PAGE = BASE + "/search/titlesearch.xhtml"
SEARCH_API = BASE + "/search/titleSearchServlet.do"
PACKAGE_DIR = Path("歌礼制药_2020-2025年报及最新中期财务披露_截至2026-09-06")
WORK = Path("_work_ascletis_financial")
PREVIEW = WORK / "preview"
ZIP_CN = Path("歌礼制药_2020-2025年报及最新季报.zip")
ZIP_EN = Path("Ascletis_Financial_Reports_2020_2025.zip")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"

PACKAGE_DIR.mkdir(exist_ok=True)
PREVIEW.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
})

YEAR_CN = {
    2020: ("2020", "二零二零", "二〇二〇"),
    2021: ("2021", "二零二一", "二〇二一"),
    2022: ("2022", "二零二二", "二〇二二"),
    2023: ("2023", "二零二三", "二〇二三"),
    2024: ("2024", "二零二四", "二〇二四"),
    2025: ("2025", "二零二五", "二〇二五"),
    2026: ("2026", "二零二六", "二〇二六"),
}


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact(value: object) -> str:
    return re.sub(r"\s+", "", normalize(value)).upper()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_release(value: object) -> dt.datetime:
    text = normalize(value)
    patterns = [
        r"(\d{2})/(\d{2})/(20\d{2})\s*(\d{2}):(\d{2})",
        r"(20\d{2})[-/](\d{2})[-/](\d{2})\s*(\d{2}):(\d{2})",
        r"(\d{2})/(\d{2})/(20\d{2})",
        r"(20\d{2})[-/](\d{2})[-/](\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        p = list(map(int, m.groups()))
        try:
            if len(p) == 5 and len(m.group(1)) == 2:
                day, month, year, hour, minute = p
            elif len(p) == 5:
                year, month, day, hour, minute = p
            elif len(p) == 3 and len(m.group(1)) == 2:
                day, month, year = p
                hour = minute = 0
            else:
                year, month, day = p
                hour = minute = 0
            return dt.datetime(year, month, day, hour, minute)
        except ValueError:
            pass
    return dt.datetime.min


def normalize_url(value: object) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).replace("\\/", "/")
    value = unquote(value).strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return urljoin(BASE, value)
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if "listedco/" in value:
        return urljoin(BASE + "/", value)
    return ""


def load_stock_id() -> tuple[str, dict]:
    urls = [
        BASE + "/ncms/script/eds/activestock_sehk_e.json",
        BASE + "/ncms/script/eds/activestock_sehk_c.json",
        BASE + "/ncms/script/eds/tierone_e.json",
        BASE + "/ncms/script/eds/tierone_c.json",
    ]
    matches = []
    for url in urls:
        try:
            r = session.get(url, params={"_": int(time.time() * 1000)}, timeout=(30, 120))
            print("STOCK LIST", r.status_code, len(r.content), r.url, flush=True)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = data.get("data") or data.get("stocks") or data.get("result") or []
            for item in data if isinstance(data, list) else []:
                if str(item.get("c") or "").zfill(5) == CODE:
                    matches.append(item)
                    print("STOCK MATCH", json.dumps(item, ensure_ascii=False), flush=True)
        except Exception as exc:
            print("STOCK LIST WARNING", url, repr(exc), flush=True)
    if not matches:
        raise RuntimeError(f"Unable to resolve HKEX stockId for {CODE}")
    chosen = next((x for x in matches if x.get("i")), matches[0])
    return str(chosen.get("i")), chosen


def query_rows(stock_id: str, start: str, end: str, lang: str) -> list[dict]:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/html,*/*",
        "Referer": SEARCH_PAGE + ("?lang=zh" if lang == "ZH" else "?lang=en"),
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
        "stockId": stock_id,
        "from": start,
        "to": end,
        "MB-Daterange": "0",
        "title": "",
    }
    p = session.post(SEARCH_PAGE, data=payload, headers=headers, timeout=(30, 180))
    print("HKEX POST", start, end, lang, p.status_code, len(p.content), flush=True)
    p.raise_for_status()
    params = {
        "sortDir": "0",
        "sortByOptions": "DateTime",
        "category": "0",
        "market": "SEHK",
        "stockId": stock_id,
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
    r = session.get(SEARCH_API, params=params, headers=headers, timeout=(30, 180))
    print("HKEX GET", start, end, lang, r.status_code, len(r.content), flush=True)
    r.raise_for_status()
    outer = r.json()
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
    result = []
    for raw in rows:
        title = normalize(raw.get("TITLE") or raw.get("LONG_TEXT") or "")
        long_text = normalize(raw.get("LONG_TEXT") or "")
        url = normalize_url(raw.get("FILE_LINK") or "")
        if not url.lower().split("?")[0].endswith(".pdf"):
            continue
        release_text = normalize(raw.get("DATE_TIME") or "")
        result.append({
            "lang": lang,
            "title": title,
            "long_text": long_text,
            "title_norm": compact(title + " " + long_text),
            "release_text": release_text,
            "release_dt": parse_release(release_text),
            "url": url,
            "file_size": normalize(raw.get("FILE_SIZE") or ""),
        })
    print("ROWS", start, end, lang, len(result), flush=True)
    return result


def contains_year(row: dict, year: int) -> bool:
    hay = normalize(row["title"] + " " + row.get("long_text", ""))
    hay_compact = compact(hay)
    return any(marker in hay or compact(marker) in hay_compact for marker in YEAR_CN[year])


def is_annual(row: dict) -> bool:
    t = row["title_norm"]
    positive = any(x in t for x in ("ANNUALREPORT", "年報", "年报", "年度報告", "年度报告"))
    excluded = any(x in t for x in (
        "INTERIM", "中期", "RESULTS", "業績", "业绩", "SUMMARY", "摘要",
        "ENVIRONMENTAL", "SUSTAINABILITY", "ESG", "環境", "环境", "可持續", "可持续",
        "NOTICE", "CIRCULAR", "PROXY", "通告", "通知", "股東週年", "股东周年",
    ))
    return positive and not excluded


def is_interim_report(row: dict) -> bool:
    t = row["title_norm"]
    positive = any(x in t for x in ("INTERIMREPORT", "中期報告", "中期报告"))
    excluded = any(x in t for x in ("RESULTS", "業績", "业绩", "NOTICE", "CIRCULAR", "通告", "通知"))
    return positive and not excluded


def is_interim_results(row: dict) -> bool:
    t = row["title_norm"]
    positive = any(x in t for x in ("INTERIMRESULTS", "中期業績", "中期业绩", "HALFYEARRESULTS"))
    excluded = any(x in t for x in ("CLARIFICATION", "澄清", "SUPPLEMENTAL", "補充", "补充"))
    return positive and not excluded


def language_score(row: dict) -> int:
    if row["lang"] == "ZH":
        return 3
    if "_c.pdf" in row["url"].lower():
        return 2
    return 1


def choose(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    return max(candidates, key=lambda x: (language_score(x), x["release_dt"], len(x["title"])))


def fetch_pdf(url: str) -> tuple[bytes, str]:
    variants = [url]
    if "www1.hkexnews.hk" in url:
        variants.append(url.replace("www1.hkexnews.hk", "www.hkexnews.hk"))
    elif "www.hkexnews.hk" in url:
        variants.append(url.replace("www.hkexnews.hk", "www1.hkexnews.hk"))
    variants = list(dict.fromkeys(variants))
    errors = []
    for attempt in range(1, 4):
        for value in variants:
            for referer in (SEARCH_PAGE, BASE + "/", "https://www.ascletis.com/"):
                try:
                    r = session.get(
                        value,
                        headers={
                            "User-Agent": UA,
                            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                            "Referer": referer,
                        },
                        timeout=(30, 900),
                        allow_redirects=True,
                    )
                    print("PDF GET", r.status_code, len(r.content), r.headers.get("content-type"), value, flush=True)
                    if r.status_code == 200 and r.content.startswith(b"%PDF-"):
                        return r.content, r.url
                    errors.append(f"{value}: {r.status_code}/{len(r.content)}")
                except Exception as exc:
                    errors.append(f"{value}: {exc!r}")
        time.sleep(2 * attempt)
    raise RuntimeError("PDF download failed: " + "; ".join(errors[-12:]))


def extract_probe(path: Path, pages: int) -> str:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            pass
    indexes = list(range(min(45, pages)))
    if pages > 55:
        indexes.extend(range(max(45, pages - 8), pages))
    chunks = []
    for idx in sorted(set(indexes)):
        try:
            chunks.append(reader.pages[idx].extract_text() or "")
        except Exception as exc:
            print("TEXT WARNING", path.name, idx, repr(exc), flush=True)
    text = "\n".join(chunks)
    if len(compact(text)) < 500:
        proc = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(min(80, pages)), str(path), "-"],
            capture_output=True,
            check=False,
        )
        text += "\n" + proc.stdout.decode("utf-8", errors="ignore")
    return compact(text)


def validate_pdf(path: Path, kind: str, year: int, minimum_pages: int) -> dict:
    with path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise RuntimeError("PDF signature missing")
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError("Encrypted PDF") from exc
    pages = len(reader.pages)
    if pages < minimum_pages:
        raise RuntimeError(f"Only {pages} pages")
    qpdf = subprocess.run(["qpdf", "--check", str(path)], capture_output=True, text=True)
    if qpdf.returncode not in (0, 3):
        raise RuntimeError("qpdf validation failed: " + qpdf.stderr[-500:])
    for label, page_no in (("first", 1), ("last", pages)):
        prefix = PREVIEW / f"{path.stem}_{label}"
        subprocess.run(
            ["pdftoppm", "-f", str(page_no), "-l", str(page_no), "-singlefile", "-png", "-r", "72", str(path), str(prefix)],
            check=True,
            capture_output=True,
        )
        image = Path(str(prefix) + ".png")
        if not image.exists() or image.stat().st_size < 1000:
            raise RuntimeError(f"Failed to render {label} page")
    text = extract_probe(path, pages)
    company_ok = any(compact(x) in text for x in (
        COMPANY_CN, COMPANY_TRAD, COMPANY_EN, "ASCLETIS PHARMA", "歌礼制药", "歌禮製藥", CODE,
    ))
    year_ok = any(compact(x) in text for x in YEAR_CN[year])
    if kind == "年度报告":
        type_ok = any(compact(x) in text for x in ("ANNUAL REPORT", "年報", "年报", "年度報告", "年度报告"))
    elif kind == "中期报告":
        type_ok = any(compact(x) in text for x in ("INTERIM REPORT", "中期報告", "中期报告"))
    else:
        type_ok = any(compact(x) in text for x in ("INTERIM RESULTS", "中期業績", "中期业绩", "SIX MONTHS ENDED"))
    print("VALIDATE", path.name, pages, company_ok, year_ok, type_ok, flush=True)
    if len(text) >= 500 and not company_ok:
        raise RuntimeError("Company identity not found in extracted text")
    if len(text) >= 500 and not type_ok:
        raise RuntimeError("Report type not found in extracted text")
    return {
        "pages": pages,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "company_verified": company_ok,
        "year_verified": year_ok,
        "type_verified": type_ok,
    }


def main() -> None:
    stock_id, stock_info = load_stock_id()
    print("USING STOCK ID", stock_id, json.dumps(stock_info, ensure_ascii=False), flush=True)

    rows: list[dict] = []
    for year in range(2021, 2027):
        end = "20260906" if year == 2026 else f"{year}1231"
        for lang in ("ZH", "EN"):
            rows.extend(query_rows(stock_id, f"{year}0101", end, lang))

    dedup: dict[tuple[str, str], dict] = {}
    for row in rows:
        dedup[(row["lang"], row["url"])] = row
    rows = list(dedup.values())
    print("TOTAL PDF ROWS", len(rows), flush=True)
    for row in rows:
        if is_annual(row) or is_interim_report(row) or is_interim_results(row):
            print("RELEVANT", row["lang"], row["release_text"], row["title"], row["url"], flush=True)

    selected = []
    for report_year in range(2020, 2026):
        candidates = [r for r in rows if is_annual(r) and contains_year(r, report_year)]
        if not candidates:
            candidates = [
                r for r in rows
                if is_annual(r)
                and r["release_dt"].year == report_year + 1
                and 3 <= r["release_dt"].month <= 6
            ]
        print("ANNUAL CANDIDATES", report_year, [(x["lang"], x["release_text"], x["title"], x["url"]) for x in candidates], flush=True)
        best = choose(candidates)
        if best is None:
            raise RuntimeError(f"No complete annual report found for {report_year}")
        selected.append({
            "kind": "年度报告",
            "year": report_year,
            "row": best,
            "filename": f"{len(selected)+1:02d}_歌礼制药_{report_year}年年度报告.pdf",
            "min_pages": 80,
        })

    formal_2026 = [r for r in rows if is_interim_report(r) and contains_year(r, 2026)]
    if formal_2026:
        latest = choose(formal_2026)
        selected.append({
            "kind": "中期报告",
            "year": 2026,
            "row": latest,
            "filename": f"{len(selected)+1:02d}_歌礼制药_2026年中期报告_最新定期报告.pdf",
            "min_pages": 25,
        })
    else:
        complete_interims = sorted(
            [r for r in rows if is_interim_report(r)],
            key=lambda r: (r["release_dt"], language_score(r)),
            reverse=True,
        )
        if not complete_interims:
            raise RuntimeError("No complete interim report found")
        latest_complete_year = next(
            (y for y in range(2026, 2019, -1) if contains_year(complete_interims[0], y)),
            complete_interims[0]["release_dt"].year,
        )
        selected.append({
            "kind": "中期报告",
            "year": latest_complete_year,
            "row": complete_interims[0],
            "filename": f"{len(selected)+1:02d}_歌礼制药_{latest_complete_year}年中期报告_最新完整中期报告.pdf",
            "min_pages": 25,
        })
        results_2026 = [r for r in rows if is_interim_results(r) and contains_year(r, 2026)]
        if not results_2026:
            raise RuntimeError("No 2026 interim results announcement found")
        latest_results = choose(results_2026)
        selected.append({
            "kind": "中期业绩公告",
            "year": 2026,
            "row": latest_results,
            "filename": f"{len(selected)+1:02d}_歌礼制药_2026年中期业绩公告_最新财务披露.pdf",
            "min_pages": 8,
        })

    print("SELECTED", flush=True)
    for item in selected:
        print(item["kind"], item["year"], item["row"]["release_text"], item["row"]["title"], item["row"]["url"], flush=True)

    records = []
    for index, item in enumerate(selected, 1):
        data, resolved_url = fetch_pdf(item["row"]["url"])
        temp = WORK / f"{index:02d}.pdf"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(data)
        meta = validate_pdf(temp, item["kind"], item["year"], item["min_pages"])
        destination = PACKAGE_DIR / item["filename"]
        shutil.copy2(temp, destination)
        record = {
            "sequence": index,
            "filename": item["filename"],
            "report_year": item["year"],
            "report_type": item["kind"],
            "original_title": item["row"]["title"],
            "release_time": item["row"]["release_text"],
            "language": "繁体中文" if item["row"]["lang"] == "ZH" else "英文",
            "requested_url": item["row"]["url"],
            "resolved_url": resolved_url,
            **meta,
        }
        records.append(record)
        print("VERIFIED", json.dumps(record, ensure_ascii=False), flush=True)

    if len(records) not in (7, 8):
        raise RuntimeError(f"Unexpected report count: {len(records)}")
    if len({r["sha256"] for r in records}) != len(records):
        raise RuntimeError("Duplicate PDF hash detected")

    with (PACKAGE_DIR / "来源与校验清单.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = [
            "序号", "文件名", "报告年度", "文件类型", "港交所原公告标题", "披露时间", "语言",
            "页数", "文件大小_字节", "SHA256", "公司名称校验", "年度校验", "报告类型校验",
            "港交所PDF地址", "最终下载地址",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "序号": r["sequence"], "文件名": r["filename"], "报告年度": r["report_year"],
                "文件类型": r["report_type"], "港交所原公告标题": r["original_title"],
                "披露时间": r["release_time"], "语言": r["language"], "页数": r["pages"],
                "文件大小_字节": r["bytes"], "SHA256": r["sha256"],
                "公司名称校验": r["company_verified"], "年度校验": r["year_verified"],
                "报告类型校验": r["type_verified"], "港交所PDF地址": r["requested_url"],
                "最终下载地址": r["resolved_url"],
            })

    has_formal_2026 = any(r["report_year"] == 2026 and r["report_type"] == "中期报告" for r in records)
    lines = [
        "歌礼制药有限公司（Ascletis Pharma Inc.）",
        "香港交易所股份代码：01672",
        f"整理日期：{CURRENT_DATE}",
        "",
        "一、收录口径",
        "1. 收录2020、2021、2022、2023、2024及2025年度报告全文，共6份。",
        "2. 港股主板公司通常不按A股口径发布独立第一季度及第三季度报告，因此以最新中期定期报告或中期业绩公告对应用户所称的‘最新季报’。",
    ]
    if has_formal_2026:
        lines.append("3. 截至整理日，2026年完整中期报告已经发布，本包直接收录该报告作为最新定期财务报告。")
    else:
        lines.append("3. 截至整理日，尚未检索到2026年完整中期报告；本包同时收录最新完整中期报告及2026年中期业绩公告，以覆盖最新财务信息。")
    lines += ["", "二、文件清单"]
    for r in records:
        lines.append(f"{r['sequence']}. {r['filename']}｜{r['language']}｜{r['pages']}页｜SHA-256：{r['sha256']}")
    lines += [
        "", "三、校验说明",
        "逐份检查PDF文件头、加密状态、实际页数、公司名称、报告年度、报告类型和qpdf结构，并渲染首尾页确认可正常显示。",
        "详细来源、披露时间、文件大小及哈希见《来源与校验清单.csv》。",
    ]
    (PACKAGE_DIR / "README_文件说明.txt").write_text("\n".join(lines), encoding="utf-8")

    for zip_path in (ZIP_CN, ZIP_EN):
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(PACKAGE_DIR.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=str(path))
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"ZIP CRC failure: {bad}")

    digest = sha256(ZIP_EN)
    Path("PACKAGE_SHA256.txt").write_text(f"{digest}  {ZIP_EN.name}\n", encoding="utf-8")
    print("PACKAGE READY", ZIP_EN, ZIP_EN.stat().st_size, digest, flush=True)


if __name__ == "__main__":
    main()
