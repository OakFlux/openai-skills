#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urlencode

import pymupdf
import requests
from PIL import Image, ImageDraw
from pypdf import PdfReader

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

COMPANY = "南方精工"
FORMER_NAME = "南方轴承"
STOCK_CODE = "002553"
AS_OF_DATE = "2026-09-29"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def request(url: str, *, referer: str | None = None, timeout: int = 120) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            headers = {"Referer": referer} if referer else {}
            response = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            print("FETCH", response.status_code, len(response.content), response.url, response.headers.get("content-type"))
            if response.status_code == 200:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {response.url}")
        except Exception as exc:  # pragma: no cover - network retry path
            last_error = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def query_reports() -> list[dict]:
    params = {
        "pageSize": "200",
        "pageNo": "1",
        "qType": "0",
        "code": STOCK_CODE,
        "beginTime": "2010-01-01",
        "endTime": AS_OF_DATE,
        "fields": "",
        "industryCode": "*",
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "orgCode": "",
    }
    url = "https://reportapi.eastmoney.com/report/list?" + urlencode(params)
    response = request(url, referer=f"https://data.eastmoney.com/report/{STOCK_CODE}.html", timeout=90)
    payload = response.json()
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected Eastmoney payload: {type(rows)}")
    print("REPORT_HITS", payload.get("hits"), "ROWS", len(rows))
    normalized: list[dict] = []
    for row in rows:
        date = str(row.get("publishDate") or "")[:10]
        item = {
            "date": date,
            "broker": str(row.get("orgSName") or row.get("orgName") or "").strip(),
            "broker_full": str(row.get("orgName") or row.get("orgSName") or "").strip(),
            "title": str(row.get("title") or "").strip(),
            "authors": str(row.get("researcher") or "").strip(),
            "rating": str(row.get("emRatingName") or row.get("sRatingName") or "").strip(),
            "pages_api": int(row.get("attachPages") or 0),
            "size_kb_api": int(row.get("attachSize") or 0),
            "column": str(row.get("column") or ""),
            "info_code": str(row.get("infoCode") or "").strip(),
            "raw": row,
        }
        normalized.append(item)
        print("REPORT", json.dumps({k: item[k] for k in item if k != "raw"}, ensure_ascii=False))
    return normalized


def year_of(item: dict) -> int:
    try:
        return int(item["date"][:4])
    except Exception:
        return 0


def score(item: dict) -> int:
    title = item["title"]
    broker = item["broker"]
    year = year_of(item)
    pages = item["pages_api"]
    value = pages * 8
    for keyword, points in (
        ("公司深度", 240),
        ("深度报告", 220),
        ("首次覆盖", 200),
        ("工艺延伸促成长", 180),
        ("进口替代", 90),
        ("成长", 45),
    ):
        if keyword in title:
            value += points
    if "银河" in broker and year >= 2026:
        value += 420
    if "华鑫" in broker and year >= 2024:
        value += 360
    if "东方财富" in broker and year >= 2022:
        value += 280
    if year >= 2026:
        value += 100
    elif year >= 2024:
        value += 80
    elif year >= 2022:
        value += 50
    elif year >= 2020:
        value += 20
    return value


def choose_reports(rows: list[dict]) -> list[dict]:
    if not rows:
        raise RuntimeError("No brokerage reports returned")

    chosen: list[dict] = []

    def add_best(predicate) -> None:
        candidates = [row for row in rows if predicate(row) and row.get("info_code")]
        if not candidates:
            return
        candidates.sort(key=lambda row: (score(row), row["date"], row["pages_api"]), reverse=True)
        candidate = candidates[0]
        if candidate["info_code"] not in {x["info_code"] for x in chosen}:
            chosen.append(candidate)

    # Priority set: recent first/deep coverage from three different institutions.
    add_best(lambda row: "银河" in row["broker"] and year_of(row) >= 2026)
    add_best(lambda row: "华鑫" in row["broker"] and year_of(row) >= 2024)
    add_best(lambda row: "东方财富" in row["broker"] and year_of(row) >= 2022)

    # Fill to three with the best remaining company reports, preserving broker diversity when possible.
    ranked = sorted(rows, key=lambda row: (score(row), row["date"], row["pages_api"]), reverse=True)
    for row in ranked:
        if len(chosen) >= 3:
            break
        if not row.get("info_code") or row["info_code"] in {x["info_code"] for x in chosen}:
            continue
        if row["broker"] in {x["broker"] for x in chosen} and len(rows) > 3:
            continue
        chosen.append(row)
    for row in ranked:
        if len(chosen) >= 3:
            break
        if row.get("info_code") and row["info_code"] not in {x["info_code"] for x in chosen}:
            chosen.append(row)

    if len(chosen) < 2:
        raise RuntimeError(f"Only {len(chosen)} suitable report candidates found")
    print("CHOSEN", json.dumps([{k: x[k] for k in x if k != "raw"} for x in chosen], ensure_ascii=False, indent=2))
    return chosen[:3]


