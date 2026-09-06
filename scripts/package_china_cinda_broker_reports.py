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
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

TODAY = "2026-09-06"
COMPANY_CN = "中国信达"
COMPANY_EN = "China Cinda"
STOCK = "01359"
OUT_DIR = Path("China_Cinda_Broker_Reports")
WORK = Path("_work_china_cinda")
PREVIEW = WORK / "preview"
ZIP_SHORT = Path("China_Cinda_Reports.zip")
ZIP_LONG = Path("China_Cinda_Broker_Research_Reports.zip")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/152.0.0.0 Safari/537.36"

OUT_DIR.mkdir(exist_ok=True)
PREVIEW.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})


def compact(value: object) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).upper()
    return re.sub(r"[^0-9A-Z\u4e00-\u9fff]+", "", text)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get(url: str, referer: str | None = None, timeout: int = 600) -> requests.Response:
    headers = {
        "User-Agent": UA,
        "Accept": "application/pdf,application/json,text/html,application/xhtml+xml,*/*;q=0.8",
        "Referer": referer or "https://www.google.com/",
    }
    last = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, headers=headers, timeout=(30, timeout), allow_redirects=True)
            print("GET", response.status_code, response.headers.get("content-type"), len(response.content), url, "->", response.url, flush=True)
            return response
        except Exception as exc:
            last = exc
            print("GETERR", url, repr(exc), flush=True)
            time.sleep(attempt * 2)
    raise RuntimeError(f"GET failed for {url}: {last!r}")


def find_guosen_report() -> dict:
    api = "https://reportapi.eastmoney.com/report/list"
    matches = []
    for qtype in (0, 1, 2):
        page = 1
        total_pages = 1
        while page <= total_pages:
            params = {
                "industryCode": "*",
                "pageSize": "100",
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "beginTime": "2025-02-18",
                "endTime": "2025-02-23",
                "pageNo": str(page),
                "fields": "",
                "qType": str(qtype),
                "orgCode": "",
                "code": "",
                "rcode": "",
                "p": str(page),
                "pageNum": str(page),
                "pageNumber": str(page),
            }
            response = session.get(
                api,
                params=params,
                headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"},
                timeout=(30, 180),
            )
            print("API", response.status_code, qtype, page, len(response.content), response.url, flush=True)
            response.raise_for_status()
            payload = response.json()
            total_pages = int(payload.get("TotalPage") or 1)
            for row in payload.get("data") or []:
                title = str(row.get("title") or "")
                org = str(row.get("orgSName") or row.get("orgName") or "")
                stock_name = str(row.get("stockName") or "")
                if (
                    "中国信达" in title
                    and "受益经济复苏" in title
                    and ("国信" in org or not org)
                ) or (
                    "中国信达" in stock_name
                    and "受益经济复苏" in title
                ):
                    item = {"qtype": qtype, **row}
                    matches.append(item)
                    print("GUOSEN MATCH", json.dumps(item, ensure_ascii=False, default=str), flush=True)
            page += 1
            time.sleep(0.2)
    if not matches:
        raise RuntimeError("Could not locate the Guosen China Cinda deep report in the public report API")
    matches.sort(
        key=lambda row: (
            1 if "国信" in str(row.get("orgSName") or row.get("orgName") or "") else 0,
            int(row.get("attachPages") or 0),
            str(row.get("publishDate") or ""),
        ),
        reverse=True,
    )
    return matches[0]


def download_guosen(row: dict, destination: Path) -> dict:
    info_code = str(row.get("infoCode") or "").strip()
    if not info_code:
        raise RuntimeError("Guosen report record has no infoCode")
    urls = [
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf?{int(time.time())}",
    ]
    for url in urls:
        response = get(url, referer="https://data.eastmoney.com/report/")
        if response.status_code == 200 and response.content.startswith(b"%PDF-"):
            destination.write_bytes(response.content)
            return {
                "source_page": f"https://data.eastmoney.com/report/info/{info_code}.html",
                "source_pdf": response.url,
                "database_record": row,
            }
    raise RuntimeError("The Guosen report PDF could not be downloaded")


