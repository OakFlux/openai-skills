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
OUT_DIR = Path("China_Cinda_Broker_Reports")
WORK_DIR = Path("_work_china_cinda_phillip")
PREVIEW_DIR = WORK_DIR / "preview"
ZIP_SHORT = Path("China_Cinda_Reports.zip")
ZIP_BACKUP = Path("China_Cinda_Broker_Reports.zip")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/152.0.0.0 Safari/537.36"

OUT_DIR.mkdir(exist_ok=True)
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})

REPORTS = [
    {
        "sequence": 1,
        "date": "2014-11-26",
        "broker": "Phillip Securities (Hong Kong) / 辉立证券",
        "title": "China Cinda Asset Management Co., Ltd. - Monopolistic advantage in the industry with strong profit growth",
        "filename": "01_Phillip_China_Cinda_Strong_Profit_Growth_2014-11-26.pdf",
        "page_urls": [
            "https://www.poems.com.hk/en-us/market-information-and-toolbox/teletext/?codeval=992&num=2361&pagenum=18",
            "https://www.poems.com.hk/en-us/research-and-analysis/research-report/?id=2361",
            "https://www.poems.com.hk/en-us/research-and-analysis/research-report/?codeval=992&num=2361&pagenum=18",
        ],
        "fallback_pdfs": [
            "https://research.cyberquote.com.hk/page/htm/kc/researchnews/img/141126e.pdf",
            "http://research.cyberquote.com.hk/page/htm/kc/researchnews/img/141126e.pdf",
        ],
        "min_pages": 5,
    },
    {
        "sequence": 2,
        "date": "2013-11-29",
        "broker": "Phillip Securities (Hong Kong) / 辉立证券",
        "title": "China Cinda Asset Management Co., Ltd. - Unique business model with obvious competitive advantages",
        "filename": "02_Phillip_China_Cinda_Unique_Business_Model_2013-11-29.pdf",
        "page_urls": [
            "https://www.poems.com.hk/en-us/research-and-analysis/research-report/?codeval=135&num=2129&pagenum=90",
            "https://www.poems.com.hk/en-us/research-and-analysis/research-report/?id=2129",
        ],
        "fallback_pdfs": [
            "https://research.cyberquote.com.hk/page/htm/kc/researchnews/img/131129e.pdf",
            "http://research.cyberquote.com.hk/page/htm/kc/researchnews/img/131129e.pdf",
        ],
        "min_pages": 5,
    },
]


def compact(value: object) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).upper()
    return re.sub(r"[^0-9A-Z\u4e00-\u9fff]+", "", text)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, referer: str | None = None, timeout: int = 600) -> requests.Response:
    headers = {
        "User-Agent": UA,
        "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
        "Referer": referer or "https://www.poems.com.hk/",
    }
    last_error = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, headers=headers, timeout=(30, timeout), allow_redirects=True)
            print(
                "GET", response.status_code, response.headers.get("content-type"),
                len(response.content), url, "->", response.url,
                flush=True,
            )
            return response
        except Exception as exc:
            last_error = exc
            print("GET ERROR", url, repr(exc), flush=True)
            time.sleep(attempt * 2)
    raise RuntimeError(f"Failed to fetch {url}: {last_error!r}")


def decode_page(response: requests.Response) -> str:
    candidates = []
    for encoding in (response.encoding, response.apparent_encoding, "utf-8", "gb18030", "big5"):
        if not encoding:
            continue
        try:
            text = response.content.decode(encoding)
            candidates.append((text.count("�"), -len(text), text))
        except Exception:
            pass
    if not candidates:
        return response.content.decode("utf-8", errors="replace")
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def extract_pdf_links(page_url: str) -> tuple[requests.Response, list[str]]:
    response = fetch(page_url)
    if response.status_code != 200:
        return response, []
    if response.content.startswith(b"%PDF-"):
        return response, [response.url]
    text = decode_page(response)
    soup = BeautifulSoup(text, "html.parser")
    links = []
    for tag in soup.find_all(True):
        for attr in ("href", "src", "data-url", "data-src", "content"):
            value = tag.get(attr)
            if isinstance(value, str) and ".pdf" in value.lower():
                links.append(urljoin(response.url, html.unescape(value.strip())))
    for value in re.findall(r"(?:https?:)?//[^\s\"'<>]+\.pdf(?:\?[^\s\"'<>]*)?", text, flags=re.I):
        links.append(urljoin(response.url, html.unescape(value)))
    deduplicated = []
    seen = set()
    for link in links:
        link = link.rstrip("),]};'\"")
        lower = link.lower()
        if any(token in lower for token in ("pspl_tc", "advisorynotes", "terms", "privacy")):
            continue
        if link not in seen:
            seen.add(link)
            deduplicated.append(link)
    print("PDF LINKS", page_url, deduplicated, flush=True)
    return response, deduplicated


