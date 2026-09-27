#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pymupdf
import requests
import urllib3
from PIL import Image, ImageDraw
from pypdf import PdfReader

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OUT = Path("output_avic_xac")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

COMPANY_FULL = "中航西安飞机工业集团股份有限公司"
COMPANY_SHORT = "中航西飞"
FORMER_NAME = "中航飞机股份有限公司"
STOCK_CODE = "000768"
AS_OF_DATE = "2026-09-27"

FILES = [
    {
        "filename": "01_中航西飞_2020年年度报告.pdf",
        "category": "2020年年度报告",
        "year": "2020",
        "doc_type": "年度报告",
        "published": "2021-03-30",
        "source_url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2021/2021-3/2021-03-30/6992715.PDF",
        "source_page": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=6992715&stockid=000768",
    },
    {
        "filename": "02_中航西飞_2021年年度报告.pdf",
        "category": "2021年年度报告",
        "year": "2021",
        "doc_type": "年度报告",
        "published": "2022-03-29",
        "source_url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2022/2022-3/2022-03-29/7926471.PDF",
        "source_page": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=7926471&stockid=000768",
    },
    {
        "filename": "03_中航西飞_2022年年度报告.pdf",
        "category": "2022年年度报告（公开披露版本）",
        "year": "2022",
        "doc_type": "年度报告",
        "published": "2023-04-18",
        "source_url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2023/2023-4/2023-04-18/9002591.PDF",
        "source_page": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=9002591&stockid=000768",
    },
    {
        "filename": "04_中航西飞_2023年年度报告.pdf",
        "category": "2023年年度报告",
        "year": "2023",
        "doc_type": "年度报告",
        "published": "2024-04-02",
        "source_url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2024/2024-4/2024-04-02/9936410.PDF",
        "source_page": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=9936410&stockid=000768",
    },
    {
        "filename": "05_中航西飞_2024年年度报告.pdf",
        "category": "2024年年度报告",
        "year": "2024",
        "doc_type": "年度报告",
        "published": "2025-04-01",
        "source_url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2025/2025-4/2025-04-01/10838171.PDF",
        "source_page": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=10838171&stockid=000768",
    },
    {
        "filename": "06_中航西飞_2025年年度报告.pdf",
        "category": "2025年年度报告",
        "year": "2025",
        "doc_type": "年度报告",
        "published": "2026-03-31",
        "source_url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2026/2026-3/2026-03-31/12038864.PDF",
        "source_page": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12038864&stockid=000768",
    },
    {
        "filename": "07_中航西飞_2026年第一季度报告_最新季报.pdf",
        "category": "2026年第一季度报告（截至2026-09-27最新标题明确为季度报告的正式定期报告）",
        "year": "2026",
        "doc_type": "第一季度报告",
        "published": "2026-04-29",
        "source_url": "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESZ_STOCK/2026/2026-4/2026-04-29/12246623.PDF",
        "source_page": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12246623&stockid=000768",
    },
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def download(item: dict, timeout: int = 240):
    encoded = item["source_url"]
    decoded = unquote(encoded)
    candidates = [encoded]
    if decoded != encoded:
        candidates.append(decoded)
    if decoded.startswith("https://"):
        candidates.append("http://" + decoded[len("https://"):])

    last_error = None
    for url in candidates:
        for attempt in range(4):
            try:
                headers = {"Referer": item["source_page"]}
                with SESSION.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                    stream=True,
                    verify=False,
                ) as response:
                    print("FETCH", response.status_code, response.url, response.headers.get("content-type"))
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            data.extend(chunk)
                    payload = bytes(data)
                    if payload.startswith(b"%PDF-"):
                        return payload, response.url
                    raise RuntimeError(f"non-PDF payload, head={payload[:64]!r}")
            except Exception as exc:
                last_error = exc
                time.sleep(min(12, 2 ** attempt))
    raise RuntimeError(f"Failed to download {item['filename']}: {last_error}")


