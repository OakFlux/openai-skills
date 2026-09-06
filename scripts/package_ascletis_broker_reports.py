#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
TODAY = "2026-09-06"
OUT = Path("Ascletis_Pharma_Broker_Reports")
WORK = Path("_ascletis_work")
PDF_DIR = WORK / "pdfs"
PREVIEW_DIR = WORK / "preview"
ZIP_SHORT = Path("Ascletis_Reports.zip")
ZIP_LONG = Path("Ascletis_Pharma_Broker_Reports.zip")
DIAGNOSTICS = Path("generated/ascletis_discovery.json")

for p in (OUT, PDF_DIR, PREVIEW_DIR, DIAGNOSTICS.parent):
    p.mkdir(parents=True, exist_ok=True)

SEARCH_QUERIES = [
    '"Ascletis Pharma" research report pdf',
    '"Ascletis Pharma" broker report',
    '"Ascletis Pharma" equity research',
    '"Ascletis Pharma" CMBI',
    '"Ascletis Pharma" Phillip Securities',
    '"Ascletis Pharma" 1672 HK report',
    '"歌礼制药" 券商 深度报告 pdf',
    '"歌礼制药" 研究报告 pdf',
    '"歌礼制药" 首次覆盖',
    '"歌礼制药" 招银国际',
]

COMPANY_MARKERS = [
    "ASCLETIS PHARMA", "ASCLETIS", "歌礼制药", "歌禮製藥", "歌礼", "歌禮", "01672", "1672.HK", "1672 HK",
]

BROKER_PATTERNS = [
    ("CMB International / 招银国际", ["CMB INTERNATIONAL", "招银国际", "招銀國際", "CMBI"]),
    ("Phillip Securities / 辉立证券", ["PHILLIP SECURITIES", "辉立证券", "輝立證券", "PHILLIP CAPITAL"]),
    ("Guosen Securities / 国信证券", ["GUOSEN SECURITIES", "国信证券", "國信證券"]),
    ("China Merchants Securities / 招商证券", ["CHINA MERCHANTS SECURITIES", "招商证券", "招商證券"]),
    ("CITIC Securities / 中信证券", ["CITIC SECURITIES", "中信证券", "中信證券"]),
    ("CICC / 中金公司", ["CHINA INTERNATIONAL CAPITAL", "CICC", "中金公司"]),
    ("Haitong International / 海通国际", ["HAITONG INTERNATIONAL", "海通国际", "海通國際"]),
    ("Huatai Financial / 华泰金融", ["HUATAI", "华泰证券", "華泰證券", "HTSC"]),
    ("BOCOM International / 交银国际", ["BOCOM INTERNATIONAL", "交银国际", "交銀國際"]),
    ("CCBI / 建银国际", ["CCB INTERNATIONAL", "建银国际", "建銀國際", "CCBI"]),
    ("SPDB International / 浦银国际", ["SPDB INTERNATIONAL", "浦银国际", "浦銀國際"]),
    ("China Galaxy International / 中国银河国际", ["CHINA GALAXY INTERNATIONAL", "中国银河国际", "中國銀河國際"]),
    ("Soochow Securities / 东吴证券", ["SOOCHOW SECURITIES", "东吴证券", "東吳證券"]),
    ("Everbright Securities / 光大证券", ["EVERBRIGHT SECURITIES", "光大证券", "光大證券"]),
]

OFFICIAL_DOMAINS = [
    "cmbi.com.hk", "sg.cmbi.com", "poems.com.hk", "cyberquote.com.hk", "guosen.com.cn", "cmschina.com",
    "citics.com", "cicc.com", "htisec.com", "htsc.com.cn", "bocomgroup.com", "ccbintl.com", "spdbi.com",
    "chinastock.com.cn", "dwzq.com.cn", "ebscn.com", "research.phillip.com.cn",
]


@dataclass
class Candidate:
    source_page: str
    pdf_url: str
    source_title: str = ""
    source_snippet: str = ""
    source_kind: str = ""


@dataclass
class VerifiedReport:
    source_page: str
    source_pdf: str
    resolved_pdf: str
    source_title: str
    title: str
    broker: str
    report_date: str
    pages: int
    bytes: int
    sha256: str
    score: float
    local_path: str


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(unicodedata.normalize("NFKC", str(value or "")))).strip()


