#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

OUT = Path("output")
DEBUG = OUT / "debug"
OUT.mkdir(parents=True, exist_ok=True)
DEBUG.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def fetch(url: str, timeout: int = 60) -> requests.Response:
    last_error = None
    for attempt in range(4):
        try:
            response = SESSION.get(url, timeout=timeout, allow_redirects=True)
            if response.status_code == 200:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {url}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(2**attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def is_health160_2026_interim(data: bytes, temp_name: str = "candidate.pdf") -> bool:
    if not data.startswith(b"%PDF-") or len(data) < 100_000:
        return False
    path = DEBUG / temp_name
    path.write_bytes(data)
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted or len(reader.pages) < 10:
            return False
        first_text = " ".join((reader.pages[0].extract_text() or "").split()).upper()
        return "160 HEALTH" in first_text and "2026" in first_text and "INTERIM" in first_text
    except Exception:  # noqa: BLE001
        return False


def discover_2026_interim_url() -> str:
    search_urls = [
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId=1000272538",
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=ZH&market=SEHK&stockId=1000272538",
        "https://www.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId=1000272538",
    ]
    candidates = []
    for index, search_url in enumerate(search_urls, start=1):
        try:
            response = fetch(search_url, timeout=45)
        except Exception as exc:  # noqa: BLE001
            print("TITLE_SEARCH_FAILED", search_url, repr(exc), flush=True)
            continue
        (DEBUG / f"title_search_{index}.html").write_bytes(response.content)
        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = urljoin(response.url, anchor["href"])
            if ".pdf" not in href.lower():
                continue
            row = anchor.find_parent("tr") or anchor.parent
            context = " ".join((row.get_text(" ", strip=True) if row else anchor.get_text(" ", strip=True)).split())
            normalized = context.upper()
            if "2026" in normalized and (
                "INTERIM REPORT" in normalized or "中期報告" in context or "中期报告" in context
            ):
                candidates.append(href)

        # Regex fallback for pages with non-standard markup.
        for match in re.finditer(r"href=[\"']([^\"']+\.pdf[^\"']*)[\"']", response.text, re.I):
            href = urljoin(response.url, match.group(1))
            context = re.sub(
                r"<[^>]+>",
                " ",
                response.text[max(0, match.start() - 700) : min(len(response.text), match.end() + 700)],
            )
            context = " ".join(context.split())
            normalized = context.upper()
            if "2026" in normalized and (
                "INTERIM REPORT" in normalized or "中期報告" in context or "中期报告" in context
            ):
                candidates.append(href)

    for index, url in enumerate(dict.fromkeys(candidates), start=1):
        print("INTERIM_CANDIDATE", url, flush=True)
        try:
            response = fetch(url, timeout=90)
            if is_health160_2026_interim(response.content, f"interim_candidate_{index}.pdf"):
                return response.url
        except Exception as exc:  # noqa: BLE001
            print("INTERIM_CANDIDATE_FAILED", url, repr(exc), flush=True)

    # Public filing index fallback. The downloaded file itself is validated before use.
    mirror_url = "https://financialfilings.com/filings/160-health-international-limited/interim-quarterly-report/2026/60033277/"
    try:
        response = fetch(mirror_url, timeout=45)
        (DEBUG / "financialfilings.html").write_bytes(response.content)
        soup = BeautifulSoup(response.text, "html.parser")
        mirror_candidates = []
        for anchor in soup.find_all("a", href=True):
            href = urljoin(response.url, anchor["href"])
            if ".pdf" in href.lower():
                mirror_candidates.append(href)
        mirror_candidates.extend(re.findall(r"https?://[^\"'<> ]+\.pdf(?:\?[^\"'<> ]*)?", response.text, re.I))
        for index, url in enumerate(dict.fromkeys(mirror_candidates), start=1):
            try:
                candidate = fetch(url, timeout=90)
                if is_health160_2026_interim(candidate.content, f"mirror_candidate_{index}.pdf"):
                    return candidate.url
            except Exception as exc:  # noqa: BLE001
                print("MIRROR_CANDIDATE_FAILED", url, repr(exc), flush=True)
    except Exception as exc:  # noqa: BLE001
        print("MIRROR_INDEX_FAILED", repr(exc), flush=True)

    raise RuntimeError("Unable to locate a validated 2026 interim report PDF")


def validate_pdf(path: Path, expected_name: str) -> dict:
    data = path.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"Not a PDF: {path.name}")
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise RuntimeError(f"Encrypted PDF: {path.name}")
    pages = len(reader.pages)
    if pages < 10:
        raise RuntimeError(f"Unexpectedly short PDF: {path.name}, pages={pages}")
    first_text = ""
    try:
        first_text = " ".join((reader.pages[0].extract_text() or "").split())[:1200]
    except Exception:  # noqa: BLE001
        pass
    if "160" not in first_text and "健康" not in first_text and expected_name not in first_text:
        print("FIRST_PAGE_TEXT_WARNING", path.name, first_text[:300], flush=True)
    return {
        "bytes": len(data),
        "pages": pages,
        "sha256": hashlib.sha256(data).hexdigest(),
        "first_page_text_excerpt": first_text,
    }


