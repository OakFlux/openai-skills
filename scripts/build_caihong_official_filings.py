#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import fitz
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
from pypdf import PdfReader

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

STOCK_CODE = "600707"
COMPANY_SHORT = "彩虹股份"
COMPANY_FULL = "彩虹显示器件股份有限公司"
AS_OF_DATE = "2026-09-29"
ANNUAL_LIST = (
    f"https://vip.stock.finance.sina.com.cn/corp/go.php/"
    f"vCB_Bulletin/stockid/{STOCK_CODE}/page_type/ndbg.phtml"
)
QUARTER_LIST = (
    f"https://vip.stock.finance.sina.com.cn/corp/go.php/"
    f"vCB_Bulletin/stockid/{STOCK_CODE}/page_type/yjdbg.phtml"
)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def get(url: str, *, referer: str | None = None, timeout: int = 180):
    last_error = None
    for attempt in range(5):
        try:
            headers = {"Referer": referer} if referer else {}
            response = SESSION.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
            print(
                "FETCH",
                response.status_code,
                len(response.content),
                response.url,
                response.headers.get("content-type"),
            )
            if response.status_code == 200:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {response.url}")
        except Exception as exc:
            last_error = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def decode_html(response: requests.Response) -> str:
    response.encoding = response.apparent_encoding or "gb18030"
    return response.text


def parse_list(list_url: str) -> list[dict]:
    response = get(list_url, timeout=90)
    soup = BeautifulSoup(decode_html(response), "html.parser")
    records: list[dict] = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(response.url, anchor["href"])
        if "vCB_AllBulletinDetail.php" not in href:
            continue
        row = anchor.find_parent("tr")
        context = " ".join(
            (row.get_text(" ", strip=True) if row else text).split()
        )
        records.append({"text": text, "context": context, "detail_url": href})

    dedup: dict[str, dict] = {}
    for record in records:
        dedup[record["detail_url"]] = record
    values = list(dedup.values())
    print("LIST_RECORDS", list_url, len(values))
    for item in values[:30]:
        print("LIST_ITEM", json.dumps(item, ensure_ascii=False))
    return values


def select_annual(records: list[dict], year: int) -> dict:
    candidates = []
    for record in records:
        title = record["text"]
        if str(year) not in title:
            continue
        if "年度报告" not in title and "年报" not in title:
            continue
        if any(
            term in title
            for term in (
                "摘要",
                "英文版",
                "审计报告",
                "社会责任报告",
                "环境、社会及管治",
                "可持续发展报告",
                "业绩快报",
                "业绩预告",
            )
        ):
            continue
        candidates.append(record)
    if not candidates:
        available = [r["text"] for r in records if str(year) in r["text"]]
        raise RuntimeError(
            f"No full annual report found for {year}; available={available}"
        )
    candidates.sort(
        key=lambda item: (
            f"{year}年年度报告" in item["text"],
            len(item["text"]),
        ),
        reverse=True,
    )
    print("SELECT_ANNUAL", year, json.dumps(candidates[0], ensure_ascii=False))
    return candidates[0]


def select_latest_quarter(records: list[dict]) -> dict:
    candidates = []
    for record in records:
        title = record["text"]
        if "2026" not in title:
            continue
        if "第一季度报告" not in title and "一季度报告" not in title:
            continue
        if any(term in title for term in ("摘要", "业绩预告", "业绩快报")):
            continue
        candidates.append(record)
    if not candidates:
        available = [r["text"] for r in records if "2026" in r["text"]]
        raise RuntimeError(f"No 2026 first-quarter report found; available={available}")
    candidates.sort(
        key=lambda item: (
            "2026年第一季度报告" in item["text"],
            len(item["text"]),
        ),
        reverse=True,
    )
    print("SELECT_QUARTER", json.dumps(candidates[0], ensure_ascii=False))
    return candidates[0]


