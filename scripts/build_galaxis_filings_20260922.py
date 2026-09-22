#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import httpx
from pypdf import PdfReader

OUT = Path("output")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://www1.hkexnews.hk"
API = BASE + "/search/titleSearchServlet.do"
STOCK_ID = "1000296002"

client = httpx.Client(
    http2=True,
    follow_redirects=True,
    timeout=httpx.Timeout(90.0, connect=30.0),
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    },
)


def request(url: str, *, params=None, timeout: int = 90) -> httpx.Response:
    last = None
    for attempt in range(5):
        try:
            response = client.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
            last = RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def normalize_rows(obj):
    if isinstance(obj, str):
        try:
            return normalize_rows(json.loads(obj))
        except Exception:
            return []
    if isinstance(obj, list):
        return [item for item in obj if isinstance(item, dict)]
    if isinstance(obj, dict):
        for key in ("result", "data", "rows", "records", "items"):
            if key in obj:
                rows = normalize_rows(obj[key])
                if rows:
                    return rows
        for value in obj.values():
            rows = normalize_rows(value)
            if rows:
                return rows
    return []


def query(lang: str, t2code: str = "-2"):
    params = {
        "sortDir": "1",
        "sortByOptions": "DateTime",
        "category": "0",
        "market": "SEHK",
        "stockId": STOCK_ID,
        "documentType": "-1",
        "fromDate": "20250101",
        "toDate": "20260922",
        "title": "",
        "searchType": "1",
        "t1code": "40000" if t2code != "-2" else "-2",
        "t2Gcode": "-2",
        "t2code": t2code,
        "rowRange": "2000",
        "lang": lang,
    }
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": BASE
        + "/search/titlesearch.xhtml?category=0&lang="
        + ("ZH" if lang == "zh" else "EN")
        + "&market=SEHK&stockId="
        + STOCK_ID,
        "X-Requested-With": "XMLHttpRequest",
    }
    response = client.get(API, params=params, headers=headers, timeout=60)
    print("QUERY", lang, t2code, response.status_code, len(response.content), response.url)
    response.raise_for_status()
    rows = normalize_rows(response.json())
    print("ROWS", lang, t2code, len(rows))
    for row in rows[:30]:
        print(
            "ROW",
            json.dumps(
                {
                    key: row.get(key)
                    for key in (
                        "DATE_TIME",
                        "STOCK_CODE",
                        "STOCK_NAME",
                        "TITLE",
                        "FILE_LINK",
                        "FILE_INFO",
                        "T2_CODE",
                    )
                },
                ensure_ascii=False,
            ),
        )
    return rows


def link_of(row: dict) -> str:
    link = (
        row.get("FILE_LINK")
        or row.get("fileLink")
        or row.get("href")
        or row.get("url")
        or ""
    )
    return urljoin(BASE + "/", link) if link else ""


def title_of(row: dict) -> str:
    return str(row.get("TITLE") or row.get("title") or "").strip()


category_rows = {}
all_rows = {}
for language in ("zh", "en"):
    for category_code in ("40200", "40300", "40500", "-2"):
        try:
            rows = query(language, category_code)
        except Exception as exc:
            print("QUERY_FAILED", language, category_code, repr(exc))
            rows = []
        category_rows[(language, category_code)] = rows
        if category_code == "-2":
            all_rows[language] = rows


def choose_rows(code: str, keywords, exclude=()):
    candidates = []
    for language_rank, language in enumerate(("zh", "en")):
        rows = category_rows.get((language, code), []) or all_rows.get(language, [])
        for row in rows:
            title = title_of(row)
            upper = title.upper()
            if keywords and not any(keyword.upper() in upper for keyword in keywords):
                continue
            if any(term.upper() in upper for term in exclude):
                continue
            link = link_of(row)
            if ".pdf" not in link.lower():
                continue
            info = str(row.get("FILE_INFO") or "")
            size_match = re.search(r"([0-9.]+)\s*(MB|KB)", info, re.I)
            size_score = 0.0
            if size_match:
                value = float(size_match.group(1))
                size_score = value * (1024 if size_match.group(2).upper() == "MB" else 1)
            date = str(row.get("DATE_TIME") or row.get("dateTime") or "")
            candidates.append((language_rank, -size_score, date, row))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in candidates]