def download_report(report: dict, destination: Path) -> dict:
    source_page = ""
    resolved_page = ""
    candidates = []
    for page_url in report["page_urls"]:
        try:
            page_response, links = extract_pdf_links(page_url)
            if page_response.status_code == 200:
                source_page = source_page or page_url
                resolved_page = resolved_page or page_response.url
                candidates.extend(links)
        except Exception as exc:
            print("PAGE ERROR", page_url, repr(exc), flush=True)
    candidates.extend(report["fallback_pdfs"])
    candidates = list(dict.fromkeys(candidates))
    errors = []
    for url in candidates:
        try:
            response = fetch(url, referer=resolved_page or source_page)
            if response.status_code == 200 and response.content.startswith(b"%PDF-"):
                destination.write_bytes(response.content)
                return {
                    "source_page": source_page,
                    "resolved_page": resolved_page,
                    "source_pdf": response.url,
                }
            errors.append(f"{url}: {response.status_code}/{len(response.content)}")
        except Exception as exc:
            errors.append(f"{url}: {exc!r}")
    raise RuntimeError("No complete PDF downloaded: " + "; ".join(errors[-12:]))


def extract_text(path: Path, pages: int) -> str:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            pass
    chunks = []
    for index in range(min(pages, 30)):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception as exc:
            print("TEXT WARNING", path.name, index, repr(exc), flush=True)
    text = "\n".join(chunks)
    if len(compact(text)) < 400:
        process = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(min(pages, 40)), str(path), "-"],
            capture_output=True,
            check=False,
        )
        text += "\n" + process.stdout.decode("utf-8", errors="ignore")
    return text


def validate(path: Path, report: dict) -> dict:
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
    if pages < report["min_pages"]:
        raise RuntimeError(f"Only {pages} pages in {path.name}")
    qpdf = subprocess.run(["qpdf", "--check", str(path)], capture_output=True, text=True)
    if qpdf.returncode not in (0, 3):
        raise RuntimeError("qpdf failed: " + qpdf.stderr[-500:])
    for label, page_number in (("first", 1), ("last", pages)):
        prefix = PREVIEW_DIR / f"{path.stem}_{label}"
        subprocess.run(
            [
                "pdftoppm", "-f", str(page_number), "-l", str(page_number),
                "-singlefile", "-png", "-r", "72", str(path), str(prefix),
            ],
            check=True,
            capture_output=True,
        )
        rendered = Path(str(prefix) + ".png")
        if not rendered.exists() or rendered.stat().st_size < 1_000:
            raise RuntimeError(f"Render test failed: {path.name} {label}")
    text = compact(extract_text(path, pages))
    company_ok = any(marker in text for marker in (
        compact("China Cinda"), compact("China Cinda Asset Management"),
        compact("中国信达"), "1359HK", "01359",
    ))
    broker_ok = any(marker in text for marker in (
        compact("Phillip Securities"), compact("Phillip"), compact("辉立"), compact("輝立"),
    ))
    if not company_ok:
        raise RuntimeError(f"China Cinda identity not found in {path.name}")
    if not broker_ok:
        raise RuntimeError(f"Phillip Securities identity not found in {path.name}")
    return {
        "pages": pages,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "company_verified": company_ok,
        "broker_verified": broker_ok,
        "qpdf_status": qpdf.returncode,
    }


def main() -> None:
    records = []
    for report in REPORTS:
        destination = OUT_DIR / report["filename"]
        source = download_report(report, destination)
        verification = validate(destination, report)
        record = {
            "sequence": report["sequence"],
            "date": report["date"],
            "broker": report["broker"],
            "title": report["title"],
            "filename": report["filename"],
            **source,
            **verification,
        }
        records.append(record)
        print("VERIFIED", json.dumps(record, ensure_ascii=False), flush=True)

    if len(records) != 2:
        raise RuntimeError(f"Expected 2 reports, got {len(records)}")
    if len({record["sha256"] for record in records}) != 2:
        raise RuntimeError("Duplicate PDF content detected")

    manifest = OUT_DIR / "source_manifest.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "sequence", "date", "broker", "title", "filename", "pages", "bytes", "sha256",
            "company_verified", "broker_verified", "source_page", "resolved_page", "source_pdf",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})

    readme = [
        "China Cinda Asset Management Co., Ltd. (01359.HK) broker research reports",
        f"Prepared: {TODAY}",
        "",
        "This package contains two complete broker-authored company coverage reports obtained through Phillip Securities' public research archive.",
        "A more recent 30-page Guosen Securities deep report was identified, but publicly indexed copies required login or provided only previews; it was excluded rather than represented as a complete report.",
        "",
        "Files:",
    ]
    for record in records:
        readme.append(
            f"{record['sequence']}. {record['date']} | {record['broker']} | {record['title']} | "
            f"{record['pages']} pages | SHA-256 {record['sha256']}"
        )
    readme += [
        "",
        "Validation: PDF signature, encryption status, actual page count, company identity, broker identity, qpdf structure, and first/last-page rendering were checked.",
        "See source_manifest.csv for source pages and resolved PDF URLs.",
    ]
    (OUT_DIR / "README.txt").write_text("\n".join(readme), encoding="utf-8")

    for zip_path in (ZIP_SHORT, ZIP_BACKUP):
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
