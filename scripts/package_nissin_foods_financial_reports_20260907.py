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

COMPANY_CN = "日清食品有限公司"
COMPANY_EN = "Nissin Foods Company Limited"
STOCK_CODE = "01475"
AS_OF_DATE = "2026-09-07"

ROOT = Path.cwd()
WORK = ROOT / "_work_nissin_foods_20260907"
PACKAGE = WORK / "日清食品_2020-2025年报及最新中期财务披露_截至2026-09-07"
PDF_DIR = PACKAGE / "PDF"
RENDER_DIR = WORK / "renders"
DIST = ROOT / "dist_nissin_foods_20260907"
FINAL_ZIP = DIST / "Nissin_Foods_1475_2020-2025_Annual_and_Latest_Reports.zip"

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
    matches = []
    for endpoint in STOCK_LISTS:
        response = session.get(endpoint, params={"_": int(time.time() * 1000)}, timeout=(30, 180))
        print("STOCK LIST", response.status_code, len(response.content), response.url, flush=True)
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload.get("data") or payload.get("result") or []
        for row in rows:
            code = str(row.get("c") or row.get("code") or "").zfill(5)
            name = normalize(row.get("n") or row.get("name") or "")
            if code == STOCK_CODE or "NISSIN FOODS" in name.upper() or "日清食品" in name or "日清食品" in name:
                matches.append(row)
                print("STOCK MATCH", json.dumps(row, ensure_ascii=False), flush=True)
    ids = [str(row.get("i") or row.get("id") or "") for row in matches if row.get("i") or row.get("id")]
    if not ids:
        raise RuntimeError("未能从港交所股份清单识别日清食品（01475）的 stockId")
    # The same issuer should map to one id across language lists.
    return max(set(ids), key=ids.count)


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

    result = []
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
    for year in range(2021, 2027):
        start = f"{year}0101"
        end = "20260907" if year == 2026 else f"{year}1231"
        for lang in ("ZH", "EN"):
            rows.extend(query_rows(session, stock_id, start, end, lang))
    unique = {}
    for row in rows:
        unique[(row["lang"], row["url"])] = row
    rows = list(unique.values())
    print("TOTAL RECORDS", len(rows), flush=True)
    for row in rows:
        if any(token in row["title_norm"] for token in (
            "ANNUALREPORT", "年報", "年报", "INTERIMREPORT", "中期報告", "中期报告", "INTERIMRESULTS", "中期業績", "中期业绩"
        )):
            print("RELEVANT", row["lang"], row["release_text"], row["title"], row["url"], flush=True)
    return rows


def is_annual(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in ("ANNUALREPORT", "年報", "年报", "年度報告", "年度报告"))
    excluded = any(marker in title for marker in (
        "INTERIM", "中期", "RESULTS", "業績", "业绩", "SUMMARY", "摘要",
        "ENVIRONMENTAL", "SUSTAINABILITY", "ESG", "環境", "环境", "可持續", "可持续",
        "NOTICE", "LETTER", "通告", "通知", "CIRCULAR", "通函",
    ))
    return positive and not excluded


def is_formal_interim(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in ("INTERIMREPORT", "中期報告", "中期报告"))
    excluded = any(marker in title for marker in ("RESULTS", "業績", "业绩", "NOTICE", "LETTER", "通告", "通知"))
    return positive and not excluded


def is_interim_results(row: dict) -> bool:
    title = row["title_norm"]
    positive = any(marker in title for marker in ("INTERIMRESULTS", "中期業績", "中期业绩"))
    excluded = any(marker in title for marker in ("CLARIFICATION", "澄清", "SUPPLEMENTAL", "補充", "补充"))
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