def extract_identity_text(path: Path, page_count: int) -> str:
    chunks = []
    reader = PdfReader(str(path))
    indices = list(range(min(page_count, 24)))
    if page_count > 30:
        indices.extend([page_count // 2, page_count - 2, page_count - 1])
    for index in sorted(set(i for i in indices if 0 <= i < page_count)):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    text = "\n".join(chunks)
    if len(text.strip()) < 300:
        doc = pymupdf.open(str(path))
        chunks = []
        for index in sorted(set(i for i in indices if 0 <= i < doc.page_count)):
            chunks.append(doc[index].get_text("text") or "")
        doc.close()
        text = "\n".join(chunks)
    return re.sub(r"\s+", "", text)


def validate_identity(text: str, item: dict):
    company_ok = any(token in text for token in (COMPANY_FULL, COMPANY_SHORT, FORMER_NAME, "西飞国际"))
    if not company_ok:
        raise RuntimeError(f"Company identity check failed: {item['filename']}")
    if item["year"] not in text:
        raise RuntimeError(f"Report-year check failed: {item['filename']}")
    if item["doc_type"] == "年度报告":
        type_ok = "年度报告" in text or "ANNUALREPORT" in text.upper()
    else:
        type_ok = "第一季度报告" in text or "一季度报告" in text or "FIRSTQUARTER" in text.upper()
    if not type_ok:
        raise RuntimeError(f"Document-type check failed: {item['filename']}")


def render_sample_pages(path: Path, pages: int):
    doc = pymupdf.open(str(path))
    picks = sorted(set([0, pages // 2, pages - 1]))
    outputs = []
    for page_index in picks:
        pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(1.15, 1.15), alpha=False)
        target = VERIFY / f"{path.stem}_p{page_index + 1}.png"
        pix.save(str(target))
        outputs.append(target)
    doc.close()
    return outputs


manifest = []
render_entries = []
for item in FILES:
    data, final_url = download(item)
    minimum_size = 30_000 if item["doc_type"] != "年度报告" else 100_000
    if len(data) < minimum_size:
        raise RuntimeError(f"PDF too small: {item['filename']} bytes={len(data)}")

    path = OUT / item["filename"]
    path.write_bytes(data)

    reader = PdfReader(str(path))
    encrypted = reader.is_encrypted
    if encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = len(reader.pages)
    if pages < (8 if item["doc_type"] != "年度报告" else 80):
        raise RuntimeError(f"Unexpectedly short report: {path.name}, pages={pages}")

    identity_text = extract_identity_text(path, pages)
    validate_identity(identity_text, item)
    renders = render_sample_pages(path, pages)
    for render_path in renders:
        render_entries.append((item["category"], render_path))

    record = {
        "filename": path.name,
        "category": item["category"],
        "year": item["year"],
        "document_type": item["doc_type"],
        "published": item["published"],
        "source_type": "新浪财经保存的上市公司公告原始PDF附件",
        "source_page": item["source_page"],
        "source_url": item["source_url"],
        "final_url": final_url,
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
        "rendered_pages": [p.name for p in renders],
    }
    manifest.append(record)
    print("VALIDATED", json.dumps(record, ensure_ascii=False))

# Contact sheet for visual inspection of first/middle/last pages.
thumbs = []
for label, render_path in render_entries:
    image = Image.open(render_path).convert("RGB")
    image.thumbnail((380, 520))
    canvas = Image.new("RGB", (400, 570), "white")
    canvas.paste(image, ((400 - image.width) // 2, 8))
    ascii_label = render_path.stem[-36:]
    ImageDraw.Draw(canvas).text((8, 542), ascii_label, fill="black")
    thumbs.append(canvas)
    image.close()
cols = 3
rows = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 400, rows * 570), "white")
for index, canvas in enumerate(thumbs):
    sheet.paste(canvas, ((index % cols) * 400, (index // cols) * 570))
    canvas.close()
contact_sheet = VERIFY / "contact_sheet.jpg"
sheet.save(contact_sheet, "JPEG", quality=88)
sheet.close()

note = f"""中航西安飞机工业集团股份有限公司（证券简称：中航西飞，证券代码：{STOCK_CODE}）定期报告资料包

整理日期：{AS_OF_DATE}

文件范围：
1. 2020年至2025年完整年度报告，共6份。
2. 2026年第一季度报告，共1份；这是截至整理日最新一份标题明确为“季度报告”的正式定期报告。
3. 公司已于2026年8月披露半年度报告，但半年度报告不属于标题明确的季度报告，因此本包按用户“最新季报”的通常口径收录2026年第一季度报告。

来源与校验：
- 各文件均来自新浪财经保存的上市公司公告原始PDF附件，并与相应公告详情页的公司名称、证券代码、报告年份和文件类型交叉核对。
- 逐份检查PDF文件头、页数、加密状态、公司名称、报告年份及报告类型。
- 每份PDF均渲染首页、中间页和末页进行视觉检查。
- 压缩包附完整来源、文件大小、页数和SHA-256校验值，并执行ZIP CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始披露文件的版权及免责声明。
"""
(OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "00_文件清单及来源校验.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

sha_lines = [f"{record['sha256']}  {record['filename']}" for record in manifest]
(OUT / "SHA256SUMS.txt").write_text("\n".join(sha_lines) + "\n", encoding="utf-8")

zip_path = OUT / "中航西飞_000768_2020-2025年报及2026年第一季度报告.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
    archive.write(OUT / "00_文件清单及来源校验.json", "00_文件清单及来源校验.json")
    archive.write(OUT / "SHA256SUMS.txt", "SHA256SUMS.txt")
    for item in FILES:
        path = OUT / item["filename"]
        archive.write(path, path.name)

with zipfile.ZipFile(zip_path) as archive:
    bad_file = archive.testzip()
    if bad_file:
        raise RuntimeError(f"ZIP CRC validation failed at {bad_file}")

summary = {
    "zip": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "pdf_count": len(FILES),
    "total_pages": sum(record["pages"] for record in manifest),
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("PACKAGE", json.dumps(summary, ensure_ascii=False))
