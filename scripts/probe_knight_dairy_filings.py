#!/usr/bin/env python3
import json
from pathlib import Path
import requests

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Referer": "https://data.eastmoney.com/notices/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})

api = "https://np-anotice-stock.eastmoney.com/api/security/ann"
results = {}
for code in ("832786", "920786"):
    rows = []
    for page in range(1, 16):
        params = {
            "sr": "-1",
            "page_size": "100",
            "page_index": str(page),
            "ann_type": "A",
            "client_source": "web",
            "stock_list": code,
        }
        response = session.get(api, params=params, timeout=90)
        print("API", code, page, response.status_code, len(response.content), response.url)
        response.raise_for_status()
        obj = response.json()
        data = obj.get("data") or {}
        page_rows = data.get("list") or []
        print("PAGE_ROWS", len(page_rows), "TOTAL_HITS", data.get("total_hits"), "TOTAL_PAGES", data.get("total_pages"))
        rows.extend(page_rows)
        if not page_rows or page >= int(data.get("total_pages") or 1):
            break
    matches = []
    for row in rows:
        title = str(row.get("title") or row.get("notice_title") or "")
        if any(term in title for term in ("年度报告", "招股说明书", "第一季度报告", "一季度报告", "第三季度报告", "三季度报告")):
            matches.append(row)
            print("MATCH", code, json.dumps(row, ensure_ascii=False))
    results[code] = matches

Path("output").mkdir(exist_ok=True)
Path("output/knight_dairy_probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
