#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
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
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
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
    "pages": 37,
    "source_url": "https://pdf.dfcfw.com/pdf/H3_AP202505161673828576_1.pdf",
    "source_page": "https://data.eastmoney.com/report/zw_stock.jshtml?infocode=AP202505161673828576",
    "info_code": "AP202505161673828576",
}

PREVIEW_REPORTS = [
    {
        "sequence": 2,
        "broker": "财通证券",
        "report_date": "2025-01-06",
        "title": "三业联动发展，业绩或迎拐点",
        "report_id": "4665308",
        "date_paths": ["2025/01/07", "2025/01/06", "2025/01/08"],
        "expected_pages": 17,
        "source_page": "https://www.fxbaogao.com/detail/4665308",
        "required": True,
    },
    {
        "sequence": 3,
        "broker": "国盛证券",
        "report_date": "2025-03-19",
        "title": "“硫磺回收”与“正丙醇”双龙头，新疆煤化工打开成长空间",
        "report_id": "4736599",
        "date_paths": ["2025/03/18", "2025/03/19", "2025/03/17"],
        "expected_pages": None,
        "max_pages": 60,
        "source_page": "https://www.fxbaogao.com/detail/4736599",
        "required": False,
    },
]


def get(url: str, *, referer: str | None = None, timeout=(15, 120), attempts: int = 4) -> requests.Response:
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
                time.sleep(min(6, 2**attempt))
    raise RuntimeError(f"request failed: {url}: {last}")


def pdf_sha_and_pages(content: bytes, *, min_pages: int) -> tuple[str, int]:
    if len(content) < 100_000 or not content.lstrip().startswith(b"%PDF-"):
        raise ValueError(f"invalid PDF or too small: {len(content)} bytes")
    if b"%%EOF" not in content[-65536:]:
        raise ValueError("PDF EOF marker missing")
    reader = PdfReader(BytesIO(content), strict=False)
    pages = len(reader.pages)
    if pages < min_pages:
        raise ValueError(f"too few pages: {pages} < {min_pages}")
    return hashlib.sha256(content).hexdigest(), pages


def validate_original_text(content: bytes) -> None:
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
        raise ValueError("expected report title not found in original PDF text")


def download_original() -> dict[str, Any]:
    item = ORIGINAL_REPORT
    print("TRY ORIGINAL", item["source_url"])
    response = get(item["source_url"], referer=item["source_page"])
    content = response.content
    sha, pages = pdf_sha_and_pages(content, min_pages=30)
    validate_original_text(content)
    if pages != item["pages"]:
        raise ValueError(f"unexpected original report page count: {pages} != {item['pages']}")
    filename = (
        f"{item['sequence']:02d}_{item['report_date']}_{item['broker']}_三维化学_"
        f"{item['title']}_{pages}页_原始PDF.pdf"
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


def image_candidates(report_id: str, date_path: str, page: int) -> list[str]:
    base = f"https://public.fxbaogao.com/report-image/{date_path}"
    return [
        f"{base}/{report_id}-{page}.png",
        f"{base}/{report_id}-{page}.jpg",
        f"{base}/{report_id}-{page}.webp",
        f"{base}/{report_id}_{page}.png",
    ]


def download_image(report: dict[str, Any], date_path: str, page: int) -> tuple[bytes, str, tuple[int, int], str]:
    errors: list[str] = []
    for url in image_candidates(report["report_id"], date_path, page):
        try:
            response = get(url, referer=report["source_page"], timeout=(10, 45), attempts=2)
            content = response.content
            if len(content) < 10_000:
                raise ValueError(f"image too small: {len(content)} bytes")
            image = Image.open(BytesIO(content))
            image.load()
            width, height = image.size
            if width < 500 or height < 500:
                raise ValueError(f"image dimensions too small: {width}x{height}")
            fmt = str(image.format or "").upper()
            if fmt not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise ValueError(f"unexpected image format: {fmt}")
            return content, response.url, (width, height), fmt
        except Exception as exc:
            errors.append(f"{url} :: {type(exc).__name__}: {exc}")
    raise RuntimeError(" | ".join(errors))


def download_complete_preview(report: dict[str, Any]) -> dict[str, Any]:
    chosen_date: str | None = None
    pages: list[bytes] = []
    page_urls: list[str] = []
    dimensions: list[tuple[int, int]] = []
    formats: list[str] = []
    errors: list[str] = []

    for date_path in report["date_paths"]:
        current_pages: list[bytes] = []
        current_urls: list[str] = []
        current_dims: list[tuple[int, int]] = []
        current_formats: list[str] = []
        try:
            if report.get("expected_pages"):
                target_range = range(1, int(report["expected_pages"]) + 1)
                for page in target_range:
                    content, url, dims, fmt = download_image(report, date_path, page)
                    current_pages.append(content)
                    current_urls.append(url)
                    current_dims.append(dims)
                    current_formats.append(fmt)
                    print(
                        f"OK IMAGE {report['broker']} page={page} date_path={date_path} "
                        f"bytes={len(content)} dims={dims[0]}x{dims[1]} format={fmt}"
                    )
            else:
                max_pages = int(report.get("max_pages", 60))
                for page in range(1, max_pages + 1):
                    try:
                        content, url, dims, fmt = download_image(report, date_path, page)
                    except Exception as exc:
                        if page == 1:
                            raise
                        print(f"END IMAGE SEQUENCE at page={page}: {exc}")
                        break
                    current_pages.append(content)
                    current_urls.append(url)
                    current_dims.append(dims)
                    current_formats.append(fmt)
                    print(
                        f"OK IMAGE {report['broker']} page={page} date_path={date_path} "
                        f"bytes={len(content)} dims={dims[0]}x{dims[1]} format={fmt}"
                    )
                if len(current_pages) < 15:
                    raise ValueError(f"only {len(current_pages)} sequential pages; need at least 15")

            hashes = [hashlib.sha256(x).hexdigest() for x in current_pages]
            if len(set(hashes)) != len(hashes):
                raise ValueError("duplicate page images detected")
            if report.get("expected_pages") and len(current_pages) != int(report["expected_pages"]):
                raise ValueError(
                    f"incomplete preview: {len(current_pages)}/{report['expected_pages']} pages"
                )
            chosen_date = date_path
            pages = current_pages
            page_urls = current_urls
            dimensions = current_dims
            formats = current_formats
            break
        except Exception as exc:
            errors.append(f"date_path={date_path}: {type(exc).__name__}: {exc}")
            print("PREVIEW PATH FAILED", errors[-1])

    if not pages or not chosen_date:
        raise RuntimeError("all preview image paths failed\n" + "\n".join(errors))

    pdf_content = img2pdf.convert(pages)
    sha, pdf_pages = pdf_sha_and_pages(pdf_content, min_pages=15)
    if pdf_pages != len(pages):
        raise ValueError(f"assembled PDF page mismatch: {pdf_pages} != {len(pages)}")
    safe_title = report["title"].replace("/", "_").replace("\\", "_")
    filename = (
        f"{report['sequence']:02d}_{report['report_date']}_{report['broker']}_三维化学_"
        f"{safe_title}_{pdf_pages}页_完整公开预览合成.pdf"
    )
    (OUT / filename).write_bytes(pdf_content)
    print(f"OK PREVIEW PDF {filename} | {len(pdf_content)} bytes | sha256={sha}")
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
        "source_format": "由公开完整预览页按原页序合成PDF；不是券商服务器原始PDF二进制文件",
        "preview_date_path": chosen_date,
        "first_page_url": page_urls[0],
        "last_page_url": page_urls[-1],
        "page_image_formats": sorted(set(formats)),
        "page_dimensions": sorted({f"{w}x{h}" for w, h in dimensions}),
        "report_id": report["report_id"],
    }