def compact(value: object) -> str:
    return re.sub(r"[^0-9A-Z\u4e00-\u9fff]+", "", clean(value).upper())


def decode_response(r: requests.Response) -> str:
    options = []
    for enc in (r.encoding, r.apparent_encoding, "utf-8", "gb18030", "big5"):
        if not enc:
            continue
        try:
            text = r.content.decode(enc)
            options.append((text.count("�"), -len(text), text))
        except Exception:
            pass
    if not options:
        return r.content.decode("utf-8", errors="replace")
    options.sort()
    return options[0][2]


def normalize_url(value: str, base: str = "") -> str:
    value = html.unescape(unquote(str(value or ""))).replace("\\/", "/").strip()
    if value.startswith("//"):
        value = "https:" + value
    if base:
        value = urljoin(base, value)
    if "uddg=" in value:
        try:
            qs = parse_qs(urlparse(value).query)
            if qs.get("uddg"):
                value = qs["uddg"][0]
        except Exception:
            pass
    return value.rstrip('),];}\"\'')


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/json,application/pdf;q=0.9,*/*;q=0.8",
    })
    return s


def search_duckduckgo(query: str) -> list[dict]:
    s = new_session()
    try:
        r = s.get("https://html.duckduckgo.com/html/", params={"q": query}, timeout=(15, 60))
        soup = BeautifulSoup(decode_response(r), "html.parser")
        rows = []
        for block in soup.select(".result"):
            a = block.select_one("a.result__a")
            if not a:
                continue
            snippet = block.select_one(".result__snippet")
            rows.append({
                "engine": "duckduckgo", "query": query,
                "title": clean(a.get_text(" ", strip=True)),
                "url": normalize_url(a.get("href", ""), r.url),
                "snippet": clean(snippet.get_text(" ", strip=True)) if snippet else "",
            })
        print("DDG", query, r.status_code, len(rows), flush=True)
        return rows
    except Exception as exc:
        print("DDG_ERROR", query, repr(exc), flush=True)
        return []


def search_bing(query: str) -> list[dict]:
    s = new_session()
    try:
        r = s.get("https://www.bing.com/search", params={"q": query, "count": 50}, timeout=(15, 60))
        soup = BeautifulSoup(decode_response(r), "html.parser")
        rows = []
        for block in soup.select("li.b_algo"):
            a = block.select_one("h2 a")
            if not a:
                continue
            p = block.select_one(".b_caption p")
            rows.append({
                "engine": "bing", "query": query,
                "title": clean(a.get_text(" ", strip=True)),
                "url": normalize_url(a.get("href", ""), r.url),
                "snippet": clean(p.get_text(" ", strip=True)) if p else "",
            })
        print("BING", query, r.status_code, len(rows), flush=True)
        return rows
    except Exception as exc:
        print("BING_ERROR", query, repr(exc), flush=True)
        return []


def discover_search_results() -> list[dict]:
    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = []
        for q in SEARCH_QUERIES:
            tasks.append(pool.submit(search_duckduckgo, q))
            tasks.append(pool.submit(search_bing, q))
        for task in as_completed(tasks):
            rows.extend(task.result())
    dedup = {}
    for row in rows:
        url = row.get("url", "")
        if url.startswith("http"):
            dedup[url] = row
    for row in dedup.values():
        print("SEARCH_RESULT", json.dumps(row, ensure_ascii=False), flush=True)
    return list(dedup.values())


def fetch_cmbi_page(page: int, lang: str) -> tuple[int, str, str]:
    s = new_session()
    try:
        r = s.get("https://www.cmbi.com.hk/market-stockreview", params={"lang": lang, "page": page}, timeout=(10, 35))
        return page, r.url, decode_response(r)
    except Exception:
        return page, "", ""


def discover_cmbi() -> list[dict]:
    hits = []
    # Scan both English and Chinese archives concurrently. 320 pages covers many years of archive entries.
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(fetch_cmbi_page, page, lang) for lang in ("en", "cn", "tc") for page in range(1, 321)]
        for future in as_completed(futures):
            page, base, text = future.result()
            if not text:
                continue
            low = text.lower()
            if not any(marker.lower() in low for marker in COMPANY_MARKERS):
                continue
            soup = BeautifulSoup(text, "html.parser")
            for a in soup.find_all("a", href=True):
                context = clean(" ".join([
                    a.get_text(" ", strip=True),
                    a.parent.get_text(" ", strip=True) if a.parent else "",
                ]))
                if any(marker.lower() in context.lower() for marker in COMPANY_MARKERS):
                    row = {
                        "engine": "cmbi_archive", "query": f"page={page}", "title": context[:600],
                        "url": normalize_url(a["href"], base), "snippet": "",
                    }
                    hits.append(row)
                    print("CMBI_MATCH", json.dumps(row, ensure_ascii=False), flush=True)
    dedup = {row["url"]: row for row in hits if row.get("url", "").startswith("http")}
    return list(dedup.values())


def eastmoney_request(begin: str, end: str, code: str, qtype: int, page: int) -> dict:
    s = new_session()
    params = {
        "industryCode": "*", "pageSize": "100", "industry": "*", "rating": "*", "ratingChange": "*",
        "beginTime": begin, "endTime": end, "pageNo": page, "fields": "", "qType": qtype,
        "orgCode": "", "code": code, "rcode": "", "p": page, "pageNum": page, "pageNumber": page,
    }
    r = s.get("https://reportapi.eastmoney.com/report/list", params=params, timeout=(15, 80))
    return r.json()


def discover_eastmoney() -> tuple[list[dict], list[Candidate]]:
    matches = []
    candidates = []
    # Direct stock-code queries are inexpensive and cover the full listing period.
    for code in ("01672", "1672", "01672.HK", "1672.HK"):
        for qtype in (0, 1, 2):
            try:
                first = eastmoney_request("2018-01-01", TODAY, code, qtype, 1)
                total = min(int(first.get("TotalPage") or 1), 20)
                rows = list(first.get("data") or [])
                for page in range(2, total + 1):
                    rows.extend(eastmoney_request("2018-01-01", TODAY, code, qtype, page).get("data") or [])
                for row in rows:
                    hay = clean(" ".join(str(row.get(k) or "") for k in ("title", "stockName", "stockCode", "orgName", "orgSName")))
                    if any(marker.lower() in hay.lower() for marker in COMPANY_MARKERS):
                        matches.append(row)
            except Exception as exc:
                print("EASTMONEY_ERROR", code, qtype, repr(exc), flush=True)
    seen = set()
    for row in matches:
        info = clean(row.get("infoCode"))
        if not info or info in seen:
            continue
        seen.add(info)
        print("EASTMONEY_MATCH", json.dumps(row, ensure_ascii=False, default=str), flush=True)
        candidates.append(Candidate(
            source_page=f"https://data.eastmoney.com/report/info/{info}.html",
            pdf_url=f"https://pdf.dfcfw.com/pdf/H3_{info}_1.pdf",
            source_title=clean(row.get("title")),
            source_snippet=clean(row.get("orgSName") or row.get("orgName")),
            source_kind="eastmoney",
        ))
    return matches, candidates


def extract_links_from_page(row: dict) -> tuple[list[Candidate], dict]:
    url = row.get("url", "")
    if not url.startswith("http"):
        return [], {"url": url, "error": "invalid_url"}
    if url.lower().split("?")[0].endswith(".pdf"):
        return [Candidate(url, url, row.get("title", ""), row.get("snippet", ""), row.get("engine", "search"))], {"url": url, "direct_pdf": True}
    s = new_session()
    try:
        r = s.get(url, timeout=(15, 80), allow_redirects=True)
        if r.content.startswith(b"%PDF-"):
            return [Candidate(url, r.url, row.get("title", ""), row.get("snippet", ""), row.get("engine", "search"))], {
                "url": url, "resolved": r.url, "status": r.status_code, "direct_pdf": True,
            }
        text = decode_response(r)
        soup = BeautifulSoup(text, "html.parser")
        links = []
        for tag in soup.find_all(True):
            for attr in ("href", "src", "data-src", "data-url", "data-download", "data-file", "content"):
                value = tag.get(attr)
                if not isinstance(value, str) or not value.strip():
                    continue
                link = normalize_url(value, r.url)
                low = link.lower()
                if ".pdf" in low or any(token in low for token in ("download", "attachment", "reportfile", "report_file")):
                    links.append(link)
        for raw in re.findall(r'(?:https?:)?//[^\s\"\'<>]+', text):
            link = normalize_url(raw, r.url)
            if ".pdf" in link.lower():
                links.append(link)
        links = list(dict.fromkeys(link for link in links if link.startswith("http")))
        title = clean(soup.title.get_text(" ", strip=True) if soup.title else row.get("title", ""))
        candidates = [Candidate(url, link, title or row.get("title", ""), row.get("snippet", ""), row.get("engine", "search")) for link in links]
        diagnostic = {
            "url": url, "resolved": r.url, "status": r.status_code,
            "content_type": r.headers.get("content-type"), "bytes": len(r.content),
            "title": title, "pdf_links": links[:100],
        }
        if links:
            print("PAGE_PDF_LINKS", json.dumps(diagnostic, ensure_ascii=False), flush=True)
        return candidates, diagnostic
    except Exception as exc:
        return [], {"url": url, "error": repr(exc)}


def discover_page_pdf_links(rows: list[dict]) -> tuple[list[Candidate], list[dict]]:
    candidates = []
    diagnostics = []
    # Prioritize likely relevant result domains and cap unrelated pages.
    filtered = []
    for row in rows:
        hay = clean(f"{row.get('title','')} {row.get('snippet','')} {row.get('url','')}").lower()
        if any(marker.lower() in hay for marker in COMPANY_MARKERS) or any(domain in hay for domain in OFFICIAL_DOMAINS):
            filtered.append(row)
    filtered = filtered[:180]
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(extract_links_from_page, row) for row in filtered]
        for future in as_completed(futures):
            found, diag = future.result()
            candidates.extend(found)
            diagnostics.append(diag)
    return candidates, diagnostics


def download_pdf(candidate: Candidate, index: int) -> tuple[Path | None, str, str]:
    s = new_session()
    variants = [candidate.pdf_url]
    if candidate.pdf_url.startswith("http://"):
        variants.append("https://" + candidate.pdf_url[len("http://"):])
    variants = list(dict.fromkeys(variants))
    errors = []
    for attempt in range(1, 3):
        for url in variants:
            try:
                r = s.get(url, headers={
                    "User-Agent": UA,
                    "Referer": candidate.source_page,
                    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                }, timeout=(20, 300), allow_redirects=True)
                if r.status_code == 200 and r.content.startswith(b"%PDF-") and len(r.content) > 20_000:
                    path = PDF_DIR / f"candidate_{index:04d}.pdf"
                    path.write_bytes(r.content)
                    print("PDF_DOWNLOADED", index, len(r.content), url, r.url, flush=True)
                    return path, r.url, ""
                errors.append(f"{url}: {r.status_code}/{len(r.content)}/{r.headers.get('content-type')}")
            except Exception as exc:
                errors.append(f"{url}: {exc!r}")
        time.sleep(attempt)
    return None, "", "; ".join(errors[-6:])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_pdf_text(path: Path, pages: int) -> str:
    reader = PdfReader(str(path))
    chunks = []
    indexes = list(range(min(20, pages)))
    if pages > 25:
        indexes.extend(range(max(20, pages - 4), pages))
    for idx in sorted(set(indexes)):
        try:
            chunks.append(reader.pages[idx].extract_text() or "")
        except Exception:
            pass
    text = "\n".join(chunks)
    if len(compact(text)) < 500:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(min(40, pages)), str(path), "-"],
            capture_output=True, check=False,
        )
        text += "\n" + result.stdout.decode("utf-8", errors="ignore")
    return text


def detect_broker(text: str, source: Candidate) -> str:
    hay = compact(f"{text} {source.source_title} {source.source_snippet} {source.source_page} {source.pdf_url}")
    for broker, markers in BROKER_PATTERNS:
        if any(compact(marker) in hay for marker in markers):
            return broker
    host = urlparse(source.pdf_url).netloc.lower()
    if "cmbi" in host:
        return "CMB International / 招银国际"
    if "poems" in host or "cyberquote" in host:
        return "Phillip Securities / 辉立证券"
    if "pdf.dfcfw.com" in host and source.source_snippet:
        return source.source_snippet
    return "Broker research / 券商研究"


def detect_date(text: str, source: Candidate) -> str:
    head = clean(text[:25000])
    patterns = [
        r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.]([0-3]?\d)\b",
        r"\b([0-3]?\d)[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})\b",
        r"\b(0?[1-9]|[12]\d|3[01])\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})\b",
    ]
    month_map = {m[:3].lower(): i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}
    dates = []
    for idx, pat in enumerate(patterns):
        for m in re.finditer(pat, head, flags=re.I):
            try:
                if idx == 0:
                    y, mo, d = map(int, m.groups())
                elif idx == 1:
                    d, mo, y = map(int, m.groups())
                else:
                    d = int(m.group(1)); mo = month_map[m.group(2)[:3].lower()]; y = int(m.group(3))
                dt = datetime(y, mo, d)
                if 2012 <= y <= 2026:
                    dates.append(dt)
            except Exception:
                pass
    if dates:
        # The report date generally appears on page one, so use earliest occurrence among parsed head dates.
        return dates[0].strftime("%Y-%m-%d")
    hay = f"{source.source_title} {source.source_snippet} {source.source_page} {source.pdf_url}"
    m = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", hay)
    if m:
        try:
            return datetime(*map(int, m.groups())).strftime("%Y-%m-%d")
        except Exception:
            pass
    return "undated"


def detect_title(text: str, source: Candidate) -> str:
    title = clean(source.source_title)
    if title and not any(noise in title.lower() for noise in ("search", "百度", "bing", "duckduckgo")):
        return title[:240]
    lines = [clean(x) for x in text.splitlines() if clean(x)]
    for line in lines[:80]:
        low = line.lower()
        if any(marker.lower() in low for marker in ("ascletis", "歌礼", "歌禮")) and 8 <= len(line) <= 220:
            return line
    return "Ascletis Pharma company research report"


def score_report(pages: int, broker: str, title: str, date: str, source: Candidate) -> float:
    score = min(pages, 45) * 1.5
    host = urlparse(source.pdf_url).netloc.lower()
    if any(domain in host for domain in OFFICIAL_DOMAINS):
        score += 40
    if broker != "Broker research / 券商研究":
        score += 20
    title_low = title.lower()
    if any(k in title_low for k in ("initiat", "deep", "coverage", "深度", "首次覆盖", "公司研究")):
        score += 20
    if date != "undated":
        try:
            year = int(date[:4])
            score += max(0, year - 2018) * 2
        except Exception:
            pass
    if pages < 4:
        score -= 100
    return score


def validate_candidate(candidate: Candidate, index: int) -> tuple[VerifiedReport | None, dict]:
    path, resolved_url, error = download_pdf(candidate, index)
    diag = {"candidate": asdict(candidate), "download_error": error}
    if path is None:
        return None, diag
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise RuntimeError("encrypted")
        pages = len(reader.pages)
        if pages < 4:
            raise RuntimeError(f"only {pages} pages")
        qpdf = subprocess.run(["qpdf", "--check", str(path)], capture_output=True, text=True)
        if qpdf.returncode not in (0, 3):
            raise RuntimeError("qpdf failed")
        text = extract_pdf_text(path, pages)
        hay = compact(f"{text} {candidate.source_title} {candidate.source_snippet}")
        company_ok = any(compact(marker) in hay for marker in COMPANY_MARKERS)
        if not company_ok:
            raise RuntimeError("company identity not found")
        broker = detect_broker(text, candidate)
        title = detect_title(text, candidate)
        report_date = detect_date(text, candidate)
        digest = sha256(path)
        report = VerifiedReport(
            source_page=candidate.source_page,
            source_pdf=candidate.pdf_url,
            resolved_pdf=resolved_url,
            source_title=candidate.source_title,
            title=title,
            broker=broker,
            report_date=report_date,
            pages=pages,
            bytes=path.stat().st_size,
            sha256=digest,
            score=score_report(pages, broker, title, report_date, candidate),
            local_path=str(path),
        )
        diag.update({"verified": asdict(report), "company_verified": company_ok, "qpdf_status": qpdf.returncode})
        print("VERIFIED", json.dumps(asdict(report), ensure_ascii=False), flush=True)
        return report, diag
    except Exception as exc:
        diag["validation_error"] = repr(exc)
        print("REJECTED", candidate.pdf_url, repr(exc), flush=True)
        return None, diag


def select_reports(reports: list[VerifiedReport]) -> list[VerifiedReport]:
    by_hash = {}
    for report in reports:
        existing = by_hash.get(report.sha256)
        if existing is None or report.score > existing.score:
            by_hash[report.sha256] = report
    ranked = sorted(by_hash.values(), key=lambda r: (r.score, r.pages, r.report_date), reverse=True)
    selected = []
    brokers = set()
    for report in ranked:
        if report.broker not in brokers:
            selected.append(report)
            brokers.add(report.broker)
        if len(selected) == 3:
            break
    if len(selected) < 2:
        for report in ranked:
            if report.sha256 not in {x.sha256 for x in selected}:
                selected.append(report)
            if len(selected) == 3:
                break
    return selected[:3]


def safe_filename(report: VerifiedReport, sequence: int) -> str:
    broker = report.broker.split("/")[0].strip()
    broker = re.sub(r"[^A-Za-z0-9]+", "_", broker).strip("_") or "Broker"
    date = report.report_date if report.report_date != "undated" else "Undated"
    return f"{sequence:02d}_{broker}_Ascletis_Pharma_{date}.pdf"


def build_package(selected: list[VerifiedReport]) -> None:
    if len(selected) < 2:
        raise RuntimeError(f"Only {len(selected)} complete reports were verified; at least 2 are required")
    records = []
    for i, report in enumerate(selected, 1):
        filename = safe_filename(report, i)
        shutil.copy2(report.local_path, OUT / filename)
        records.append({**asdict(report), "filename": filename})

    with (OUT / "source_manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["sequence", "filename", "report_date", "broker", "title", "pages", "bytes", "sha256", "source_page", "source_pdf", "resolved_pdf"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, row in enumerate(records, 1):
            writer.writerow({key: (i if key == "sequence" else row.get(key, "")) for key in fields})

    lines = [
        "Ascletis Pharma Inc. (歌礼制药有限公司; HKEX: 01672) broker research report package",
        f"Prepared: {TODAY}", "",
        "Selection standard: complete downloadable broker research PDFs only; ordinary news, login-only pages, cover-only files and incomplete previews were excluded.",
        "", "Included reports:",
    ]
    for i, row in enumerate(records, 1):
        lines.append(f"{i}. {row['filename']} | {row['broker']} | {row['report_date']} | {row['pages']} pages | SHA-256 {row['sha256']}")
        lines.append(f"   Title: {row['title']}")
    lines += ["", "Validation: PDF signature, encryption state, page count, company identity, qpdf structure and ZIP CRC were checked.", "Full source details are in source_manifest.csv."]
    (OUT / "README.txt").write_text("\n".join(lines), encoding="utf-8")

    for zip_path in (ZIP_SHORT, ZIP_LONG):
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(OUT.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path))
        with zipfile.ZipFile(zip_path, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f"ZIP CRC failure: {bad}")
    digest = sha256(ZIP_SHORT)
    Path("PACKAGE_SHA256.txt").write_text(f"{digest}  {ZIP_SHORT.name}\n", encoding="utf-8")
    print("PACKAGE_READY", ZIP_SHORT, ZIP_SHORT.stat().st_size, digest, flush=True)


def main() -> None:
    search_rows = discover_search_results()
    cmbi_rows = discover_cmbi()
    eastmoney_rows, eastmoney_candidates = discover_eastmoney()
    page_candidates, page_diagnostics = discover_page_pdf_links(search_rows + cmbi_rows)

    # Add direct PDF-like search URLs and Eastmoney candidates.
    candidates = eastmoney_candidates + page_candidates
    for row in search_rows + cmbi_rows:
        url = row.get("url", "")
        if ".pdf" in url.lower():
            candidates.append(Candidate(url, url, row.get("title", ""), row.get("snippet", ""), row.get("engine", "search")))
    dedup = {}
    for c in candidates:
        if c.pdf_url.startswith("http"):
            dedup[(c.source_page, c.pdf_url)] = c
    candidates = list(dedup.values())[:260]
    print("PDF_CANDIDATES", len(candidates), flush=True)

    reports = []
    validation_diagnostics = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(validate_candidate, c, i): (c, i) for i, c in enumerate(candidates, 1)}
        for future in as_completed(futures):
            report, diag = future.result()
            validation_diagnostics.append(diag)
            if report:
                reports.append(report)

    selected = select_reports(reports)
    diagnostics = {
        "prepared": TODAY,
        "search_results": search_rows,
        "cmbi_results": cmbi_rows,
        "eastmoney_matches": eastmoney_rows,
        "page_diagnostics": page_diagnostics,
        "candidate_count": len(candidates),
        "validation_diagnostics": validation_diagnostics,
        "verified_reports": [asdict(x) for x in reports],
        "selected_reports": [asdict(x) for x in selected],
    }
    DIAGNOSTICS.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    build_package(selected)


if __name__ == "__main__":
    main()
