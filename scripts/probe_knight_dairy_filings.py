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

base = "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/920786/page_type/ndbg.phtml"
pages = [("annual_plain", base)]
for number in range(1, 8):
    pages.append((f"annual_p_{number}", base + f"?p={number}"))
    pages.append((f"annual_page_{number}", base + f"?page={number}"))
pages.extend([
    ("quarter_q1", "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/920786/page_type/yjdbg.phtml"),
    ("quarter_q3", "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/920786/page_type/sjdbg.phtml"),
    ("prospectus", "https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_RaiseExplanation/stockid/920786.phtml"),
])

results = {}
for label, url in pages:
    response = session.get(url, timeout=90, allow_redirects=True)
    response.encoding = response.apparent_encoding or "gb18030"
    soup = BeautifulSoup(response.text, "html.parser")
    links = []
    pagination = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(response.url, anchor["href"])
        if "vCB_AllBulletinDetail" in href or "vISSUE_RaiseExplanationDetail" in href:
            links.append({"text": text, "href": href})
        if "page=" in href or "?p=" in href:
            pagination.append({"text": text, "href": href})
    dedup = []
    seen = set()
    for item in links:
        key = (item["text"], item["href"])
        if key not in seen:
            seen.add(key)
            dedup.append(item)
    results[label] = {
        "status": response.status_code,
        "final_url": response.url,
        "links": dedup,
        "pagination": pagination[:50],
    }
    print("PAGE", label, response.status_code, len(response.content), response.url)
    print(json.dumps(dedup, ensure_ascii=False, indent=2))
    if pagination:
        print("PAGINATION", json.dumps(pagination[:20], ensure_ascii=False, indent=2))

Path("output").mkdir(exist_ok=True)
Path("output/knight_dairy_probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
