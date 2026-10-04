#!/usr/bin/env python3
import json
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})

pages = []
for code in ("832786", "920786"):
    for page_type in ("ndbg", "yjdbg", "sjdbg"):
        pages.append((f"{code}_{page_type}", f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/{code}/page_type/{page_type}.phtml"))
    pages.append((f"{code}_prospectus", f"https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_RaiseExplanation/stockid/{code}.phtml"))

results = {}
for label, url in pages:
    try:
        response = session.get(url, timeout=90, allow_redirects=True)
        response.encoding = response.apparent_encoding or "gb18030"
        soup = BeautifulSoup(response.text, "html.parser")
        links = []
        for anchor in soup.find_all("a", href=True):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            href = urljoin(response.url, anchor["href"])
            if any(term in text for term in ("年度报告", "季度报告", "一季度报告", "三季度报告", "招股说明书")) or "vCB_AllBulletinDetail" in href or "vISSUE_RaiseExplanationDetail" in href:
                links.append({"text": text, "href": href})
        dedup = []
        seen = set()
        for item in links:
            key = (item["text"], item["href"])
            if key not in seen:
                seen.add(key)
                dedup.append(item)
        results[label] = {"status": response.status_code, "final_url": response.url, "links": dedup}
        print(label, response.status_code, len(response.content), response.url)
        print(json.dumps(dedup, ensure_ascii=False, indent=2))
    except Exception as exc:
        results[label] = {"error": repr(exc)}
        print("ERROR", label, repr(exc))

Path("output").mkdir(exist_ok=True)
Path("output/knight_dairy_probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
