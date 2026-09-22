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

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})

TODAY = "2026-09-22"
IR_API_KEY = "2110bee3-6b4c-4be3-8604-f49922215a82"


def get(url, *, timeout=120, headers=None):
    last = None
    merged = dict(session.headers)
    if headers:
        merged.update(headers)
    for attempt in range(5):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True, headers=merged)
            if response.status_code == 200:
                return response
            last = RuntimeError(f"HTTP {response.status_code} for {response.url}")
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2 ** attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def pdf_info(data, temp_name, min_pages=1):
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"Not a PDF: head={data[:32]!r}")
    if len(data) < 50_000:
        raise RuntimeError(f"PDF too small: {len(data)} bytes")
    temp_path = DEBUG / temp_name
    temp_path.write_bytes(data)
    reader = PdfReader(str(temp_path))
    encrypted = bool(reader.is_encrypted)
    if encrypted:
        try:
            result = reader.decrypt("")
        except Exception as exc:
            raise RuntimeError(f"Encrypted PDF cannot be opened: {exc}") from exc
        if not result:
            raise RuntimeError("Password-protected PDF")
    pages = len(reader.pages)
    if pages < min_pages:
        raise RuntimeError(f"Unexpectedly short PDF: {pages} pages")
    first_text = ""
    try:
        first_text = " ".join((reader.pages[0].extract_text() or "").split())[:1200]
    except Exception:
        pass
    return {
        "pages": pages,
        "encrypted_flag": encrypted,
        "first_page_text_excerpt": first_text,
    }


def download_doc(filename, title, category, url, min_pages):
    response = get(url)
    data = response.content
    info = pdf_info(data, filename.replace(".pdf", "_check.pdf"), min_pages=min_pages)
    path = OUT / filename
    path.write_bytes(data)
    record = {
        "filename": filename,
        "title": title,
        "category": category,
        "source_url": url,
        "final_url": response.url,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        **info,
    }
    print("VALIDATED", json.dumps(record, ensure_ascii=False))
    return path, record


