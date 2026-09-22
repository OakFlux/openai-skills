#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image
from pypdf import PdfReader

OUT = Path("output")
OUT.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)

REPORTS = [
    {
        "id": "177723",
        "institution": "天风证券",
        "date": "2024-10-14",
        "title": "三级引擎齐发力，珍酒李渡成长可期",
        "expected_total_pages": 28,
        "filename": "01_天风证券_珍酒李渡_三级引擎齐发力成长可期_2024-10-14_公开预览15页.pdf",
    },
    {
        "id": "144022",
        "institution": "华西证券",
        "date": "2023-10-24",
        "title": "多赛道布局，老品牌开创酱酒新格局",
        "expected_total_pages": 62,
        "filename": "02_华西证券_珍酒李渡_多赛道布局老品牌开创酱酒新格局_2023-10-24_公开预览15页.pdf",
    },
    {
        "id": "128486",
        "institution": "海通国际",
        "date": "2023-06-06",
        "title": "首次覆盖：珍酒与李渡双品牌驱动收入增长，多香型赛道市占率提升",
        "expected_total_pages": 24,
        "filename": "03_海通国际_珍酒李渡_双品牌驱动收入增长_2023-06-06_公开预览15页.pdf",
    },
]


def fetch(url: str, *, referer: str | None = None, timeout: int = 60) -> requests.Response:
    headers = {"Referer": referer} if referer else {}
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = SESSION.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
            if response.status_code == 200:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {url}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(2**attempt)
    raise RuntimeError(f"Fetch failed: {url}: {last_error}")


def parse_int_variable(html: str, name: str) -> int | None:
    patterns = [
        rf"var\s+{re.escape(name)}\s*=\s*parseInt\(\s*[\"']?(\d+)[\"']?\s*\)",
        rf"var\s+{re.escape(name)}\s*=\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def image_to_rgb(data: bytes, report_id: str, page_no: int) -> tuple[Image.Image, dict]:
    try:
        source = Image.open(io.BytesIO(data))
        source.load()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Invalid public preview image for {report_id}, page {page_no}: {exc}"
        ) from exc

    if source.width < 600 or source.height < 400:
        raise RuntimeError(
            f"Unexpected image dimensions for {report_id}, page {page_no}: {source.size}"
        )

    rgba = source.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    rgb = background.convert("RGB")
    metadata = {
        "width": source.width,
        "height": source.height,
        "source_format": source.format,
    }
    source.close()
    rgba.close()
    background.close()
    return rgb, metadata


