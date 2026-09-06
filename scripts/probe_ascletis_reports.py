#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
from pathlib import Path

import requests

API = "https://reportapi.eastmoney.com/report/list"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
OUT = Path("ascletis_probe")
OUT.mkdir(exist_ok=True)

WINDOWS = [
    ("2023-01-01", "2023-01-08"),
    ("2024-12-18", "2024-12-30"),
    ("2025-02-18", "2025-02-25"),
    ("2025-03-27", "2025-04-10"),
    ("2025-06-01", "2025-07-10"),
    ("2025-08-12", "2025-09-15"),
    ("2025-12-20", "2026-01-05"),
    ("2026-03-25", "2026-04-10"),
]
KEYWORDS = [
    "歌礼制药", "歌禮製藥", "ASCLETIS", "01672", "1672.HK",
    "全新GLP-1减重不减肌", "减重不减肌", "ASC30", "ASC47",
]


def query(begin, end, qtype, code=""):
    rows = []
    page = 1
    total = 1
    while page <= total:
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*", "beginTime": begin,
            "endTime": end, "pageNo": str(page), "fields": "",
            "qType": str(qtype), "orgCode": "", "code": code,
            "rcode": "", "p": str(page), "pageNum": str(page),
            "pageNumber": str(page),
        }
        r = S.get(API, params=params, timeout=(30, 180))
        print("QUERY", begin, end, qtype, code, page, r.status_code, len(r.content), flush=True)
        r.raise_for_status()
        payload = r.json()
        total = int(payload.get("TotalPage") or 1)
        data = payload.get("data") or []
        print("PAGE", page, "TOTAL", total, "ROWS", len(data), flush=True)
        rows.extend(data)
        page += 1
        time.sleep(0.1)
    return rows


def is_match(row):
    hay = " ".join(str(row.get(k) or "") for k in (
        "title", "stockName", "stockCode", "orgName", "orgSName", "researcher"
    )).upper()
    return any(k.upper() in hay for k in KEYWORDS)


def download_pdf(info_code):
    urls = [
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf?{int(time.time())}",
    ]
    for url in urls:
        try:
            r = S.get(url, headers={
                "User-Agent": UA,
                "Referer": "https://data.eastmoney.com/report/",
                "Accept": "application/pdf,application/octet-stream,*/*",
            }, timeout=(30, 600), allow_redirects=True)
            print("PDF", info_code, r.status_code, r.headers.get("content-type"), len(r.content), r.content[:5], flush=True)
            if r.status_code == 200 and r.content.startswith(b"%PDF-"):
                path = OUT / f"{info_code}.pdf"
                path.write_bytes(r.content)
                return {"url": r.url, "bytes": len(r.content), "path": str(path)}
        except Exception as exc:
            print("PDFERR", info_code, repr(exc), flush=True)
    return None


matches = {}
for begin, end in WINDOWS:
    for qtype in (0, 1, 2):
        for row in query(begin, end, qtype):
            if is_match(row):
                info = str(row.get("infoCode") or "")
                if not info:
                    continue
                item = {"query": [begin, end, qtype, ""], **row}
                matches[info] = item
                print("MATCH", json.dumps(item, ensure_ascii=False, default=str), flush=True)

# Direct code-filter checks, useful if broad scans suppress HK rows.
for code in ("01672", "1672", "01672.HK", "HK01672"):
    for begin, end in (("2023-01-01", "2026-09-06"),):
        try:
            for row in query(begin, end, 0, code=code):
                info = str(row.get("infoCode") or "")
                if info:
                    item = {"query": [begin, end, 0, code], **row}
                    matches[info] = item
                    print("CODE_MATCH", json.dumps(item, ensure_ascii=False, default=str), flush=True)
        except Exception as exc:
            print("CODE_QUERY_ERR", code, repr(exc), flush=True)

results = []
for info, row in matches.items():
    pdf = download_pdf(info)
    results.append({"metadata": row, "pdf": pdf})

Path("ascletis_probe_results.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
)
print("TOTAL", len(results), "PDFS", sum(1 for x in results if x["pdf"]), flush=True)
