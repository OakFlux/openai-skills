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

REPORTS = [
    {
        "sequence": 1,
        "broker": "国信证券",
        "report_date": "2025-05-16",
        "title": "受益于新疆煤化工建设提速，“工程+实业”携手并进",
        "expected_pages": 37,
        "source_url": "https://pdf.dfcfw.com/pdf/H3_AP202505161673828576_1.pdf",
        "source_page": "https://data.eastmoney.com/report/zw_stock.jshtml?infocode=AP202505161673828576",
        "info_code": "AP202505161673828576",
        "source_type": "original_pdf",
    },
    {
        "sequence": 2,
        "broker": "国盛证券",
        "report_date": "2025-03-18",
        "title": "“硫磺回收”与“正丙醇”双龙头，新疆煤化工打开成长空间",
        "expected_pages": 27,
        "source_page": "https://www.baogaobox.com/reports/250319000052828.html",
        "image_pattern": "https://zy.baogaobox.com/file/p/2025/03-19/img7a1bz63x7r500b73vtxid00052828_{page}.webp",
        "source_type": "public_page_sequence",
    },
]


def fetch(url: str, *, referer: str | None = None, attempts: int = 5) -> requests.Response:
    headers = {
        "Accept": "application/pdf,image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    }
    if referer:
        headers["Referer"] = referer
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = SESSION.get(
                url,
                headers=headers,
                timeout=(20, 180),
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(8, 2**attempt))
    raise RuntimeError(f"request failed: {url}: {last}")


def inspect_pdf(content: bytes, expected_pages: int) -> tuple[str, int]:
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


def download_original_pdf(report: dict[str, Any]) -> dict[str, Any]:
    response = fetch(report["source_url"], referer=report["source_page"])
    content = response.content
    sha, pages = inspect_pdf(content, int(report["expected_pages"]))

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
        f"{report['sequence']:02d}_{report['report_date']}_{report['broker']}_三维化学_"
        f"{safe_name(report['title'])}_{pages}页_原始PDF.pdf"
    )
    (OUT / filename).write_bytes(content)
    print(f"OK ORIGINAL {filename} | {len(content)} bytes | sha256={sha}")
    return {
        "sequence": report["sequence"],
        "broker": report["broker"],
        "report_date": report["report_date"],
        "title": report["title"],
        "filename": filename,
        "pages": pages,
        "bytes": len(content),
        "sha256": sha,
        "source_page": report["source_page"],
        "source_url": response.url,
        "source_format": "券商原始PDF",
        "info_code": report["info_code"],
    }


def decode_page_image(content: bytes, page_number: int) -> tuple[bytes, tuple[int, int], str]:
    if len(content) < 10_000:
        raise ValueError(f"page {page_number} image too small: {len(content)} bytes")
    image = Image.open(BytesIO(content))
    image.load()
    width, height = image.size
    source_format = str(image.format or "").upper()
    if width < 500 or height < 500:
        raise ValueError(f"page {page_number} dimensions too small: {width}x{height}")
    if getattr(image, "n_frames", 1) != 1:
        raise ValueError(f"page {page_number} is animated")
    if source_format not in {"WEBP", "PNG", "JPEG", "GIF"}:
        raise ValueError(f"page {page_number} unexpected format: {source_format}")

    # Convert each public page image to lossless PNG before PDF assembly.
    if image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGB")
    png = BytesIO()
    image.save(png, format="PNG", optimize=True)
    return png.getvalue(), (width, height), source_format


def download_public_page_sequence(report: dict[str, Any]) -> dict[str, Any]:
    png_pages: list[bytes] = []
    source_hashes: list[str] = []
    source_urls: list[str] = []
    dimensions: list[tuple[int, int]] = []
    source_formats: list[str] = []

    for page_number in range(1, int(report["expected_pages"]) + 1):
        url = report["image_pattern"].format(page=page_number)
        response = fetch(url, referer=report["source_page"])
        source_content = response.content
        source_sha = hashlib.sha256(source_content).hexdigest()
        png_content, dims, source_format = decode_page_image(source_content, page_number)
        png_pages.append(png_content)
        source_hashes.append(source_sha)
        source_urls.append(response.url)
        dimensions.append(dims)
        source_formats.append(source_format)
        print(
            f"OK PAGE {page_number}/{report['expected_pages']} | {len(source_content)} bytes | "
            f"{dims[0]}x{dims[1]} | {source_format} | {source_sha}"
        )

    if len(png_pages) != int(report["expected_pages"]):
        raise ValueError(
            f"incomplete page sequence: {len(png_pages)}/{report['expected_pages']}"
        )
    if len(set(source_hashes)) != len(source_hashes):
        raise ValueError("duplicate public page images detected")

    pdf_content = img2pdf.convert(png_pages)
    sha, pages = inspect_pdf(pdf_content, int(report["expected_pages"]))
    filename = (
        f"{report['sequence']:02d}_{report['report_date']}_{report['broker']}_三维化学_"
        f"{safe_name(report['title'])}_{pages}页_完整公开页面合成.pdf"
    )
    (OUT / filename).write_bytes(pdf_content)
    print(f"OK ASSEMBLED {filename} | {len(pdf_content)} bytes | sha256={sha}")
    return {
        "sequence": report["sequence"],
        "broker": report["broker"],
        "report_date": report["report_date"],
        "title": report["title"],
        "filename": filename,
        "pages": pages,
        "bytes": len(pdf_content),
        "sha256": sha,
        "source_page": report["source_page"],
        "source_format": (
            "由来源报告页公开提供的27张连续页面图像按原页序合成PDF；"
            "内容完整，但不是券商服务器原始PDF二进制文件"
        ),
        "page_url_pattern": report["image_pattern"],
        "first_page_url": source_urls[0],
        "last_page_url": source_urls[-1],
        "page_source_formats": sorted(set(source_formats)),
        "page_dimensions": sorted({f"{w}x{h}" for w, h in dimensions}),
        "page_source_sha256": source_hashes,
    }


def main() -> None:
    completed = [
        download_original_pdf(REPORTS[0]),
        download_public_page_sequence(REPORTS[1]),
    ]
    completed.sort(key=lambda item: int(item["sequence"]))
    total_pages = sum(int(item["pages"]) for item in completed)

    manifest = {
        "company": COMPANY,
        "stock_code": STOCK_CODE,
        "short_name": SHORT_NAME,
        "selection_rule": (
            "仅收录公司深度/首次覆盖类报告，排除短篇财报与事件点评；"
            "原始PDF优先，另一份由报告页公开的完整连续页面合成并明确标注"
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_pdf_files": len(completed),
        "total_pages": total_pages,
        "files": completed,
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
        "筛选口径：公司深度或首次覆盖类报告；排除短篇财报、季报和事件点评。",
        "",
        "文件清单：",
    ]
    for item in completed:
        readme.append(
            f"- {item['report_date']}｜{item['broker']}｜{item['title']}｜"
            f"{item['pages']}页｜{item['source_format']}"
        )
    readme.extend(
        [
            "",
            "国盛证券文件由来源报告页公开提供的27张连续页面图像按原页序合成。",
            "报告内容和页序完整，但该文件不是券商服务器提供的原始PDF二进制文件。",
            "两份PDF均已检查文件签名、EOF、页数和可解析性；页面合成文件另检查连续性及重复页。",
            "来源、文件大小及SHA-256见《报告清单与来源.json》和SHA256SUMS.txt。",
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