def main() -> None:
    latest_interim_url = discover_2026_interim_url()
    print("LATEST_INTERIM_URL", latest_interim_url, flush=True)

    documents = [
        {
            "filename": "01_健康160_招股说明书_2025-09-09.pdf",
            "title": "健康160国际有限公司 - 全球发售（最终版招股说明书）",
            "date": "2025-09-09",
            "category": "招股说明书",
            "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0909/2025090900019.pdf",
        },
        {
            "filename": "02_健康160_2025年中期报告.pdf",
            "title": "健康160国际有限公司 - 2025年中期报告",
            "date": "2025-09-29",
            "category": "中期报告",
            "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0929/2025092903164.pdf",
        },
        {
            "filename": "03_健康160_2025年年报.pdf",
            "title": "健康160国际有限公司 - 2025年年报",
            "date": "2026-04-14",
            "category": "年报",
            "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0414/2026041400418.pdf",
        },
        {
            "filename": "04_健康160_2026年中期报告_最新定期财报.pdf",
            "title": "健康160国际有限公司 - 2026年中期报告",
            "date": "2026-09-15",
            "category": "最新定期财报",
            "url": latest_interim_url,
        },
    ]

    manifest = []
    pdf_paths = []
    for document in documents:
        print("DOWNLOADING", document["url"], flush=True)
        response = fetch(document["url"], timeout=120)
        data = response.content
        if not data.startswith(b"%PDF-") or len(data) < 50_000:
            raise RuntimeError(
                f"Invalid PDF response: {document['url']}, status={response.status_code}, bytes={len(data)}, head={data[:24]!r}"
            )
        path = OUT / document["filename"]
        path.write_bytes(data)
        validation = validate_pdf(path, "160 HEALTH")
        record = {**document, "final_url": response.url, **validation}
        manifest.append(record)
        pdf_paths.append(path)
        print("VALIDATED", json.dumps(record, ensure_ascii=False), flush=True)

    note = """健康160国际有限公司（02656.HK）官方披露文件资料包

文件范围：
1. 2025年9月9日最终版招股说明书（全球发售）。
2. 2025年中期报告。
3. 2025年年报。健康160于2025年9月17日上市，因此截至2026年9月22日，上市后正式年报仅此一份。
4. 2026年中期报告，为截至2026年9月22日最新正式定期财务报告。

说明：
- 香港主板公司以年度报告和中期报告为主要定期报告，港交所披露记录中没有健康160的季度报告。因此本资料包以2026年中期报告作为“最新季报/最新定期财报”的对应文件。
- PDF优先从港交所披露系统下载；每份文件均经过PDF文件头、页数、加密状态和SHA-256校验。
- 文件仅供个人研究与学习使用，请遵守原始文件的版权及免责声明。
"""
    (OUT / "资料说明.txt").write_text(note, encoding="utf-8")
    (OUT / "文件清单及校验值.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    zip_path = OUT / "健康160_年报_招股说明书_最新定期财报.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in pdf_paths:
            archive.write(path, path.name)
        archive.write(OUT / "资料说明.txt", "资料说明.txt")
        archive.write(OUT / "文件清单及校验值.json", "文件清单及校验值.json")
    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"ZIP CRC validation failed: {bad_member}")

    summary = {
        "zip": zip_path.name,
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "reports": manifest,
    }
    (OUT / "BUILD_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