def extract_pdf_urls(detail_url: str) -> list[str]:
    response = get(detail_url, timeout=90)
    html = decode_html(response)
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(response.url, anchor["href"])
        if ".PDF" in href.upper() or "下载公告" in text or "PDF" in text.upper():
            urls.append(href)

    patterns = [
        r"https?://file\.finance\.sina\.com\.cn/[^\"'<>\s]+\.PDF(?:\?[^\"'<>\s]*)?",
        r"https?://[^\"'<>\s]+\.PDF(?:\?[^\"'<>\s]*)?",
    ]
    for pattern in patterns:
        urls.extend(re.findall(pattern, html, re.I))

    dedup: list[str] = []
    seen: set[str] = set()
    for url in urls:
        clean = url.replace("&amp;", "&").replace("\\/", "/")
        if clean not in seen:
            dedup.append(clean)
            seen.add(clean)
    if not dedup:
        raise RuntimeError(f"No PDF URL found at {detail_url}")
    dedup.sort(
        key=lambda url: (
            "file.finance.sina.com.cn" not in url,
            ".PDF" not in url.upper(),
            len(url),
        )
    )
    print("PDF_URLS", detail_url, json.dumps(dedup, ensure_ascii=False))
    return dedup


def download_pdf(detail_url: str) -> tuple[bytes, str, str]:
    errors: list[str] = []
    for url in extract_pdf_urls(detail_url):
        variants = [url]
        if url.startswith("http://"):
            variants.insert(0, "https://" + url[len("http://") :])
        for candidate in variants:
            try:
                response = get(candidate, referer=detail_url, timeout=240)
                data = response.content
                if data.startswith(b"%PDF-") and len(data) > 50_000:
                    return data, candidate, response.url
                errors.append(f"{candidate}: not PDF ({len(data)} bytes)")
            except Exception as exc:
                errors.append(f"{candidate}: {exc!r}")
    raise RuntimeError("No valid PDF attachment: " + " | ".join(errors))


def first_pages_text(path: Path, limit: int = 8) -> str:
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password protected: {path.name}")
    chunks: list[str] = []
    for index in range(min(limit, len(reader.pages))):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    text = " ".join(chunks)
    if len(text.strip()) < 100:
        doc = fitz.open(str(path))
        text = " ".join(
            doc[index].get_text("text")
            for index in range(min(limit, doc.page_count))
        )
        doc.close()
    return re.sub(r"\s+", "", text).upper()


def validate(
    path: Path,
    year: int,
    doc_type: str,
    minimum_pages: int,
) -> tuple[int, bool]:
    reader = PdfReader(str(path))
    encrypted = bool(reader.is_encrypted)
    if encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password protected: {path.name}")
    pages = len(reader.pages)
    if pages < minimum_pages:
        raise RuntimeError(f"Unexpected page count for {path.name}: {pages}")

    text = first_pages_text(path)
    company_tokens = (
        COMPANY_FULL,
        COMPANY_SHORT,
        "CAIHONG DISPLAY DEVICES",
        "CAIHONGDISPLAYDEVICES",
    )
    if not any(token.replace(" ", "").upper() in text for token in company_tokens):
        raise RuntimeError(f"Company identity validation failed: {path.name}")
    if str(year) not in text:
        raise RuntimeError(f"Year validation failed: {path.name}")
    if doc_type == "annual":
        if "年度报告" not in text and "ANNUALREPORT" not in text:
            raise RuntimeError(f"Annual-report validation failed: {path.name}")
    else:
        if not any(
            token in text
            for token in ("第一季度报告", "一季度报告", "FIRSTQUARTERLYREPORT")
        ):
            raise RuntimeError(f"Quarter-report validation failed: {path.name}")
    return pages, encrypted