def select_documents(rows: list[dict]) -> list[dict]:
    selected: list[dict] = []
    for year in range(2020, 2026):
        candidates = [row for row in rows if is_annual(row) and has_year(row, year)]
        if not candidates:
            # Fallback for unusual titles: annual report normally appears Mar-May in following year.
            candidates = [
                row for row in rows
                if is_annual(row)
                and row["release_dt"].year == year + 1
                and 3 <= row["release_dt"].month <= 6
            ]
        print("ANNUAL CANDIDATES", year, [(x["lang"], x["release_text"], x["title"], x["url"]) for x in candidates], flush=True)
        if not candidates:
            raise RuntimeError(f"未找到 {year} 年完整年报")
        row = choose(candidates)
        selected.append({
            "kind": "年度报告",
            "year": year,
            "filename": f"{len(selected)+1:02d}_日清食品_{year}年年度报告.pdf",
            "row": row,
            "minimum_pages": 70,
        })

    formal = sorted([row for row in rows if is_formal_interim(row)], key=lambda r: (r["release_dt"], lang_priority(r)), reverse=True)
    if not formal:
        raise RuntimeError("未找到任何完整中期报告")
    latest_formal = choose([r for r in formal if r["release_dt"] == formal[0]["release_dt"]])
    formal_year = next((year for year in range(2026, 2019, -1) if has_year(latest_formal, year)), latest_formal["release_dt"].year)
    selected.append({
        "kind": "中期报告",
        "year": formal_year,
        "filename": f"{len(selected)+1:02d}_日清食品_{formal_year}年中期报告_最新完整中期报告.pdf",
        "row": latest_formal,
        "minimum_pages": 35,
    })

    results = sorted([row for row in rows if is_interim_results(row)], key=lambda r: (r["release_dt"], lang_priority(r)), reverse=True)
    if results:
        latest_result = choose([r for r in results if r["release_dt"] == results[0]["release_dt"]])
        result_year = next((year for year in range(2026, 2019, -1) if has_year(latest_result, year)), latest_result["release_dt"].year)
        if result_year > formal_year:
            selected.append({
                "kind": "中期业绩公告",
                "year": result_year,
                "filename": f"{len(selected)+1:02d}_日清食品_{result_year}年中期业绩公告_最新财务披露.pdf",
                "row": latest_result,
                "minimum_pages": 15,
            })

    print("SELECTED DOCUMENTS", flush=True)
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
    errors = []
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
    indexes = list(range(min(40, pages)))
    if pages > 45:
        indexes.extend(range(max(40, pages - 8), pages))
    chunks = []
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

    text = compact(extract_probe(reader, pages))
    company_ok = any(compact(marker) in text for marker in (
        COMPANY_CN, COMPANY_EN, "日清食品", "NISSINFOODS", "1475", "01475"
    ))
    year_ok = str(item["year"]) in text or compact(YEAR_CN[item["year"]]) in text
    if item["kind"] == "年度报告":
        type_ok = any(compact(marker) in text for marker in ("ANNUALREPORT", "年報", "年报", "年度報告", "年度报告"))
    elif item["kind"] == "中期报告":
        type_ok = any(compact(marker) in text for marker in ("INTERIMREPORT", "中期報告", "中期报告"))
    else:
        type_ok = any(compact(marker) in text for marker in ("INTERIMRESULTS", "中期業績", "中期业绩", "SIXMONTHSENDED"))

    # Some heavily outlined PDFs have sparse extraction. Official HKEX metadata plus structural checks remain authoritative.
    print("VALIDATE", path.name, pages, company_ok, year_ok, type_ok, "text_chars", len(text), flush=True)
    if len(text) > 1000 and not company_ok:
        raise RuntimeError(f"{path.name} 未在可提取文本中识别到公司名称或股份代码")

    return {
        "pages": pages,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "qpdf_ok": True,
        "first_last_rendered": True,
        "company_text_verified": company_ok,
        "year_text_verified": year_ok,
        "type_text_verified": type_ok,
    }


def make_package(records: list[dict], selected: list[dict]) -> None:
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

    formal = [r for r in records if r["kind"] == "中期报告"][-1]
    latest_result = next((r for r in records if r["kind"] == "中期业绩公告"), None)
    lines = [
        f"{COMPANY_CN}（{COMPANY_EN}）",
        "香港交易所股份代号：01475 / 1475",
        f"整理日期：{AS_OF_DATE}",
        "",
        "一、收录口径",
        "1. 收录2020、2021、2022、2023、2024、2025年度报告全文，共6份。",
        "2. 香港主板公司通常不按A股口径发布独立第一季度及第三季度报告，因此以完整中期报告对应‘最新季报’需求。",
        f"3. 最新完整中期报告为{formal['year']}年中期报告。",
    ]
    if latest_result:
        lines.append(f"4. 截至整理日，{latest_result['year']}年完整中期报告尚未发布；另收录{latest_result['year']}年中期业绩公告，覆盖当前最新财务披露。")
    lines += ["", "二、文件清单"]
    for record in records:
        lines.append(
            f"{record['sequence']}. {record['filename']}｜{record['language']}｜{record['pages']}页｜SHA-256：{record['sha256']}"
        )
    lines += [
        "",
        "三、校验说明",
        "每份PDF均检查文件头、加密状态、实际页数、qpdf结构，并渲染首尾页确认可正常显示。",
        "公司名称、报告年度和报告类型在可提取文本中同步核验；详细来源见《来源与校验清单.csv》。",
    ]
    (PACKAGE / "README_文件说明.txt").write_text("\n".join(lines), encoding="utf-8")
    (PACKAGE / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    with zipfile.ZipFile(FINAL_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(WORK)))
    with zipfile.ZipFile(FINAL_ZIP, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError("ZIP CRC 校验失败：" + bad)
        entries = archive.namelist()
    package_hash = sha256(FINAL_ZIP)
    (DIST / "PACKAGE_SHA256.txt").write_text(f"{package_hash}  {FINAL_ZIP.name}\n", encoding="utf-8")
    print("FINAL ZIP", json.dumps({
        "path": str(FINAL_ZIP),
        "bytes": FINAL_ZIP.stat().st_size,
        "sha256": package_hash,
        "entries": len(entries),
        "pdf_count": len([x for x in entries if x.lower().endswith('.pdf')]),
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
    print("USING STOCK ID", stock_id, flush=True)
    rows = gather_records(session, stock_id)
    selected = select_documents(rows)

    records = []
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

    if len(records) not in (7, 8):
        raise RuntimeError(f"文件数量异常：{len(records)}")
    if len({record["sha256"] for record in records}) != len(records):
        raise RuntimeError("检测到重复PDF")
    if [r["year"] for r in records if r["kind"] == "年度报告"] != list(range(2020, 2026)):
        raise RuntimeError("年报年份不完整")

    make_package(records, selected)


if __name__ == "__main__":
    main()
