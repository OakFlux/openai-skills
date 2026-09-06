#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
OUT = Path("Kerry_Properties_Broker_Reports")
WORK = Path("_work_kerry")
PREVIEW = WORK / "preview"
ZIP_SHORT = Path("Kerry_Properties_Reports.zip")
ZIP_FULL = Path("Kerry_Properties_Broker_Reports.zip")
OUT.mkdir(exist_ok=True)
PREVIEW.mkdir(parents=True, exist_ok=True)

REPORTS = [
    {
        "sequence": 1,
        "date": "2017-10-10",
        "broker": "Phillip Securities (Hong Kong) / 辉立证券",
        "title": "Kerry Properties (683.HK) - Sustainable Dividend Level Given Stable Recurring Income",
        "page_url": "https://www.poems.com.hk/en-us/research-and-analysis/research-report/?codeval=175&num=3171",
        "filename": "01_Phillip_Securities_Kerry_Properties_Sustainable_Dividend_2017-10-10.pdf",
        "fallback_pdf": "https://research.cyberquote.com.hk/page/htm/kc/researchnews/img/171009e.pdf",
    },
    {
        "sequence": 2,
        "date": "2017-02-02",
        "broker": "Phillip Securities (Hong Kong) / 辉立证券",
        "title": "Kerry Properties (683.HK) - Gradual Shift to Property Development in China",
        "page_url": "https://www.poems.com.hk/en-us/research-and-analysis/research-report/?codeval=683&num=2997",
        "filename": "02_Phillip_Securities_Kerry_Properties_China_Property_Development_2017-02-02.pdf",
        "fallback_pdf": "https://research.cyberquote.com.hk/page/htm/kc/researchnews/img/170202e.pdf",
    },
]

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.7",
})


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_pdf_candidates(page_url: str) -> tuple[str, list[str]]:
    r = session.get(page_url, timeout=(30, 180), allow_redirects=True)
    print("PAGE", r.status_code, r.url, len(r.content), flush=True)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    candidates: list[str] = []
    for tag in soup.find_all(True):
        for attr in ("href", "src", "data-url", "data-file", "data-pdf", "data-download"):
            value = tag.get(attr)
            if not isinstance(value, str):
                continue
            value = value.strip().replace("\\/", "/")
            if ".pdf" not in value.lower():
                continue
            u = urljoin(r.url, value)
            if "research" in u.lower() or "cyberquote" in u.lower():
                if u not in candidates:
                    candidates.append(u)
    for value in re.findall(r"(?:https?:)?//[^\s\"'<>\\]+\.pdf(?:\?[^\s\"'<>\\]*)?", r.text, flags=re.I):
        u = value if value.startswith("http") else "https:" + value
        u = u.rstrip("),]};'\"")
        if u not in candidates:
            candidates.append(u)
    print("CANDIDATES", page_url, candidates, flush=True)
    return r.url, candidates


def fetch_pdf(urls: list[str], referer: str) -> tuple[bytes, str, str]:
    errors = []
    normalized = []
    for u in urls:
        if u.startswith("http://"):
            normalized.extend([u.replace("http://", "https://", 1), u])
        else:
            normalized.append(u)
    normalized = list(dict.fromkeys(normalized))
    for attempt in range(1, 4):
        for url in normalized:
            try:
                r = session.get(
                    url,
                    headers={
                        "User-Agent": UA,
                        "Referer": referer,
                        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                    },
                    timeout=(30, 300),
                    allow_redirects=True,
                )
                print("PDF", r.status_code, r.headers.get("content-type"), len(r.content), url, "->", r.url, flush=True)
                if r.status_code == 200 and r.content.startswith(b"%PDF-"):
                    return r.content, url, r.url
                errors.append(f"{url}: {r.status_code}/{len(r.content)}")
            except Exception as exc:
                errors.append(f"{url}: {exc!r}")
        time.sleep(attempt)
    raise RuntimeError("PDF download failed: " + "; ".join(errors[-12:]))


