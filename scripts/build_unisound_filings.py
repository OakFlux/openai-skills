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
from pypdf import PdfReader

OUT = Path("output")
VERIFY = OUT / "verification"
OUT.mkdir(parents=True, exist_ok=True)
VERIFY.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def fetch(url: str, *, headers=None, timeout: int = 120) -> requests.Response:
    last = None
    merged = dict(SESSION.headers)
    if headers:
        merged.update(headers)
    for attempt in range(5):
        try:
            response = SESSION.get(
                url,
                headers=merged,
                timeout=timeout,
                allow_redirects=True,
            )
            print("FETCH", response.status_code, len(response.content), response.url)
            if response.status_code == 200:
                return response
            last = RuntimeError(f"HTTP {response.status_code}: {url}")
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def parse_json_response(response: requests.Response):
    text = response.text.strip()
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        left = text.find("(")
        right = text.rfind(")")
        if left >= 0 and right > left:
            return json.loads(text[left + 1 : right])
        raise


def collect_rows(obj, rows: list[dict]) -> None:
    if isinstance(obj, str):
        try:
            collect_rows(json.loads(obj), rows)
        except Exception:  # noqa: BLE001
            return
    elif isinstance(obj, list):
        for item in obj:
            collect_rows(item, rows)
    elif isinstance(obj, dict):
        upper_keys = {str(key).upper() for key in obj}
        if "TITLE" in upper_keys and ("FILE_LINK" in upper_keys or "FILELINK" in upper_keys):
            rows.append(obj)
        for value in obj.values():
            collect_rows(value, rows)