def pdf_candidates(info_code: str) -> list[str]:
    return [
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{info_code}.pdf",
    ]


def get_pdf(item: dict) -> tuple[bytes, str, str]:
    errors: list[str] = []
    referer = f"https://data.eastmoney.com/report/zw_stock.jshtml?infocode={item['info_code']}"
    for url in pdf_candidates(item["info_code"]):
        try:
            response = request(url, referer=referer, timeout=120)
            data = response.content
            if data.startswith(b"%PDF-") and len(data) > 20_000:
                return data, url, response.url
            errors.append(f"{url}: not PDF ({len(data)} bytes)")
        except Exception as exc:
            errors.append(f"{url}: {exc!r}")
    raise RuntimeError("No valid PDF for report: " + " | ".join(errors))


def safe_filename(text: str) -> str:
    text = text.replace("“", "").replace("”", "").replace("‘", "").replace("’", "")
    text = re.sub(r"[\\/:*?\"<>|\s，。；：、]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:90]


def inspect_pdf(data: bytes, path: Path) -> tuple[int, bool, str]:
    path.write_bytes(data)
    reader = PdfReader(io.BytesIO(data))
    encrypted = bool(reader.is_encrypted)
    if encrypted:
        result = reader.decrypt("")
        if not result:
            raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = len(reader.pages)
    if pages < 5:
        raise RuntimeError(f"Unexpectedly short report: {path.name}, {pages} pages")
    chunks: list[str] = []
    for index in range(min(4, pages)):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    text = "\n".join(chunks)
    if len(text.strip()) < 80:
        doc = pymupdf.open(str(path))
        text = "\n".join(doc[index].get_text("text") for index in range(min(4, doc.page_count)))
        doc.close()
    normalized = re.sub(r"\s+", "", text)
    if not any(token in normalized for token in (COMPANY, FORMER_NAME, STOCK_CODE)):
        raise RuntimeError(f"Company identity validation failed: {path.name}")
    return pages, encrypted, text


def render_page(pdf_path: Path, page_index: int, image_path: Path) -> None:
    doc = pymupdf.open(str(pdf_path))
    pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(1.35, 1.35), alpha=False)
    pix.save(str(image_path))
    doc.close()