def main() -> None:
    manifest: list[dict] = []
    pdf_paths: list[Path] = []

    for spec in REPORTS:
        report_id = spec["id"]
        detail_url = f"https://www.sgpjbg.com/baogao/{report_id}.html"
        detail_response = fetch(detail_url, timeout=45)
        html = detail_response.text
        soup = BeautifulSoup(html, "html.parser")
        prefix_element = soup.find("input", id="dp")
        if not prefix_element or not prefix_element.get("value"):
            raise RuntimeError(f"Missing public reader page prefix for report {report_id}")
        page_prefix = str(prefix_element["value"])

        preview_pages = parse_int_variable(html, "mtp")
        total_pages = parse_int_variable(html, "fCount")
        force_free_pages = parse_int_variable(html, "ForceFreepage")

        if preview_pages != 15 or force_free_pages != 15:
            raise RuntimeError(
                f"Unexpected public preview range for {report_id}: "
                f"mtp={preview_pages}, ForceFreepage={force_free_pages}"
            )
        if total_pages != spec["expected_total_pages"]:
            raise RuntimeError(
                f"Unexpected original page count for {report_id}: {total_pages}"
            )

        page_dir = OUT / f"pages_{report_id}"
        page_dir.mkdir(parents=True, exist_ok=True)
        images: list[Image.Image] = []
        page_records: list[dict] = []

        for page_no in range(1, preview_pages + 1):
            # Fetch only pages within the source site's explicit public preview range.
            page_url = f"{page_prefix}{page_no}.gif"
            page_response = fetch(page_url, referer=detail_url, timeout=60)
            data = page_response.content
            rgb, image_metadata = image_to_rgb(data, report_id, page_no)
            jpg_path = page_dir / f"{page_no:03d}.jpg"
            rgb.save(jpg_path, "JPEG", quality=94, optimize=True)
            images.append(rgb.copy())
            rgb.close()
            page_records.append(
                {
                    "page": page_no,
                    "url": page_url,
                    "source_bytes": len(data),
                    "source_sha256": hashlib.sha256(data).hexdigest(),
                    **image_metadata,
                }
            )

        pdf_path = OUT / spec["filename"]
        images[0].save(
            pdf_path,
            "PDF",
            resolution=150.0,
            save_all=True,
            append_images=images[1:],
        )
        for image in images:
            image.close()

        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted or len(reader.pages) != preview_pages:
            raise RuntimeError(
                f"PDF validation failed for {report_id}: "
                f"pages={len(reader.pages)}, encrypted={reader.is_encrypted}"
            )

        record = {
            **spec,
            "source_detail_page": detail_url,
            "public_preview_pages": preview_pages,
            "original_report_pages": total_pages,
            "page_prefix": page_prefix,
            "pdf_pages": len(reader.pages),
            "pdf_bytes": pdf_path.stat().st_size,
            "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "page_records": page_records,
            "completeness": (
                "公开预览版：收录平台公开在线阅读范围内的第1至15页；"
                "未包含账户权限限制后的剩余页面。"
            ),
        }
        manifest.append(record)
        pdf_paths.append(pdf_path)
        print(
            "VALIDATED",
            json.dumps(
                {
                    key: record[key]
                    for key in (
                        "id",
                        "institution",
                        "title",
                        "public_preview_pages",
                        "original_report_pages",
                        "pdf_bytes",
                        "pdf_sha256",
                    )
                },
                ensure_ascii=False,
            ),
        )

    note = """珍酒李渡（06979.HK）券商深度报告资料包——公开预览版

本资料包包含：
1. 天风证券：《三级引擎齐发力，珍酒李渡成长可期》，2024-10-14，原报告28页，收录公开预览第1至15页。
2. 华西证券：《多赛道布局，老品牌开创酱酒新格局》，2023-10-24，原报告62页，收录公开预览第1至15页。
3. 海通国际：《首次覆盖：珍酒与李渡双品牌驱动收入增长，多香型赛道市占率提升》，2023-06-06，原报告24页，收录公开预览第1至15页。

完整性说明：
- 三份原始报告的完整下载均要求来源平台账户权限；本资料包只收录来源平台明确公开展示的前15页。
- PDF由公开在线阅读页的原始逐页图像按原顺序合成，未改写、补写、裁剪或重排报告正文。
- 未请求或抓取公开预览范围以外的页面，未绕过登录、会员或付费限制。
- 文件仅供个人研究与学习使用，请遵守原报告版权及免责声明。
"""
    (OUT / "资料说明.txt").write_text(note, encoding="utf-8")
    (OUT / "文件清单及校验值.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    zip_path = OUT / "珍酒李渡_券商深度报告_公开预览版_3份.zip"
    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for pdf_path in pdf_paths:
            archive.write(pdf_path, pdf_path.name)
        archive.write(OUT / "资料说明.txt", "资料说明.txt")
        archive.write(OUT / "文件清单及校验值.json", "文件清单及校验值.json")

    with zipfile.ZipFile(zip_path) as archive:
        bad_file = archive.testzip()
        if bad_file:
            raise RuntimeError(f"ZIP CRC failed at {bad_file}")

    summary = {
        "zip": zip_path.name,
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "reports": [
            {
                key: record[key]
                for key in (
                    "id",
                    "institution",
                    "title",
                    "date",
                    "public_preview_pages",
                    "original_report_pages",
                    "pdf_bytes",
                    "pdf_sha256",
                )
            }
            for record in manifest
        ],
    }
    (OUT / "BUILD_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