def query_hkex(from_date: str, to_date: str) -> list[dict]:
    api = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
    combined: list[dict] = []
    for lang in ("en", "zh"):
        params = {
            "sortDir": "1",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": "1000262960",
            "documentType": "-1",
            "fromDate": from_date,
            "toDate": to_date,
            "title": "",
            "searchType": "1",
            "t1code": "-2",
            "t2Gcode": "-2",
            "t2code": "-2",
            "rowRange": "2000",
            "lang": lang,
        }
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": (
                "https://www1.hkexnews.hk/search/titlesearch.xhtml"
                f"?category=0&lang={lang.upper()}&market=SEHK&stockId=1000262960"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }
        response = SESSION.get(api, params=params, headers=headers, timeout=90)
        print("HKEX_QUERY", lang, response.status_code, len(response.content), response.url)
        response.raise_for_status()
        rows: list[dict] = []
        collect_rows(parse_json_response(response), rows)
        print("HKEX_ROWS", lang, len(rows))
        combined.extend(rows)

    dedup: dict[tuple[str, str], dict] = {}
    for row in combined:
        title = str(row.get("TITLE") or row.get("title") or "")
        link = str(
            row.get("FILE_LINK")
            or row.get("fileLink")
            or row.get("FILELINK")
            or row.get("href")
            or ""
        )
        if link:
            dedup[(title, link)] = row
    result = list(dedup.values())
    (OUT / "HKEX检索结果.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def row_title(row: dict) -> str:
    return str(row.get("TITLE") or row.get("title") or "")


def row_link(row: dict) -> str:
    value = str(
        row.get("FILE_LINK")
        or row.get("fileLink")
        or row.get("FILELINK")
        or row.get("href")
        or ""
    )
    return urljoin("https://www1.hkexnews.hk/", value)


def choose_hkex_url(rows: list[dict], include_terms: list[str], exclude_terms=()) -> str | None:
    matches = []
    for row in rows:
        title = row_title(row)
        upper = title.upper()
        if not any(term.upper() in upper for term in include_terms):
            continue
        if any(term.upper() in upper for term in exclude_terms):
            continue
        link = row_link(row)
        if ".pdf" not in link.lower():
            continue
        date = str(row.get("DATE_TIME") or row.get("dateTime") or "")
        chinese_first = 0 if ("_c.pdf" in link.lower() or any(x in title for x in ("年報", "中期", "全球發售"))) else 1
        matches.append((chinese_first, date, link, title))
    matches.sort(key=lambda item: (item[0], item[1]))
    print("HKEX_MATCHES", json.dumps(matches, ensure_ascii=False, indent=2))
    return matches[0][2] if matches else None


def normalize_pdf_candidate(url: str | None) -> str | None:
    if not url:
        return None
    value = url.strip()
    lower = value.lower()
    for suffix in (".pdf.jpg", ".pdf.jpeg", ".pdf.png", ".pdf.webp"):
        if lower.endswith(suffix):
            return value[: -len(suffix) + 4]
    return value


def collect_ir_candidates(page_url: str) -> list[str]:
    response = fetch(page_url, timeout=60)
    soup = BeautifulSoup(response.text, "html.parser")
    candidates: list[str] = []
    for tag in soup.find_all(True):
        for attr in ("href", "src", "data-src", "data-original", "data-url", "data-file"):
            value = tag.get(attr)
            if not isinstance(value, str) or not value.strip():
                continue
            candidate = normalize_pdf_candidate(urljoin(response.url, value.strip()))
            if candidate and ".pdf" in candidate.lower():
                candidates.append(candidate)
    pattern = r"https?://[^\"'<>\s]+\.PDF(?:\.jpg|\.jpeg|\.png|\.webp)?(?:\?[^\"'<>\s]*)?"
    for match in re.findall(pattern, response.text, re.I):
        candidate = normalize_pdf_candidate(match)
        if candidate:
            candidates.append(candidate)
    output: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            output.append(candidate)
            seen.add(candidate)
    print("IR_CANDIDATES", page_url, json.dumps(output, ensure_ascii=False, indent=2))
    return output


def unique_urls(urls) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        result.append(url)
        seen.add(url)
    return result


def validate_and_save(
    filename: str,
    title: str,
    category: str,
    candidate_urls,
    min_pages: int,
    required_terms: list[str],
):
    errors = []
    for url in unique_urls(candidate_urls):
        try:
            response = fetch(
                url,
                headers={"Referer": "https://www.hkexnews.hk/"},
                timeout=180,
            )
            data = response.content
            if not data.startswith(b"%PDF-"):
                raise RuntimeError(f"not a PDF, head={data[:32]!r}")
            if len(data) < 50_000:
                raise RuntimeError(f"PDF too small: {len(data)} bytes")

            path = OUT / filename
            path.write_bytes(data)
            reader = PdfReader(str(path))
            if reader.is_encrypted and not reader.decrypt(""):
                raise RuntimeError("password-protected PDF")
            pages = len(reader.pages)
            if pages < min_pages:
                raise RuntimeError(f"unexpected page count: {pages}")

            text_chunks = []
            for index in range(min(4, pages)):
                try:
                    text_chunks.append(reader.pages[index].extract_text() or "")
                except Exception:  # noqa: BLE001
                    pass
            identity_text = " ".join(text_chunks).upper()
            if required_terms and not any(term.upper() in identity_text for term in required_terms):
                raise RuntimeError("identity validation failed")

            document = fitz.open(str(path))
            pixmap = document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            render_path = VERIFY / f"{path.stem}_page1.png"
            pixmap.save(str(render_path))
            document.close()

            record = {
                "filename": filename,
                "title": title,
                "category": category,
                "source_url": url,
                "final_url": response.url,
                "pages": pages,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "first_page_render": render_path.name,
            }
            print("VALIDATED", json.dumps(record, ensure_ascii=False))
            return path, record
        except Exception as exc:  # noqa: BLE001
            errors.append({"url": url, "error": repr(exc)})
            print("CANDIDATE_FAILED", url, repr(exc))
            (OUT / filename).unlink(missing_ok=True)

    raise RuntimeError(
        f"No valid PDF candidate for {category}: {json.dumps(errors, ensure_ascii=False)}"
    )


def main() -> None:
    hkex_rows = query_hkex("20250601", "20260927")

    annual_hkex = choose_hkex_url(
        hkex_rows,
        ["2025 ANNUAL REPORT", "2025年報", "2025年度報告"],
        ["RESULTS", "業績", "业绩"],
    )
    prospectus_hkex = choose_hkex_url(
        hkex_rows,
        ["GLOBAL OFFERING", "全球發售", "招股章程"],
        ["FORMAL NOTICE", "正式通告"],
    )
    interim_hkex = choose_hkex_url(
        hkex_rows,
        ["2026 INTERIM REPORT", "2026年中期報告", "中期報告2026"],
        ["RESULTS", "業績", "业绩"],
    )

    listing_candidates = collect_ir_candidates("https://ir.unisound.com/prospectus/index.html?lang=en")
    report_candidates = collect_ir_candidates("https://ir.unisound.com/report/index.html?lang=en")
    announcement_candidates = collect_ir_candidates("https://ir.unisound.com/announcement/index.html?lang=en")

    documents = []
    documents.append(
        validate_and_save(
            "01_云知声_2025年年度报告.pdf",
            "云知声智能科技股份有限公司 - 2025年年度报告",
            "年度报告",
            [
                annual_hkex,
                "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0429/2026042906218.pdf",
                *report_candidates,
                "https://ir.unisound.com/uploads/ir-docs/iis/9678/uploads/iis/2026/12137283-0.PDF",
            ],
            min_pages=80,
            required_terms=["UNISOUND", "雲知聲", "云知声"],
        )
    )
    documents.append(
        validate_and_save(
            "02_云知声_最终版招股说明书_2025-06-20.pdf",
            "云知声智能科技股份有限公司 - 全球发售（最终版招股说明书）",
            "招股说明书",
            [
                prospectus_hkex,
                *listing_candidates,
                "https://ir.unisound.com/uploads/ir-docs/iis/9678/uploads/iis/2025/11719671-0.PDF",
            ],
            min_pages=300,
            required_terms=["UNISOUND", "雲知聲", "云知声"],
        )
    )
    documents.append(
        validate_and_save(
            "03_云知声_2026年中期报告_最新定期财报.pdf",
            "云知声智能科技股份有限公司 - 2026年中期报告",
            "最新定期财报",
            [interim_hkex, *announcement_candidates, *report_candidates],
            min_pages=30,
            required_terms=["UNISOUND", "雲知聲", "云知声"],
        )
    )

    manifest = [record for _, record in documents]
    note = """云知声智能科技股份有限公司（09678.HK）官方披露文件资料包

文件范围：
1. 2025年年度报告。公司于2025年6月30日在香港联交所主板上市；截至2026年9月27日，上市后正式年度报告仅此一份。
2. 2025年6月20日最终版招股说明书（全球发售）。未重复收录申请版本或聆讯后资料集。
3. 2026年中期报告，为截至2026年9月27日最新正式定期财务报告。

说明：
- 香港主板发行人通常披露年度报告和中期报告，并不强制发布季度报告，因此以2026年中期报告对应“最新季报/最新定期财报”。
- 文件从香港交易所披露易或云知声官方投资者关系网站下载，来源URL、实际页数、文件大小和SHA-256校验值均记录在清单中。
- PDF已完成文件头、页数、加密状态、公司名称及首页渲染检查。
- 仅供个人研究与学习使用，请遵守原始文件的版权及免责声明。
"""
    (OUT / "资料说明.txt").write_text(note, encoding="utf-8")
    (OUT / "文件清单及校验值.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    zip_path = OUT / "云知声_全部年报_招股说明书_最新定期财报.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, _ in documents:
            archive.write(path, path.name)
        archive.write(OUT / "资料说明.txt", "资料说明.txt")
        archive.write(OUT / "文件清单及校验值.json", "文件清单及校验值.json")
    with zipfile.ZipFile(zip_path) as archive:
        bad_file = archive.testzip()
        if bad_file:
            raise RuntimeError(f"ZIP CRC validation failed at {bad_file}")

    summary = {
        "zip": zip_path.name,
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "reports": manifest,
    }
    (OUT / "BUILD_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
