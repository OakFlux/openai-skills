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

COMPANY_CN = "匯量科技有限公司"
COMPANY_CN_SIMPLE = "汇量科技有限公司"
COMPANY_EN = "Mobvista Inc."
STOCK_CODE = "01860"
AS_OF_DATE = "2026-09-07"

ROOT = Path.cwd()
WORK = ROOT / "_work_mobvista_20260907"
PACKAGE = WORK / "汇量科技_2020-2025年报及最新季度财务披露_截至2026-09-07"
PDF_DIR = PACKAGE / "PDF"
RENDER_DIR = WORK / "renders"
DIST = ROOT / "dist_mobvista_20260907"
FINAL_ZIP = DIST / "Mobvista_01860_2020-2025_Annual_and_Latest_Reports_2026-09-07.zip"

BASE = "https://www1.hkexnews.hk"
SEARCH_PAGE = BASE + "/search/titlesearch.xhtml"
SEARCH_API = BASE + "/search/titleSearchServlet.do"
STOCK_LISTS = [
    BASE + "/ncms/script/eds/activestock_sehk_e.json",
    BASE + "/ncms/script/eds/activestock_sehk_c.json",
]
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
        total=6,
        connect=6,
        read=6,
        status=6,
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


def find_stock_id(session: requests.Session) -> str:
    matches: list[dict] = []
    for endpoint in STOCK_LISTS:
        response = session.get(endpoint, params={"_": int(time.time() * 1000)}, timeout=(30, 180))
        print("STOCK LIST", response.status_code, len(response.content), response.url, flush=True)
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload.get("data") or payload.get("result") or []
        for row in rows:
            code = str(row.get("c") or row.get("code") or "").zfill(5)
            name = normalize(row.get("n") or row.get("name") or "")
            name_compact = compact(name)
            if code == STOCK_CODE and any(marker in name_compact for marker in ("MOBVISTA", "匯量科技", "汇量科技")):
                matches.append(row)
                print("STOCK MATCH", json.dumps(row, ensure_ascii=False), flush=True)
    ids = [str(row.get("i") or row.get("id") or "") for row in matches if row.get("i") or row.get("id")]
    if not ids:
        raise RuntimeError("未能从港交所股份清单严格识别汇量科技（01860）的 stockId")
    stock_id = max(set(ids), key=ids.count)
    if any(str(row.get("c") or "").zfill(5) != STOCK_CODE for row in matches):
        raise RuntimeError("股份代码校验失败")
    print("USING STOCK ID", stock_id, flush=True)
    return stock_id