def main() -> None:
    rows = query_reports()
    chosen = choose_reports(rows)
    manifest: list[dict] = []
    render_pairs: list[tuple[Path, Path, dict]] = []
    failures: list[dict] = []

    for index, item in enumerate(chosen, 1):
        try:
            data, source_url, final_url = get_pdf(item)
            provisional = OUT / f"temp_{index}.pdf"
            pages, encrypted, first_text = inspect_pdf(data, provisional)
            filename = (
                f"{index:02d}_{safe_filename(item['broker'])}_{COMPANY}_"
                f"{safe_filename(item['title'])}_{item['date']}_{pages}页.pdf"
            )
            pdf_path = OUT / filename
            provisional.replace(pdf_path)

            first_render = VERIFY / f"{index:02d}_page1.png"
            last_render = VERIFY / f"{index:02d}_lastpage.png"
            render_page(pdf_path, 0, first_render)
            render_page(pdf_path, pages - 1, last_render)

            record = {
                "filename": filename,
                "broker": item["broker"],
                "broker_full": item["broker_full"],
                "title": item["title"],
                "date": item["date"],
                "authors": item["authors"],
                "rating": item["rating"],
                "pages_api": item["pages_api"],
                "pages_actual": pages,
                "info_code": item["info_code"],
                "source_url": source_url,
                "final_url": final_url,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "encrypted": encrypted,
                "first_text_excerpt": re.sub(r"\s+", " ", first_text)[:800],
            }
            manifest.append(record)
            render_pairs.append((first_render, last_render, record))
            print("VALIDATED", json.dumps(record, ensure_ascii=False))
        except Exception as exc:
            failure = {
                "broker": item["broker"],
                "title": item["title"],
                "date": item["date"],
                "info_code": item["info_code"],
                "error": repr(exc),
            }
            failures.append(failure)
            print("FAILED", json.dumps(failure, ensure_ascii=False))

    # If a priority candidate failed, continue through remaining ranked reports until there are three valid PDFs.
    if len(manifest) < 3:
        used_codes = {x["info_code"] for x in chosen}
        ranked = sorted(rows, key=lambda row: (score(row), row["date"], row["pages_api"]), reverse=True)
        for item in ranked:
            if len(manifest) >= 3:
                break
            if not item.get("info_code") or item["info_code"] in used_codes:
                continue
            used_codes.add(item["info_code"])
            index = len(manifest) + 1
            try:
                data, source_url, final_url = get_pdf(item)
                provisional = OUT / f"temp_fallback_{index}.pdf"
                pages, encrypted, first_text = inspect_pdf(data, provisional)
                filename = (
                    f"{index:02d}_{safe_filename(item['broker'])}_{COMPANY}_"
                    f"{safe_filename(item['title'])}_{item['date']}_{pages}页.pdf"
                )
                pdf_path = OUT / filename
                provisional.replace(pdf_path)
                first_render = VERIFY / f"{index:02d}_page1.png"
                last_render = VERIFY / f"{index:02d}_lastpage.png"
                render_page(pdf_path, 0, first_render)
                render_page(pdf_path, pages - 1, last_render)
                record = {
                    "filename": filename,
                    "broker": item["broker"],
                    "broker_full": item["broker_full"],
                    "title": item["title"],
                    "date": item["date"],
                    "authors": item["authors"],
                    "rating": item["rating"],
                    "pages_api": item["pages_api"],
                    "pages_actual": pages,
                    "info_code": item["info_code"],
                    "source_url": source_url,
                    "final_url": final_url,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "encrypted": encrypted,
                    "first_text_excerpt": re.sub(r"\s+", " ", first_text)[:800],
                }
                manifest.append(record)
                render_pairs.append((first_render, last_render, record))
                print("VALIDATED_FALLBACK", json.dumps(record, ensure_ascii=False))
            except Exception as exc:
                print("FALLBACK_FAILED", item["info_code"], repr(exc))

    if len(manifest) < 2:
        raise RuntimeError(f"Only {len(manifest)} valid brokerage reports. Failures={failures}")

    # Build contact sheet for visual verification.
    thumbnails: list[Image.Image] = []
    for first_render, last_render, record in render_pairs:
        for image_path, label in ((first_render, "首页"), (last_render, "末页")):
            image = Image.open(image_path).convert("RGB")
            image.thumbnail((520, 700))
            canvas = Image.new("RGB", (560, 770), "white")
            canvas.paste(image, ((560 - image.width) // 2, 45))
            ImageDraw.Draw(canvas).text(
                (15, 15),
                f"{record['broker']} {record['date']} {label}（{record['pages_actual']}页）",
                fill="black",
            )
            thumbnails.append(canvas)
            image.close()
    cols = 2
    rows_count = (len(thumbnails) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 560, rows_count * 770), "white")
    for idx, image in enumerate(thumbnails):
        sheet.paste(image, ((idx % cols) * 560, (idx // cols) * 770))
        image.close()
    sheet.save(VERIFY / "contact_sheet.jpg", "JPEG", quality=90)
    sheet.close()

    metadata = {
        "company": COMPANY,
        "stock_code": STOCK_CODE,
        "as_of_date": AS_OF_DATE,
        "pdf_count": len(manifest),
        "total_pages": sum(item["pages_actual"] for item in manifest),
        "reports": manifest,
        "failures": failures,
    }
    (OUT / "00_文件清单及校验值.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    note_lines = [
        f"{COMPANY}（{STOCK_CODE}.SZ）券商研究报告资料包",
        "",
        f"收录完整PDF {len(manifest)}份，合计{metadata['total_pages']}页。",
        "优先选择近期首次覆盖或公司深度研究，并兼顾不同券商与历史研究框架。",
        "每份PDF均已核验公司名称/证券代码、实际页数、可打开状态，并渲染检查首页和末页。",
        "报告仅供研究参考，不构成投资建议。",
        "",
    ]
    for idx, item in enumerate(manifest, 1):
        note_lines.extend(
            [
                f"{idx}. {item['broker']}：《{item['title']}》",
                f"   日期：{item['date']}；分析师：{item['authors']}；评级：{item['rating']}；实际页数：{item['pages_actual']}",
                f"   SHA-256：{item['sha256']}",
                "",
            ]
        )
    (OUT / "00_资料说明.txt").write_text("\n".join(note_lines), encoding="utf-8")

    zip_path = OUT / "nanfang_jinggong_brokerage_reports.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(OUT / "00_资料说明.txt", "00_资料说明.txt")
        archive.write(OUT / "00_文件清单及校验值.json", "00_文件清单及校验值.json")
        for item in manifest:
            archive.write(OUT / item["filename"], item["filename"])
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC validation failed at {bad}")

    summary = {
        **metadata,
        "zip_filename": zip_path.name,
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "zip_entries": zipfile.ZipFile(zip_path).namelist(),
    }
    (OUT / "BUILD_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