def main() -> None:
    annual_records = parse_list(ANNUAL_LIST)
    quarter_records = parse_list(QUARTER_LIST)

    specs: list[dict] = []
    for index, year in enumerate(range(2020, 2026), 1):
        specs.append(
            {
                "filename": f"{index:02d}_{COMPANY_SHORT}_{year}年年度报告.pdf",
                "category": f"{year}年年度报告",
                "year": year,
                "doc_type": "annual",
                "minimum_pages": 100,
                "record": select_annual(annual_records, year),
            }
        )
    specs.append(
        {
            "filename": f"07_{COMPANY_SHORT}_2026年第一季度报告_最新季报.pdf",
            "category": (
                f"2026年第一季度报告（截至{AS_OF_DATE}最新正式季报）"
            ),
            "year": 2026,
            "doc_type": "quarter",
            "minimum_pages": 8,
            "record": select_latest_quarter(quarter_records),
        }
    )

    manifest: list[dict] = []
    renders: list[Path] = []
    for spec in specs:
        record = spec["record"]
        data, source_url, final_url = download_pdf(record["detail_url"])
        path = OUT / spec["filename"]
        path.write_bytes(data)
        pages, encrypted = validate(
            path,
            spec["year"],
            spec["doc_type"],
            spec["minimum_pages"],
        )

        doc = fitz.open(str(path))
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
        render = VERIFY / f"{path.stem}_page1.png"
        pix.save(str(render))
        doc.close()
        renders.append(render)

        item = {
            "filename": path.name,
            "category": spec["category"],
            "announcement_title": record["text"],
            "announcement_context": record["context"],
            "announcement_detail_url": record["detail_url"],
            "pdf_source_url": source_url,
            "pdf_final_url": final_url,
            "pages": pages,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "encrypted": encrypted,
            "first_page_render": render.name,
        }
        manifest.append(item)
        print("VALIDATED", json.dumps(item, ensure_ascii=False))

    thumbs: list[Image.Image] = []
    for render in renders:
        image = Image.open(render).convert("RGB")
        image.thumbnail((470, 630))
        canvas = Image.new("RGB", (500, 680), "white")
        canvas.paste(image, ((500 - image.width) // 2, 10))
        ImageDraw.Draw(canvas).text((10, 650), render.stem[:70], fill="black")
        thumbs.append(canvas)
        image.close()

    cols = 2
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 500, rows * 680), "white")
    for index, image in enumerate(thumbs):
        sheet.paste(image, ((index % cols) * 500, (index // cols) * 680))
        image.close()
    contact = VERIFY / "contact_sheet.jpg"
    sheet.save(contact, "JPEG", quality=90)
    sheet.close()

    note = f"""彩虹显示器件股份有限公司（证券简称：彩虹股份，证券代码：{STOCK_CODE}）定期报告资料包

文件范围：
1. 2020年至2025年完整年度报告，共6份；不含摘要版、英文重复版、审计报告或业绩快报。
2. 2026年第一季度报告，为截至{AS_OF_DATE}最新一份标题明确为“季度报告”的正式定期报告。

口径说明：
- 公司已披露2026年半年度报告，但半年度报告不属于标题明确的“季度报告”；因此本包按“最新季报”口径收录2026年第一季度报告。
- 截至{AS_OF_DATE}，2026年第三季度报告尚未披露。
- PDF附件来自新浪财经公司公告镜像，逐份核验公司名称、年份、报告类型、实际页数、加密状态及首页渲染。
- ZIP已执行CRC完整性检查；文件仅供个人研究与学习使用。
"""
    (OUT / "00_资料说明.txt").write_text(note, encoding="utf-8")
    (OUT / "00_文件清单及校验值.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    zip_path = OUT / "caihong_gufen_2020_2025_annual_reports_latest_quarter.zip"
    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
        archive.write(
            OUT / "00_文件清单及校验值.json",
            "00_文件清单及校验值.json",
        )
        for spec in specs:
            path = OUT / spec["filename"]
            archive.write(path, path.name)

    with zipfile.ZipFile(zip_path) as archive:
        bad_file = archive.testzip()
        if bad_file:
            raise RuntimeError(f"ZIP CRC failed at {bad_file}")

    summary = {
        "zip_filename": zip_path.name,
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "pdf_count": len(manifest),
        "total_pages": sum(item["pages"] for item in manifest),
        "reports": manifest,
    }
    (OUT / "BUILD_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