annual_candidates = choose_rows("40200", ["年報", "年度報告", "ANNUAL REPORT"])
interim_candidates = choose_rows(
    "40300",
    ["中期報告", "INTERIM REPORT"],
    exclude=["業績", "RESULTS", "公告", "ANNOUNCEMENT"],
)
prospectus_candidates = choose_rows(
    "40500",
    ["全球發售", "招股章程", "GLOBAL OFFERING", "PROSPECTUS"],
    exclude=["正式通告", "FORMAL NOTICE"],
)

fallback = {
    "prospectus": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0316/2026031600014_c.pdf",
    "annual": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0427/2026042703313_c.pdf",
    "annual_en": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0427/2026042703312.pdf",
    "prospectus_en": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0316/2026031600013.pdf",
}

docs = []


def candidate_urls(rows, extras=()):
    seen = set()
    output = []
    for row in rows:
        url = link_of(row)
        if url and url not in seen:
            output.append((url, row))
            seen.add(url)
    for url in extras:
        if url and url not in seen:
            output.append((url, {}))
            seen.add(url)
    return output


def download_first(filename, title, category, candidates, min_pages=5):
    errors = []
    for url, row in candidates:
        try:
            print("TRY_DOWNLOAD", category, url)
            response = request(url, timeout=120)
            data = response.content
            if not data.startswith(b"%PDF-"):
                raise RuntimeError(f"not PDF head={data[:32]!r}")
            if len(data) < 50000:
                raise RuntimeError(f"PDF too small: {len(data)}")
            path = OUT / filename
            path.write_bytes(data)
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                raise RuntimeError("encrypted PDF")
            pages = len(reader.pages)
            if pages < min_pages:
                raise RuntimeError(f"unexpected page count {pages}")
            first_text = ""
            try:
                first_text = " ".join((reader.pages[0].extract_text() or "").split())[:1000]
            except Exception:
                pass
            record = {
                "filename": filename,
                "title": title,
                "category": category,
                "source_title": title_of(row),
                "source_url": url,
                "final_url": str(response.url),
                "bytes": len(data),
                "pages": pages,
                "sha256": hashlib.sha256(data).hexdigest(),
                "first_page_text_excerpt": first_text,
            }
            docs.append((path, record))
            print("VALIDATED", json.dumps(record, ensure_ascii=False))
            return record
        except Exception as exc:
            errors.append({"url": url, "error": repr(exc)})
            print("DOWNLOAD_FAILED", url, repr(exc))
            (OUT / filename).unlink(missing_ok=True)
    raise RuntimeError(
        f"No valid candidate for {category}: {json.dumps(errors, ensure_ascii=False)}"
    )


download_first(
    "01_凯乐士科技_2025年年报.pdf",
    "浙江凯乐士科技集团股份有限公司 - 2025年年度报告",
    "年报",
    candidate_urls(annual_candidates, [fallback["annual"], fallback["annual_en"]]),
    min_pages=50,
)

download_first(
    "02_凯乐士科技_招股说明书_2026-03-16.pdf",
    "浙江凯乐士科技集团股份有限公司 - 全球发售（最终版招股说明书）",
    "招股说明书",
    candidate_urls(
        prospectus_candidates,
        [fallback["prospectus"], fallback["prospectus_en"]],
    ),
    min_pages=200,
)

download_first(
    "03_凯乐士科技_2026年中期报告_最新定期财报.pdf",
    "浙江凯乐士科技集团股份有限公司 - 2026年中期报告",
    "最新定期财报",
    candidate_urls(interim_candidates),
    min_pages=20,
)

manifest = [record for _, record in docs]
note = """凯乐士科技（浙江凯乐士科技集团股份有限公司，02729.HK）官方披露文件资料包

文件范围：
1. 2025年年度报告。公司于2026年3月24日上市，截至2026年9月22日，上市后正式年度报告仅此一份。
2. 2026年3月16日最终版招股说明书（全球发售）。
3. 2026年中期报告，为截至2026年9月22日最新正式定期财务报告。

说明：
- 香港主板发行人通常披露年度报告和中期报告，并不强制发布季度报告，因此以2026年中期报告对应“最新季报/最新定期财报”。
- 文件均从香港交易所披露易公开系统下载，并保留来源URL及SHA-256校验值。
- 申请版本和聆讯后资料集未重复收录；最终版招股说明书已取代此前版本。
- 仅供个人研究与学习使用，请遵守原始文件的版权及免责声明。
"""
(OUT / "资料说明.txt").write_text(note, encoding="utf-8")
(OUT / "文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = OUT / "凯乐士科技_年报_招股说明书_最新定期财报.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path, _ in docs:
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
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("FINAL_SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))