def collect_pdf_candidates_from_html(url):
    candidates = []
    try:
        response = get(url, timeout=60)
    except Exception as exc:
        print("HTML_DISCOVERY_FAILED", url, repr(exc))
        return candidates
    (DEBUG / (re.sub(r"[^A-Za-z0-9]+", "_", url)[:120] + ".html")).write_bytes(response.content)
    soup = BeautifulSoup(response.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = urljoin(response.url, anchor["href"])
        context_node = anchor.find_parent("li") or anchor.find_parent("tr") or anchor.parent
        context = " ".join((context_node.get_text(" ", strip=True) if context_node else anchor.get_text(" ", strip=True)).split())
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if ".pdf" in href.lower():
            candidates.append({"url": href, "title": label or context, "context": context, "source": response.url})
    return candidates


def collect_pdf_candidates_from_json(obj, context=""):
    candidates = []
    if isinstance(obj, dict):
        local_text = " ".join(str(v) for k, v in obj.items() if isinstance(v, (str, int, float)) and k.lower() not in {"url", "link", "href", "file", "filelink"})
        combined = (context + " " + local_text).strip()
        for key, value in obj.items():
            if isinstance(value, str) and ".pdf" in value.lower():
                candidates.append({"url": value, "title": combined, "context": combined, "source": "IR API"})
            else:
                candidates.extend(collect_pdf_candidates_from_json(value, combined))
    elif isinstance(obj, list):
        for item in obj:
            candidates.extend(collect_pdf_candidates_from_json(item, context))
    elif isinstance(obj, str) and ".pdf" in obj.lower():
        candidates.append({"url": obj, "title": context, "context": context, "source": "IR API"})
    return candidates


def discover_latest_periodic_report():
    candidates = []

    # Most likely official company-IR file paths. Prefer a full interim report.
    direct = [
        {"url": "https://doc.irasia.com/listco/hk/zjld/interim/2026/intrepc.pdf", "title": "2026中期报告", "source": "company IR predictable path"},
        {"url": "https://doc.irasia.com/listco/hk/zjld/interim/2026/intrep.pdf", "title": "2026 Interim Report", "source": "company IR predictable path"},
        {"url": "https://doc.irasia.com/listco/hk/zjld/announcement/ca260819.pdf", "title": "截至2026年6月30日止六个月中期业绩公告", "source": "company IR predictable path"},
    ]
    candidates.extend(direct)

    # Live company investor-relations pages.
    for page in [
        "https://www.zjld.com/investor/announcement?sid=2026",
        "https://www.zjld.com/investor/announcement",
        "https://www.zjld.com/investor/announcement?sid=0&t=5",
    ]:
        candidates.extend(collect_pdf_candidates_from_html(page))

    # Live IR synchronization APIs.
    for kind in ["aclr", "t11t12t13"]:
        api = f"https://sync.irasia.com/api/sync/2.5/zjld/sc/byType/{kind}/19900101-20260922?apikey={IR_API_KEY}"
        try:
            response = get(api, timeout=60)
            (DEBUG / f"irasia_{kind}.json").write_bytes(response.content)
            try:
                obj = response.json()
            except Exception:
                obj = json.loads(response.text)
            candidates.extend(collect_pdf_candidates_from_json(obj))
        except Exception as exc:
            print("IR_API_FAILED", api, repr(exc))

    # Public filing mirror, used only to identify the official original filing if necessary.
    candidates.extend(collect_pdf_candidates_from_html(
        "https://financialfilings.com/filings/zjld-group-inc/interim-quarterly-report/2026/56578537/"
    ))

    # Normalize and score candidates.
    normalized = []
    seen = set()
    for item in candidates:
        url = str(item.get("url", "")).strip().replace("http://doc.irasia.com/", "https://doc.irasia.com/")
        if not url or ".pdf" not in url.lower():
            continue
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("/"):
            url = urljoin("https://www.zjld.com/", url)
        if url in seen:
            continue
        seen.add(url)
        text = " ".join([str(item.get("title", "")), str(item.get("context", "")), url])
        upper = text.upper()
        if "2026" not in upper and "260819" not in upper:
            continue
        if not any(token in upper for token in ["中期", "INTERIM", "SIX MONTH", "260819"]):
            continue
        score = 0
        if "中期报告" in text or "中期報告" in text or "INTERIM REPORT" in upper:
            score += 100
        if "/interim/2026/" in url.lower():
            score += 80
        if "中期业绩" in text or "中期業績" in text or "INTERIM RESULTS" in upper:
            score += 40
        if "announcement" in url.lower():
            score += 15
        if "doc.irasia.com" in url.lower() or "hkexnews.hk" in url.lower():
            score += 20
        normalized.append({**item, "url": url, "score": score, "combined_text": text})

    normalized.sort(key=lambda x: x["score"], reverse=True)
    (DEBUG / "latest_candidates.json").write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")

    errors = []
    for index, item in enumerate(normalized, 1):
        try:
            print("TRY_LATEST", item["score"], item["url"], item.get("title", ""))
            response = get(item["url"], timeout=120)
            data = response.content
            info = pdf_info(data, f"latest_candidate_{index}.pdf", min_pages=5)
            first_upper = info["first_page_text_excerpt"].upper()
            combined_upper = item["combined_text"].upper()
            if not any(token in first_upper + " " + combined_upper for token in ["ZJLD", "珍酒李渡", "6979"]):
                raise RuntimeError("Candidate does not appear to be ZJLD")
            is_full = (
                info["pages"] >= 40
                or "/interim/2026/" in item["url"].lower()
                or "INTERIM REPORT" in first_upper
                or "中期報告" in info["first_page_text_excerpt"]
            )
            if is_full:
                filename = "05_珍酒李渡_2026年中期报告_最新定期财报.pdf"
                title = "珍酒李渡集团有限公司 - 2026年中期报告"
                category = "最新定期财报"
            else:
                filename = "05_珍酒李渡_2026年中期业绩公告_最新财务披露.pdf"
                title = "珍酒李渡集团有限公司 - 截至2026年6月30日止六个月中期业绩公告"
                category = "最新财务披露"
            path = OUT / filename
            path.write_bytes(data)
            record = {
                "filename": filename,
                "title": title,
                "category": category,
                "source_url": item["url"],
                "final_url": response.url,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                **info,
            }
            print("LATEST_SELECTED", json.dumps(record, ensure_ascii=False))
            return path, record, is_full
        except Exception as exc:
            errors.append({"url": item["url"], "error": repr(exc)})
            print("LATEST_FAILED", item["url"], repr(exc))

    raise RuntimeError("No valid 2026 interim filing found: " + json.dumps(errors, ensure_ascii=False))


def main():
    documents = []
    fixed_docs = [
        (
            "01_珍酒李渡_2023年年报.pdf",
            "珍酒李渡集团有限公司 - 2023年年度报告",
            "2023年年报",
            "https://doc.irasia.com/listco/hk/zjld/annual/2023/car2023.pdf",
            80,
        ),
        (
            "02_珍酒李渡_2024年年报.pdf",
            "珍酒李渡集团有限公司 - 2024年年度报告",
            "2024年年报",
            "https://doc.irasia.com/listco/hk/zjld/annual/2024/car2024.pdf",
            80,
        ),
        (
            "03_珍酒李渡_2025年年报.pdf",
            "珍酒李渡集团有限公司 - 2025年年度报告",
            "2025年年报",
            "https://doc.irasia.com/listco/hk/zjld/annual/2025/car2025.pdf",
            80,
        ),
        (
            "04_珍酒李渡_招股说明书_2023-04-17.pdf",
            "珍酒李渡集团有限公司 - 全球发售（最终版招股说明书）",
            "招股说明书",
            "https://doc.irasia.com/listco/hk/zjld/listingdoc/cl230417.pdf",
            300,
        ),
    ]

    for spec in fixed_docs:
        documents.append(download_doc(*spec))

    latest_path, latest_record, latest_is_full = discover_latest_periodic_report()
    documents.append((latest_path, latest_record))

    manifest = [record for _, record in documents]
    latest_note = (
        "已收录2026年完整中期报告，作为截至2026年9月22日最新正式定期财报。"
        if latest_is_full
        else "截至2026年9月22日尚未检索到已发布的2026年完整中期报告；因此收录2026年中期业绩公告作为最新财务披露。"
    )
    note = f"""珍酒李渡集团有限公司（06979.HK）官方披露文件资料包

文件范围：
1. 2023年年度报告。
2. 2024年年度报告。
3. 2025年年度报告。
4. 2023年4月17日最终版招股说明书（全球发售）。
5. {latest_record['title']}。

说明：
- 公司于2023年4月27日在香港联合交易所主板上市，上市以来正式年度报告为2023、2024及2025年度报告。
- {latest_note}
- 香港主板发行人通常披露年度报告和中期报告，并不强制发布季度报告；本资料包以最新中期文件对应用户所称“最新季报”。
- 所有PDF均来自公司投资者关系网站、其官方文件服务器或港交所原始披露文件；文件清单保留来源URL及SHA-256校验值。
- 仅供个人研究与学习使用，请遵守原始文件的版权及免责声明。
"""
    (OUT / "资料说明.txt").write_text(note, encoding="utf-8")
    (OUT / "文件清单及校验值.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = OUT / "珍酒李渡_全部年报_招股说明书_最新定期财报.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, _ in documents:
            archive.write(path, path.name)
        archive.write(OUT / "资料说明.txt", "资料说明.txt")
        archive.write(OUT / "文件清单及校验值.json", "文件清单及校验值.json")

    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC validation failed at {bad}")

    summary = {
        "zip": zip_path.name,
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "total_pdf_pages": sum(item["pages"] for item in manifest),
        "reports": manifest,
    }
    (OUT / "BUILD_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
