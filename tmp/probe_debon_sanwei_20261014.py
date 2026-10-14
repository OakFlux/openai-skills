#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT = Path("debon_sanwei_probe")
OUT.mkdir(exist_ok=True)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}
URLS = [
    "https://www.sgpjbg.com/labels/jingxihuagongxingyegaikuang.html",
    "https://www.sgpjbg.com/labels/jingxihuagongxingyefazhanqushiyuce.html",
]
needle = "三维化学-公司研究报告-精细化工稳发展"
results = []
for url in URLS:
    rec = {"url": url}
    try:
        r = requests.get(url, headers=HEADERS, timeout=(20, 120), allow_redirects=True)
        rec.update({"status": r.status_code, "bytes": len(r.content), "final_url": r.url})
        print("PAGE", rec)
        text = r.content.decode(r.encoding or "utf-8", errors="replace")
        (OUT / ("label_" + str(len(results)) + ".html")).write_text(text, encoding="utf-8")
        soup = BeautifulSoup(text, "html.parser")
        matches = []
        for tag in soup.find_all("a"):
            label = tag.get_text(" ", strip=True)
            href = tag.get("href") or ""
            if "三维化学" in label or "精细化工稳发展" in label or "230620" in label:
                full = urljoin(r.url, href)
                item = {"label": label, "href": full}
                matches.append(item)
                print("MATCH", item)
        for m in re.finditer(r"三维化学.{0,300}", text, flags=re.S):
            snippet = re.sub(r"\s+", " ", m.group(0))[:1000]
            print("CONTEXT", snippet)
        rec["matches"] = matches
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        print("ERROR", rec)
    results.append(rec)
(OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