def query_rows(session: requests.Session, stock_id: str, start: str, end: str, lang: str) -> list[dict]:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/html,*/*",
        "Referer": SEARCH_PAGE + "?lang=" + lang.lower(),
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
    warmup = session.post(SEARCH_PAGE, data=payload, headers=headers, timeout=(30, 180))
    print("HKEX POST", start, end, lang, warmup.status_code, len(warmup.content), flush=True)

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


def gather_records(session: requests.Session, stock_id: str) -> list[dict]:
    rows: list[dict] = []
    for year in range(2020, 2027):
        start = f"{year}0101"
        end = "20260907" if year == 2026 else f"{year}1231"
        for lang in ("ZH", "EN"):
            rows.extend(query_rows(session, stock_id, start, end, lang))
    unique: dict[tuple[str, str], dict] = {}
    for row in rows:
        unique[(row["lang"], row["url"])] = row
    rows = list(unique.values())
    print("TOTAL RECORDS", len(rows), flush=True)
    for row in rows:
        if any(token in row["title_norm"] for token in (
            "ANNUALREPORT", "年報", "年报", "INTERIMREPORT", "中期報告", "中期报告",
            "INTERIMRESULTS", "中期業績", "中期业绩", "QUARTERLYRESULTS", "季度業績", "季度业绩",
            "FIRSTQUARTER", "第一季度", "THIRDQUARTER", "第三季度"
        )):
            print("RELEVANT", row["lang"], row["release_text"], row["title"], row["url"], flush=True)
    return rows


def is_annual(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in ("ANNUALREPORT", "年報", "年报", "年度報告", "年度报告"))
    excluded = any(marker in title for marker in (
        "INTERIM", "中期", "RESULTS", "業績", "业绩", "SUMMARY", "摘要",
        "ENVIRONMENTAL", "SUSTAINABILITY", "ESG", "環境", "环境", "可持續", "可持续",
        "NOTICE", "LETTER", "通告", "通知", "CIRCULAR", "通函", "SUPPLEMENTAL", "補充", "补充",
        "NOTIFICATION", "CHANGE REQUEST", "變更申請", "变更申请", "DISPLAYDOCUMENT", "展示文件",
    ))
    return positive and not excluded


def is_formal_interim(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in ("INTERIMREPORT", "中期報告", "中期报告", "HALFYEARREPORT"))
    excluded = any(marker in title for marker in (
        "RESULTS", "業績", "业绩", "NOTICE", "LETTER", "通告", "通知", "NOTIFICATION", "REPLYFORM", "回條", "回条"
    ))
    return positive and not excluded


def is_interim_results(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in (
        "INTERIMRESULTS", "中期業績", "中期业绩", "RESULTSFORTHESIXMONTHS", "SIXMONTHSENDED"
    ))
    excluded = any(marker in title for marker in (
        "CLARIFICATION", "澄清", "SUPPLEMENTAL", "補充", "补充", "DATEOFBOARDMEETING", "董事會會議日期", "董事会会议日期"
    ))
    return positive and not excluded


def is_quarterly_results(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in (
        "QUARTERLYRESULTS", "季度業績", "季度业绩", "FIRSTQUARTERLYREPORT", "FIRSTQUARTERRESULTS",
        "第一季度報告", "第一季度报告", "第一季度業績", "第一季度业绩",
        "THIRDQUARTERLYREPORT", "THIRDQUARTERRESULTS", "第三季度報告", "第三季度报告", "第三季度業績", "第三季度业绩",
        "RESULTSFORTHEMONTHSENDED", "RESULTSFORTHE3MONTHS", "RESULTSFORTHE9MONTHS",
        "THREEMONTHSENDED", "NINEMONTHSENDED"
    ))
    excluded = any(marker in title for marker in (
        "DATEOFBOARDMEETING", "董事會會議日期", "董事会会议日期", "PARENTCOMPANY", "控股股東", "控股股东",
        "SUPPLEMENTAL", "補充", "补充", "CLARIFICATION", "澄清"
    ))
    return positive and not excluded


def has_year(row: dict, year: int) -> bool:
    title = row["title_norm"]
    return str(year) in title or compact(YEAR_CN[year]) in title


def lang_priority(row: dict) -> int:
    if row["lang"] == "ZH":
        return 3
    if "_c.pdf" in row["url"].lower():
        return 2
    return 1


def choose(rows: list[dict]) -> dict:
    if not rows:
        raise RuntimeError("候选列表为空")
    return sorted(rows, key=lambda r: (lang_priority(r), r["release_dt"], len(r["title"])), reverse=True)[0]


def infer_year(row: dict) -> int:
    for year in range(2026, 2019, -1):
        if has_year(row, year):
            return year
    return row["release_dt"].year


def select_documents(rows: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for year in range(2020, 2026):
        candidates = [row for row in rows if is_annual(row) and has_year(row, year)]
        print("ANNUAL CANDIDATES", year, [(x["lang"], x["release_text"], x["title"], x["url"]) for x in candidates], flush=True)
        if not candidates:
            raise RuntimeError(f"未找到汇量科技 {year} 年完整年报")
        row = choose(candidates)
        selected.append({
            "kind": "年度报告",
            "year": year,
            "filename": f"{len(selected)+1:02d}_汇量科技_{year}年年度报告.pdf",
            "row": row,
            "minimum_pages": 60,
        })

    quarterly = sorted([row for row in rows if is_quarterly_results(row)], key=lambda r: (r["release_dt"], lang_priority(r)), reverse=True)
    print("QUARTERLY CANDIDATES", [(x["lang"], x["release_text"], x["title"], x["url"]) for x in quarterly[:20]], flush=True)
    if not quarterly:
        raise RuntimeError("未找到任何季度业绩报告")
    latest_quarter_dt = quarterly[0]["release_dt"]
    latest_quarter = choose([r for r in quarterly if r["release_dt"] == latest_quarter_dt])
    quarter_year = infer_year(latest_quarter)
    selected.append({
        "kind": "季度业绩报告",
        "year": quarter_year,
        "filename": f"{len(selected)+1:02d}_汇量科技_{quarter_year}年最新季度业绩报告.pdf",
        "row": latest_quarter,
        "minimum_pages": 5,
    })

    formal = sorted([row for row in rows if is_formal_interim(row)], key=lambda r: (r["release_dt"], lang_priority(r)), reverse=True)
    print("FORMAL INTERIM CANDIDATES", [(x["lang"], x["release_text"], x["title"], x["url"]) for x in formal[:20]], flush=True)
    if formal:
        formal_dt = formal[0]["release_dt"]
        latest_formal = choose([r for r in formal if r["release_dt"] == formal_dt])
        formal_year = infer_year(latest_formal)
        selected.append({
            "kind": "中期报告",
            "year": formal_year,
            "filename": f"{len(selected)+1:02d}_汇量科技_{formal_year}年中期报告_最新完整中期报告.pdf",
            "row": latest_formal,
            "minimum_pages": 25,
        })

    results = sorted([row for row in rows if is_interim_results(row)], key=lambda r: (r["release_dt"], lang_priority(r)), reverse=True)
    print("INTERIM RESULTS CANDIDATES", [(x["lang"], x["release_text"], x["title"], x["url"]) for x in results[:20]], flush=True)
    if results:
        result_dt = results[0]["release_dt"]
        latest_result = choose([r for r in results if r["release_dt"] == result_dt])
        result_year = infer_year(latest_result)
        selected.append({
            "kind": "中期业绩公告",
            "year": result_year,
            "filename": f"{len(selected)+1:02d}_汇量科技_{result_year}年中期业绩公告_最新财务披露.pdf",
            "row": latest_result,
            "minimum_pages": 8,
        })

    # Remove any accidental duplicate URL while preserving order.
    deduped: list[dict] = []
    seen_urls: set[str] = set()
    for item in selected:
        url = item["row"]["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        item["filename"] = f"{len(deduped)+1:02d}_" + item["filename"].split("_", 1)[1]
        deduped.append(item)

    print("FINAL SELECTED DOCUMENTS", flush=True)
    for item in deduped:
        row = item["row"]
        print(item["kind"], item["year"], row["lang"], row["release_text"], row["title"], row["url"], flush=True)
    return deduped


def fetch_pdf(session: requests.Session, url: str) -> tuple[bytes, str]:
    variants = [url]
    if "www1.hkexnews.hk" in url:
        variants.append(url.replace("www1.hkexnews.hk", "www.hkexnews.hk"))
    elif "www.hkexnews.hk" in url:
        variants.append(url.replace("www.hkexnews.hk", "www1.hkexnews.hk"))
    variants = list(dict.fromkeys(variants))
    errors: list[str] = []
    for attempt in range(1, 4):
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
    raise RuntimeError("PDF 下载失败：" + "; ".join(errors[-8:]))


def command_ok(command: list[str]) -> tuple[bool, str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    return result.returncode in (0, 3), ((result.stdout or "") + (result.stderr or ""))[-4000:]


def extract_probe(reader: PdfReader, pages: int) -> str:
    indexes = list(range(min(45, pages)))
    if pages > 50:
        indexes.extend(range(max(45, pages - 8), pages))
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
            ["pdftoppm", "-f", str(page_no), "-l", str(page_no), "-singlefile", "-png", "-r", "72", str(path), str(prefix)],
            check=True,
            capture_output=True,
            timeout=180,
        )
        image = Path(str(prefix) + ".png")
        if not image.exists() or image.stat().st_size < 1000:
            raise RuntimeError(f"{path.name} {label} page render failed")
        rendered_files.append(image.name)

    text = compact(extract_probe(reader, pages))
    company_ok = any(compact(marker) in text for marker in (
        COMPANY_CN, COMPANY_CN_SIMPLE, COMPANY_EN, "MOBVISTA", "匯量科技", "汇量科技", "01860", "1860"
    ))
    year_ok = str(item["year"]) in text or compact(YEAR_CN[item["year"]]) in text
    if item["kind"] == "年度报告":
        type_ok = any(compact(marker) in text for marker in ("ANNUALREPORT", "年報", "年报", "年度報告", "年度报告"))
    elif item["kind"] == "中期报告":
        type_ok = any(compact(marker) in text for marker in ("INTERIMREPORT", "中期報告", "中期报告", "HALFYEARREPORT"))
    elif item["kind"] == "中期业绩公告":
        type_ok = any(compact(marker) in text for marker in ("INTERIMRESULTS", "中期業績", "中期业绩", "SIXMONTHSENDED"))
    else:
        type_ok = any(compact(marker) in text for marker in (
            "QUARTERLYRESULTS", "季度業績", "季度业绩", "FIRSTQUARTER", "第一季度", "THREEMONTHSENDED",
            "THIRDQUARTER", "第三季度", "NINEMONTHSENDED"
        ))

    print("VALIDATE", path.name, pages, company_ok, year_ok, type_ok, "text_chars", len(text), flush=True)
    if len(text) > 1000 and not company_ok:
        raise RuntimeError(f"{path.name} 未在可提取文本中识别到汇量科技或股份代码")
    if len(text) > 1000 and not year_ok:
        raise RuntimeError(f"{path.name} 未识别到目标报告年度 {item['year']}")

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

    with (PACKAGE / "来源与校验清单.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "序号", "文件名", "报告年度", "报告类型", "港交所原公告标题", "披露时间", "语言",
            "页数", "文件大小_字节", "SHA256", "公司名称文本校验", "年度文本校验", "类型文本校验", "港交所PDF地址",
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
                "公司名称文本校验": record["company_text_verified"],
                "年度文本校验": record["year_text_verified"],
                "类型文本校验": record["type_text_verified"],
                "港交所PDF地址": record["resolved_url"],
            })

    latest_quarter = next(r for r in records if r["kind"] == "季度业绩报告")
    formal_interims = [r for r in records if r["kind"] == "中期报告"]
    interim_results = [r for r in records if r["kind"] == "中期业绩公告"]
    lines = [
        f"{COMPANY_CN_SIMPLE}（{COMPANY_EN}）",
        "香港交易所股份代号：01860 / 1860",
        f"整理日期：{AS_OF_DATE}",
        "",
        "一、收录口径",
        "1. 收录2020、2021、2022、2023、2024、2025年度报告全文，共6份。",
        f"2. 最新严格季度口径报告为{latest_quarter['year']}年季度业绩报告。",
    ]
    if formal_interims:
        lines.append(f"3. 同时收录最新完整中期报告：{formal_interims[-1]['official_title']}。")
    if interim_results:
        lines.append(f"4. 同时收录截至整理日更新的中期业绩公告：{interim_results[-1]['official_title']}。")
    lines += ["", "二、文件清单"]
    for record in records:
        lines.append(
            f"{record['sequence']}. {record['filename']}｜{record['pages']}页｜披露时间：{record['release_time']}｜SHA-256：{record['sha256']}"
        )
    lines += [
        "",
        "三、校验说明",
        "每份PDF均检查文件头、实际页数、公司名称/股份代码、报告年度，并使用qpdf检查结构完整性；首尾页均已渲染确认可正常显示。",
        "详细来源、披露时间、页数、文件大小和SHA-256见《来源与校验清单.csv》。",
    ]
    (PACKAGE / "README_文件说明.txt").write_text("\n".join(lines), encoding="utf-8")

    manifest = {
        "company": COMPANY_CN_SIMPLE,
        "company_english": COMPANY_EN,
        "stock_code": STOCK_CODE,
        "as_of_date": AS_OF_DATE,
        "pdf_count": len(records),
        "documents": records,
    }
    (PACKAGE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (PACKAGE / "SHA256SUMS.txt").write_text(
        "\n".join(f"{record['sha256']}  PDF/{record['filename']}" for record in records) + "\n",
        encoding="utf-8",
    )

    with zipfile.ZipFile(FINAL_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(WORK)))
    with zipfile.ZipFile(FINAL_ZIP, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError("ZIP CRC 校验失败：" + bad)
        pdf_entries = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
        if len(pdf_entries) != len(records):
            raise RuntimeError(f"ZIP 内PDF数量不一致：{len(pdf_entries)} != {len(records)}")
    package_hash = sha256(FINAL_ZIP)
    (DIST / "PACKAGE_SHA256.txt").write_text(f"{package_hash}  {FINAL_ZIP.name}\n", encoding="utf-8")
    print("FINAL ZIP", json.dumps({
        "path": str(FINAL_ZIP),
        "bytes": FINAL_ZIP.stat().st_size,
        "sha256": package_hash,
        "entries": len(zipfile.ZipFile(FINAL_ZIP).namelist()),
        "pdf_count": len(records),
    }, ensure_ascii=False), flush=True)


def main() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    if DIST.exists():
        shutil.rmtree(DIST)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    session = build_session()
    stock_id = find_stock_id(session)
    rows = gather_records(session, stock_id)
    selected = select_documents(rows)

    records: list[dict] = []
    for sequence, item in enumerate(selected, 1):
        data, resolved_url = fetch_pdf(session, item["row"]["url"])
        destination = PDF_DIR / item["filename"]
        destination.write_bytes(data)
        metadata = validate_pdf(destination, item)
        record = {
            "sequence": sequence,
            "filename": item["filename"],
            "year": item["year"],
            "kind": item["kind"],
            "official_title": item["row"]["title"],
            "release_time": item["row"]["release_text"],
            "language": "繁体中文" if item["row"]["lang"] == "ZH" else "英文",
            "requested_url": item["row"]["url"],
            "resolved_url": resolved_url,
            **metadata,
        }
        records.append(record)
        print("VERIFIED", json.dumps(record, ensure_ascii=False), flush=True)

    expected_annuals = {2020, 2021, 2022, 2023, 2024, 2025}
    found_annuals = {r["year"] for r in records if r["kind"] == "年度报告"}
    if found_annuals != expected_annuals:
        raise RuntimeError(f"年报年度集合不完整：{sorted(found_annuals)}")
    if len({r["sha256"] for r in records}) != len(records):
        raise RuntimeError("发现重复PDF哈希")
    if not any(r["kind"] == "季度业绩报告" for r in records):
        raise RuntimeError("缺少最新季度业绩报告")

    make_package(records)


if __name__ == "__main__":
    main()
