#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import img2pdf
import requests
from PIL import Image
from pypdf import PdfReader

STOCK_CODE = "002469"
COMPANY = "山东三维化学集团股份有限公司"
SHORT_NAME = "三维化学"
OUT = Path("sanwei_chemical_broker_reports")
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        "Connection": "keep-alive",
    }
)

ORIGINAL_REPORT = {
    "sequence": 1,
    "broker": "国信证券",
    "report_date": "2025-05-16",
    "title": "受益于新疆煤化工建设提速，“工程+实业”携手并进",
    "expected_pages": 37,
    "source_url": "https://pdf.dfcfw.com/pdf/H3_AP202505161673828576_1.pdf",
    "source_page": "https://data.eastmoney.com/report/zw_stock.jshtml?infocode=AP202505161673828576",
    "info_code": "AP202505161673828576",
}

SGPJBG_REPORTS = [
    {
        "sequence": 2,
        "broker": "财通证券",
        "report_date": "2025-01-07",
        "title": "三业联动发展，业绩或迎拐点",
        "expected_pages": 17,
        "source_page": "https://www.sgpjbg.com/sgpjbg/View.aspx?id=399121",
        "report_id": "399121",
        "image_base": "https://file.sgpjbg.com/fileroot2/2025-1/8/cf5cb216-a3f7-457a-9565-48ba311f7f7b/cf5cb216-a3f7-457a-9565-48ba311f7f7b",
    },
    {
        "sequence": 3,
        "broker": "天风证券",
        "report_date": "2025-05-11",
        "title": "高弹性建筑化工小巨人，业绩增长潜力可期",
        "expected_pages": 26,
        "source_page": "https://www.sgpjbg.com/baogao/651928.html",
        "report_id": "651928",
        "image_base": "https://file.sgpjbg.com/fileroot2/2025-5/12/b7238af1-1804-4c32-8026-e87dfa7f6f40/b7238af1-1804-4c32-8026-e87dfa7f6f40",
    },
]


def get(url: str, *, referer: str | None = None, timeout=(15, 180), attempts: int = 5) -> requests.Response:
    headers = {
        "Accept": "application/pdf,image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    }
    if referer:
        headers["Referer"] = referer
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(8, 2**attempt))
    raise RuntimeError(f"request failed: {url}: {last}")


def inspect_pdf(content: bytes, *, expected_pages: int) -> tuple[str, int]:
    if len(content) < 100_000 or not content.lstrip().startswith(b"%PDF-"):
        raise ValueError(f"invalid PDF or too small: {len(content)} bytes")
    if b"%%EOF" not in content[-65536:]:
        raise ValueError("PDF EOF marker missing")
    reader = PdfReader(BytesIO(content), strict=False)
    pages = len(reader.pages)
    if pages != expected_pages:
        raise ValueError(f"unexpected PDF page count: {pages} != {expected_pages}")
    return hashlib.sha256(content).hexdigest(), pages


def safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", value)


def download_original() -> dict[str, Any]:
    item = ORIGINAL_REPORT
    print("TRY ORIGINAL", item["source_url"])
    response = get(item["source_url"], referer=item["source_page"])
    content = response.content
    sha, pages = inspect_pdf(content, expected_pages=item["expected_pages"])

    reader = PdfReader(BytesIO(content), strict=False)
    text_parts: list[str] = []
    for page in reader.pages[:8]:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:
            pass
    text = "".join(text_parts).replace(" ", "")
    if "三维化学" not in text and STOCK_CODE not in text:
        raise ValueError("issuer/ticker not found in original PDF text")
    if "新疆煤化工" not in text and "工程+实业" not in text and "工程＋实业" not in text:
        raise ValueError("expected title not found in original PDF text")

    filename = (
        f"{item['sequence']:02d}_{item['report_date']}_{item['broker']}_三维化学_"
        f"{safe_name(item['title'])}_{pages}页_原始PDF.pdf"
    )
    (OUT / filename).write_bytes(content)
    print(f"OK ORIGINAL {filename} | {len(content)} bytes | sha256={sha}")
    return {
        "sequence": item["sequence"],
        "broker": item["broker"],
        "report_date": item["report_date"],
        "title": item["title"],
        "filename": filename,
        "pages": pages,
        "bytes": len(content),
        "sha256": sha,
        "source_url": response.url,
        "source_page": item["source_page"],
        "source_format": "券商原始PDF",
        "info_code": item["info_code"],
    }


def download_sgpjbg_page(report: dict[str, Any], page_number: int) -> tuple[bytes, str, tuple[int, int], str]:
    url = f"{report['image_base']}{page_number}.gif"
    response = get(url, referer=report["source_page"], timeout=(15, 90), attempts=5)
    content = response.content
    if len(content) < 10_000:
        raise ValueError(f"page image too small: page={page_number}, bytes={len(content)}")
    image = Image.open(BytesIO(content))
    image.load()
    width, height = image.size
    frame_count = getattr(image, "n_frames", 1)
    fmt = str(image.format or "").upper()
    if width < 500 or height < 500:
        raise ValueError(f"page dimensions too small: page={page_number}, {width}x{height}")
    if frame_count != 1:
        raise ValueError(f"unexpected animated image: page={page_number}, frames={frame_count}")
    if fmt not in {"GIF", "PNG", "JPEG", "WEBP"}:
        raise ValueError(f"unexpected page image format: page={page_number}, format={fmt}")
    return content, response.url, (width, height), fmt