def extract_pdf_links(page_url: str) -> tuple[requests.Response, list[str]]:
    response = get(page_url, referer="https://www.poems.com.hk/")
    response.raise_for_status()
    # Site occasionally declares an incorrect encoding; UTF-8 is usually correct.
    try:
        text = response.content.decode("utf-8")
    except Exception:
        text = response.text
    soup = BeautifulSoup(text, "html.parser")
    links: list[str] = []
    for tag in soup.find_all(True):
        for attr in ("href", "src", "data-url", "data-src"):
            value = tag.get(attr)
            if isinstance(value, str) and ".pdf" in value.lower():
                links.append(urljoin(response.url, value.strip()))
    for value in re.findall(r"(?:https?:)?//[^\s\"'<>]+\.pdf(?:\?[^\s\"'<>]*)?", text, flags=re.I):
        links.append(urljoin(response.url, value))
    dedup = []
    seen = set()
    for link in links:
        link = html.unescape(link).rstrip("),]};'\"")
        if link not in seen:
            seen.add(link)
            dedup.append(link)
    print("POEMS PDF LINKS", page_url, dedup, flush=True)
    return response, dedup


def download_phillip(page_url: str, fallbacks: list[str], destination: Path) -> dict:
    page_response, links = extract_pdf_links(page_url)
    candidates = []
    for link in links + fallbacks:
        lower = link.lower()
        # Exclude generic T&Cs/advisory PDFs.
        if any(marker in lower for marker in ("pspl_tc", "advisorynotes", "terms")):
            continue
        candidates.append(link)
    errors = []
    for url in candidates:
        response = get(url, referer=page_response.url)
        if response.status_code == 200 and response.content.startswith(b"%PDF-"):
            destination.write_bytes(response.content)
            return {
                "source_page": page_url,
                "source_pdf": response.url,
                "resolved_page": page_response.url,
            }
        errors.append(f"{url}: {response.status_code}/{len(response.content)}")
    raise RuntimeError("Phillip report PDF download failed: " + "; ".join(errors[-10:]))


def extract_text(path: Path, pages: int) -> str:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            pass
    chunks = []
    for index in range(min(pages, 25)):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception as exc:
            print("TEXT WARN", path.name, index, repr(exc), flush=True)
    text = "\n".join(chunks)
    if len(compact(text)) < 500:
        proc = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(min(pages, 40)), str(path), "-"],
            capture_output=True,
            check=False,
        )
        text += "\n" + proc.stdout.decode("utf-8", errors="ignore")
    return text


def validate_pdf(path: Path, broker_markers: list[str], min_pages: int, expected_pages: int | None = None) -> dict:
    if not path.exists() or path.stat().st_size < 20_000:
        raise RuntimeError(f"Missing or implausibly small PDF: {path}")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise RuntimeError(f"Invalid PDF signature: {path}")
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError(f"Encrypted PDF: {path}") from exc
    pages = len(reader.pages)
    if pages < min_pages:
        raise RuntimeError(f"Only {pages} pages in {path.name}")
    if expected_pages and abs(pages - expected_pages) > 1:
        raise RuntimeError(f"Page mismatch in {path.name}: expected about {expected_pages}, got {pages}")
    qpdf = subprocess.run(["qpdf", "--check", str(path)], capture_output=True, text=True)
    if qpdf.returncode not in (0, 3):
        raise RuntimeError("qpdf failed: " + qpdf.stderr[-500:])
    for label, page in (("first", 1), ("last", pages)):
        prefix = PREVIEW / f"{path.stem}_{label}"
        subprocess.run(
            ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-png", "-r", "72", str(path), str(prefix)],
            check=True,
            capture_output=True,
        )
        png = Path(str(prefix) + ".png")
        if not png.exists() or png.stat().st_size < 1_000:
            raise RuntimeError(f"Render test failed: {path.name} {label}")
    text = compact(extract_text(path, pages))
    company_ok = (
        compact(COMPANY_CN) in text
        or compact(COMPANY_EN) in text
        or STOCK in text
        or "1359HK" in text
    )
    broker_ok = any(compact(marker) in text for marker in broker_markers)
    if not company_ok:
        raise RuntimeError(f"Company identity not found in {path.name}")
    if not broker_ok:
        raise RuntimeError(f"Broker identity not found in {path.name}")
    return {
        "pages": pages,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "company_verified": company_ok,
        "broker_verified": broker_ok,
        "qpdf_status": qpdf.returncode,
    }