def main() -> None:
    completed: list[dict[str, Any]] = []
    failures: list[str] = []

    completed.append(download_original())

    for report in PREVIEW_REPORTS:
        try:
            completed.append(download_complete_preview(report))
        except Exception as exc:
            failure = f"{report['broker']} | {report['title']} | {type(exc).__name__}: {exc}"
            failures.append(failure)
            print("REPORT FAILED", failure)
            if report.get("required"):
                raise

    completed.sort(key=lambda x: x["sequence"])
    if len(completed) < 2:
        raise SystemExit(f"incomplete package: {len(completed)} reports")

    total_pages = sum(int(x["pages"]) for x in completed)
    manifest = {
        "company": COMPANY,
        "stock_code": STOCK_CODE,
        "short_name": SHORT_NAME,
        "selection_rule": (
            "优先公司深度/首次覆盖报告，排除短篇年报、季报和事件点评；"
            "原始PDF优先，原始二进制不可公开直取时使用连续完整公开预览页合成PDF并明确标注"
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_pdf_files": len(completed),
        "total_pages": total_pages,
        "files": completed,
        "optional_failures": failures,
    }
    (OUT / "报告清单与来源.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "SHA256SUMS.txt").write_text(
        "".join(f"{x['sha256']}  {x['filename']}\n" for x in completed), encoding="utf-8"
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
            "内容和页序保持原样，但该文件并非券商服务器提供的原始PDF二进制文件。",
            "所有文件均已检查PDF签名、EOF、页数、页面连续性、重复页和ZIP CRC。",
            "具体来源URL、文件大小及SHA-256见《报告清单与来源.json》和SHA256SUMS.txt。",
        ]
    )
    (OUT / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")

    test_zip = Path("sanwei_chemical_broker_reports_crc_test.zip")
    with zipfile.ZipFile(test_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(OUT.iterdir()):
            if path.is_file():
                zf.write(path, path.name)
    with zipfile.ZipFile(test_zip) as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC failure: {bad}")
    zip_sha = hashlib.sha256(test_zip.read_bytes()).hexdigest()
    print(f"PACKAGE COMPLETE: {len(completed)} PDFs, {total_pages} pages")
    print(f"CRC TEST ZIP SHA256: {zip_sha}")
    test_zip.unlink()


if __name__ == "__main__":
    main()
