#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT = Path("baogaobox_probe")
OUT.mkdir(exist_ok=True)

REPORTS = {
    "guosheng_20250319": "https://www.baogaobox.com/reports/250319000052828.html",
    "tianfeng_20250511": "https://www.baogaobox.com/reports/250512000059175.html",
    "caitong_20250107": "https://www.baogaobox.com/reports/250108000045564.html",
    "changjiang_20250812": "https://www.baogaobox.com/reports/250812000071760.html",
    "report_250806": "https://www.baogaobox.com/reports/250806000071108.html",
}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}
summary = []
for key, url in REPORTS.items():
    rec = {"key": key, "url": url}
    try:
        r = requests.get(url, headers=headers, timeout=(20, 120), allow_redirects=True)
        rec.update(
            {
                "status": r.status_code,
                "final_url": r.url,
                "bytes": len(r.content),
                "content_type": r.headers.get("content-type"),
            }
        )
        print("PAGE", rec)
        (OUT / f"{key}.html").write_bytes(r.content)
        text = r.content.decode(r.encoding or "utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        rec["title"] = soup.title.get_text(" ", strip=True) if soup.title else ""
        candidates = []
        for tag in soup.find_all(True):
            for attr, val in tag.attrs.items():
                vals = val if isinstance(val, list) else [val]
                for item in vals:
                    item = str(item)
                    if not item:
                        continue
                    full = urljoin(r.url, item)
                    low = full.lower()
                    if any(token in low for token in (".pdf", ".doc", ".docx", "download", "attachment", "file", "report", "preview", "zy.baogaobox", "oss", "cos", "static")):
                        candidates.append(
                            {
                                "tag": tag.name,
                                "attr": attr,
                                "url": full,
                                "text": tag.get_text(" ", strip=True)[:200],
                            }
                        )
        for m in re.finditer(r"(?:https?:)?//[^\"'<>\s]+", text):
            raw = m.group(0)
            full = "https:" + raw if raw.startswith("//") else raw
            low = full.lower()
            if any(token in low for token in (".pdf", ".doc", ".docx", "download", "attachment", "file", "report", "preview", "zy.baogaobox", "oss", "cos")):
                candidates.append({"tag": "regex", "attr": "text", "url": full, "text": ""})
        unique = []
        seen = set()
        for c in candidates:
            u = c["url"]
            if u not in seen:
                seen.add(u)
                unique.append(c)
        rec["candidates"] = unique
        print("TITLE", rec["title"])
        for c in unique:
            print("LINK", c)
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        print("ERROR", rec)
    summary.append(rec)

(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