def main() -> None:
    reports = [
        {
            "sequence": 1,
            "date": "2025-02-20",
            "broker": "国信证券",
            "title": "中国信达（01359.HK）：受益经济复苏，业绩筑底",
            "filename": "01_Guosen_China_Cinda_Economic_Recovery_2025-02-20.pdf",
            "min_pages": 25,
            "expected_pages": 30,
            "broker_markers": ["国信证券", "GUOSEN"],
            "kind": "guosen",
        },
        {
            "sequence": 2,
            "date": "2014-11-26",
            "broker": "Phillip Securities / 辉立证券",
            "title": "China Cinda Asset Management Co., Ltd. - Monopolistic advantage in the industry with strong profit growth",
            "filename": "02_Phillip_China_Cinda_Monopolistic_Advantage_2014-11-26.pdf",
            "min_pages": 5,
            "expected_pages": None,
            "broker_markers": ["PHILLIP", "辉立", "輝立"],
            "kind": "phillip",
            "page_url": "https://www.poems.com.hk/en-us/market-information-and-toolbox/teletext/?codeval=992&num=2361&pagenum=18",
            "fallbacks": [
                "https://research.cyberquote.com.hk/page/htm/kc/researchnews/img/141126e.pdf",
                "http://research.cyberquote.com.hk/page/htm/kc/researchnews/img/141126e.pdf",
            ],
        },
        {
            "sequence": 3,
            "date": "2013-11-29",
            "broker": "Phillip Securities / 辉立证券",
            "title": "China Cinda Asset Management Co., Ltd. - Unique business model with obvious competitive advantages",
            "filename": "03_Phillip_China_Cinda_Unique_Business_Model_2013-11-29.pdf",
            "min_pages": 5,
            "expected_pages": None,
            "broker_markers": ["PHILLIP", "辉立", "輝立"],
            "kind": "phillip",
            "page_url": "https://www.poems.com.hk/en-us/research-and-analysis/research-report/?codeval=135&num=2129&pagenum=90",
            "fallbacks": [
                "https://research.cyberquote.com.hk/page/htm/kc/researchnews/img/131129e.pdf",
                "http://research.cyberquote.com.hk/page/htm/kc/researchnews/img/131129e.pdf",
            ],
        },
    ]

    records = []
    guosen_row = find_guosen_report()
    for report in reports:
        destination = OUT_DIR / report["filename"]
        if report["kind"] == "guosen":
            source = download_guosen(guosen_row, destination)
        else:
            source = download_phillip(report["page_url"], report["fallbacks"], destination)
        expected = report["expected_pages"]
        if report["kind"] == "guosen" and int(guosen_row.get("attachPages") or 0):
            expected = int(guosen_row.get("attachPages"))
        meta = validate_pdf(
            destination,
            broker_markers=report["broker_markers"],
            min_pages=report["min_pages"],
            expected_pages=expected,
        )
        record = {**report, **source, **meta}
        record.pop("fallbacks", None)
        record.pop("broker_markers", None)
        record.pop("kind", None)
        record.pop("expected_pages", None)
        record.pop("min_pages", None)
        records.append(record)
        print("VERIFIED", json.dumps(record, ensure_ascii=False, default=str), flush=True)

    if len(records) != 3:
        raise RuntimeError(f"Expected 3 reports, got {len(records)}")
    if len({record["sha256"] for record in records}) != 3:
        raise RuntimeError("Duplicate report content detected")

    manifest = OUT_DIR / "source_manifest.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "sequence", "date", "broker", "title", "filename", "pages", "bytes", "sha256",
            "company_verified", "broker_verified", "source_page", "source_pdf",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})

    readme = [
        "China Cinda Asset Management Co., Ltd. (01359.HK) broker research report package",
        f"Prepared: {TODAY}",
        "",
        "Selection: public, complete broker-authored company reports; login-only previews and short news summaries were excluded.",
        "The package includes one recent 30-page Guosen Securities deep report and two complete Phillip Securities company coverage reports.",
        "",
        "Files:",
    ]
    for record in records:
        readme.append(
            f"{record['sequence']}. {record['date']} | {record['broker']} | {record['title']} | {record['pages']} pages | SHA-256 {record['sha256']}"
        )
    readme += [
        "",
        "Validation: PDF signature, encryption status, actual page count, company identity, broker identity, qpdf structure, and first/last-page rendering were checked.",
        "See source_manifest.csv for source pages and PDF URLs.",
    ]
    (OUT_DIR / "README.txt").write_text("\n".join(readme), encoding="utf-8")

    for zip_path in (ZIP_SHORT, ZIP_LONG):
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(OUT_DIR.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path))
        with zipfile.ZipFile(zip_path, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f"ZIP CRC failure: {bad}")

    package_hash = sha256(ZIP_SHORT)
    Path("PACKAGE_SHA256.txt").write_text(f"{package_hash}  {ZIP_SHORT.name}\n", encoding="utf-8")
    print("PACKAGE READY", ZIP_SHORT, ZIP_SHORT.stat().st_size, package_hash, flush=True)


if __name__ == "__main__":
    main()