def extract_text(reader: PdfReader, max_pages: int = 20) -> str:
    chunks = []
    pages = len(reader.pages)
    indexes = list(range(min(max_pages, pages)))
    if pages > max_pages:
        indexes.extend(range(max(max_pages, pages - 3), pages))
    for idx in sorted(set(indexes)):
        try:
            chunks.append(reader.pages[idx].extract_text() or "")
        except Exception as exc:
            print("TEXT WARNING", idx, repr(exc), flush=True)
    return "\n".join(chunks)


def validate_pdf(path: Path, report: dict) -> dict:
    with path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise RuntimeError("Missing PDF signature")
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError("Encrypted PDF") from exc
    pages = len(reader.pages)
    if pages < 3:
        raise RuntimeError(f"Only {pages} pages")
    text = extract_text(reader)
    norm = re.sub(r"\s+", "", text).upper()
    company_ok = any(x in norm for x in ("KERRYPROPERTIES", "嘉里建設", "嘉里建设", "683.HK", "00683"))
    broker_ok = any(x in norm for x in ("PHILLIPSECURITIES", "PHILLIP", "輝立", "辉立"))
    if not company_ok:
        raise RuntimeError("Kerry Properties identity not found")
    if not broker_ok:
        raise RuntimeError("Phillip Securities identity not found")
    check = subprocess.run(["qpdf", "--check", str(path)], capture_output=True, text=True)
    if check.returncode not in (0, 3):
        raise RuntimeError("qpdf validation failed: " + check.stderr[-500:])
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
    return {
        "pages": pages,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "company_verified": company_ok,
        "broker_verified": broker_ok,
        "qpdf_status": check.returncode,
    }


def main() -> None:
    records = []
    for report in REPORTS:
        resolved_page, candidates = extract_pdf_candidates(report["page_url"])
        urls = candidates + [report["fallback_pdf"]]
        data, requested_pdf, resolved_pdf = fetch_pdf(urls, resolved_page)
        temp = WORK / f"{report['sequence']:02d}.pdf"
        temp.parent.mkdir(exist_ok=True)
        temp.write_bytes(data)
        meta = validate_pdf(temp, report)
        dest = OUT / report["filename"]
        shutil.copy2(temp, dest)
        record = {
            **report,
            "resolved_page_url": resolved_page,
            "requested_pdf_url": requested_pdf,
            "resolved_pdf_url": resolved_pdf,
            **meta,
        }
        records.append(record)
        print("VERIFIED", record, flush=True)

    if len(records) != 2 or len({r["sha256"] for r in records}) != 2:
        raise RuntimeError("Report count or uniqueness validation failed")

    manifest = OUT / "source_manifest.csv"
    fields = [
        "sequence", "date", "broker", "title", "filename", "pages", "bytes", "sha256",
        "company_verified", "broker_verified", "page_url", "resolved_page_url",
        "requested_pdf_url", "resolved_pdf_url",
    ]
    with manifest.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k, "") for k in fields})

    lines = [
        "Kerry Properties Limited (00683.HK) - Broker Research Reports",
        "Prepared: 2026-09-06",
        "",
        "This package contains two complete company research reports published by Phillip Securities (Hong Kong).",
        "Recent 2025-2026 deep reports found in public indexes were not included because their full PDFs required login/payment or only preview pages were publicly accessible.",
        "",
        "Files:",
    ]
    for r in records:
        lines.append(f"{r['sequence']}. {r['date']} | {r['title']} | {r['pages']} pages | SHA-256: {r['sha256']}")
    lines += [
        "",
        "Validation: PDF signature, encryption status, actual page count, company/broker identity, qpdf structure, and first/last-page rendering.",
        "See source_manifest.csv for official report pages and original PDF URLs.",
    ]
    (OUT / "README.txt").write_text("\n".join(lines), encoding="utf-8")

    for zip_path in (ZIP_SHORT, ZIP_FULL):
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(OUT.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=str(path))
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"ZIP CRC failure: {bad}")
    package_hash = sha256(ZIP_SHORT)
    Path("PACKAGE_SHA256.txt").write_text(f"{package_hash}  {ZIP_SHORT.name}\n", encoding="utf-8")
    print("PACKAGE READY", ZIP_SHORT, ZIP_SHORT.stat().st_size, package_hash, flush=True)


if __name__ == "__main__":
    main()