def download_sgpjbg_report(report: dict[str, Any]) -> dict[str, Any]:
    page_blobs: list[bytes] = []
    page_urls: list[str] = []
    page_hashes: list[str] = []
    dimensions: list[tuple[int, int]] = []
    formats: list[str] = []

    for page_number in range(1, int(report["expected_pages"]) + 1):
        content, url, dims, fmt = download_sgpjbg_page(report, page_number)
        digest = hashlib.sha256(content).hexdigest()
        page_blobs.append(content)
        page_urls.append(url)
        page_hashes.append(digest)
        dimensions.append(dims)
        formats.append(fmt)
        print(
            f"OK PAGE {report['broker']} {page_number}/{report['expected_pages']} | "
            f"{len(content)} bytes | {dims[0]}x{dims[1]} | {fmt} | {digest}"
        )

    if len(page_blobs) != int(report["expected_pages"]):
        raise ValueError(
            f"incomplete page sequence: {len(page_blobs)}/{report['expected_pages']}"
        )
    if len(set(page_hashes)) != len(page_hashes):
        raise ValueError("duplicate page images detected")

    pdf_content = img2pdf.convert(page_blobs)
    sha, pdf_pages = inspect_pdf(pdf_content, expected_pages=int(report["expected_pages"]))
    filename = (
        f"{report['sequence']:02d}_{report['report_date']}_{report['broker']}_三维化学_"
        f"{safe_name(report['title'])}_{pdf_pages}页_完整公开预览合成.pdf"
    )
    (OUT / filename).write_bytes(pdf_content)
    print(f"OK ASSEMBLED {filename} | {len(pdf_content)} bytes | sha256={sha}")
    return {
        "sequence": report["sequence"],
        "broker": report["broker"],
        "report_date": report["report_date"],
        "title": report["title"],
        "filename": filename,
        "pages": pdf_pages,
        "bytes": len(pdf_content),
        "sha256": sha,
        "source_page": report["source_page"],
        "source_format": "由来源网站公开展示的全部连续页面图像按原页序合成PDF；非券商服务器原始PDF二进制文件",
        "report_id": report["report_id"],
        "page_url_pattern": report["image_base"] + "{page}.gif",
        "first_page_url": page_urls[0],
        "last_page_url": page_urls[-1],
        "page_image_formats": sorted(set(formats)),
        "page_dimensions": sorted({f"{w}x{h}" for w, h in dimensions}),
        "page_sha256": page_hashes,
    }


def main() -> None:
    completed: list[dict[str, Any]] = [download_original()]
    failures: list[str] = []

    for report in SGPJBG_REPORTS:
        try:
            completed.append(download_sgpjbg_report(report))
        except Exception as exc:
            failure = f"{report['broker']} | {report['title']} | {type(exc).__name__}: {exc}"
            failures.append(failure)
            print("REPORT FAILED", failure)

    completed.sort(key=lambda item: int(item["sequence"]))
    if len(completed) < 2:
        raise SystemExit(f"incomplete package: only {len(completed)} valid reports")

    total_pages = sum(int(item["pages"]) for item in completed)
    manifest = {
        "company": COMPANY,
        "stock_code": STOCK_CODE,
        "short_name": SHORT_NAME,
        "selection_rule": (
            "优先公司深度、首次覆盖或专题研究，排除短篇年报、季报和事件点评；"
            "原始PDF优先，原始二进制不可公开直取时使用全部连续公开预览页合成并明确标注"
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_pdf_files": len(completed),
        "total_pages": total_pages,
        "files": completed,
        "failures": failures,
    }
    (OUT / "报告清单与来源.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "SHA256SUMS.txt").write_text(
        "".join(f"{item['sha256']}  {item['filename']}\n" for item in completed),
        encoding="utf-8",
    )

    readme = [
        "三维化学（002469）券商深度报告资料包",
        "==================================",
        "",
        f"共收录 {len(completed)} 份报告、{total_pages} 页。",
        "筛选口径：公司深度、首次覆盖或专题研究；排除短篇年报、季报及事件点评。",
        "",
        "文件清单：",
    ]
    for item in completed:
        readme.append(
            f"- {item['report_date']}｜{item['broker']}｜{item['title']}｜{item['pages']}页｜{item['source_format']}"
        )
    readme.extend(
        [
            "",
            "说明：标记为“完整公开预览合成”的PDF，由来源网站公开展示的全部连续页面图像按原页序合成。",
            "内容与页序保持原样，但不是券商服务器提供的原始PDF二进制文件。",
            "所有文件均已检查PDF签名、EOF、页数、页面连续性、重复页及ZIP CRC。",
            "来源URL、文件大小、文件与逐页SHA-256见《报告清单与来源.json》和SHA256SUMS.txt。",
        ]
    )
    (OUT / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")

    test_zip = Path("sanwei_chemical_broker_reports_crc_test.zip")
    with zipfile.ZipFile(test_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUT.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    with zipfile.ZipFile(test_zip) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC failure: {bad}")
    zip_sha = hashlib.sha256(test_zip.read_bytes()).hexdigest()
    print(f"PACKAGE COMPLETE: {len(completed)} PDFs, {total_pages} pages")
    print(f"CRC TEST ZIP SHA256: {zip_sha}")
    test_zip.unlink()


if __name__ == "__main__":
    main()
