#!/usr/bin/env python3
from __future__ import annotations

import html
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

PAGES = {
    "399121": "https://www.sgpjbg.com/sgpjbg/View.aspx?id=399121",
    "651928": "https://www.sgpjbg.com/baogao/651928.html",
}
JS_URLS = [
    "https://cache.sgpjbg.com/Master/view/view-mini-fz.js",
    "https://cache.sgpjbg.com/master/view/view2.js",
    "https://cache.sgpjbg.com/css/view/encoder.js",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}

for rid, url in PAGES.items():
    print("\n" + "#" * 140)
    print("PAGE", rid, url)
    r = requests.get(url, headers=HEADERS, timeout=(20, 120))
    r.raise_for_status()
    text = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")

    dp = soup.find(id="dp")
    print("DP", dp.get("value") if dp else None)
    print("TITLE", soup.title.get_text(" ", strip=True) if soup.title else "")

    # Print key JS variables and assignments.
    for pat in (
        r"(?:var\s+)?(?:fCount|defaultShowPage|curtotalpage|filecount|pageCount|totalPage|totalpage|FilePath|filePath|imgPath|imagePath|dp)\s*=\s*[^;]+;",
        r"document\.getElementById\(['\"]dp['\"]\)[^;]+;",
        r"\$\(['\"]#dp['\"]\)[^;]+;",
    ):
        vals = list(dict.fromkeys(re.findall(pat, text, flags=re.I)))
        print("ASSIGNMENTS", pat, len(vals))
        for val in vals[:100]:
            print("  ", html.unescape(val)[:1200])

    # Print all occurrences of target-specific fileroot base and surrounding context.
    roots = sorted(set(re.findall(r"https?://[^\"'<>\s]+/fileroot[^\"'<>\s]+", text, flags=re.I)))
    print("FILEROOT URLS", len(roots))
    for root in roots:
        if rid in root or (dp and str(dp.get("value", "")).split("/")[-1] in root):
            print("TARGET ROOT URL", html.unescape(root)[:1500])
    if dp:
        uuid = str(dp.get("value", "")).rstrip("/").split("/")[-1]
        print("UUID", uuid)
        for m in re.finditer(re.escape(uuid), text, flags=re.I):
            a = max(0, m.start() - 500)
            b = min(len(text), m.end() + 900)
            print("UUID_CONTEXT", re.sub(r"\s+", " ", html.unescape(text[a:b]))[:1600])

    # Inspect page/reader elements and every attribute, including lazy-loader attributes.
    for selector in ("#boxleft", ".outer_page", ".inner_page", "#pflashContent", "#html-reader-go-more"):
        nodes = soup.select(selector)
        print("SELECTOR", selector, "COUNT", len(nodes))
        for node in nodes[:30]:
            print("NODE", str(node)[:5000])

    # All attributes containing fileroot or obvious image/page tokens.
    hits = []
    for tag in soup.find_all(True):
        for attr, value in tag.attrs.items():
            values = value if isinstance(value, list) else [value]
            for val in values:
                s = str(val)
                low = s.lower()
                if "fileroot" in low or "page" in low or "lazy" in low or (dp and str(dp.get("value", "")).split("/")[-1] in s):
                    hits.append((tag.name, attr, urljoin(r.url, s), str(tag)[:600]))
    print("ATTR HITS", len(hits))
    for hit in hits[:300]:
        print("ATTR", hit)

for js_url in JS_URLS:
    print("\n" + "=" * 140)
    print("JS", js_url)
    r = requests.get(js_url, headers=HEADERS, timeout=(20, 120))
    print("STATUS", r.status_code, "LEN", len(r.content), r.headers.get("content-type"))
    text = r.content.decode("utf-8", errors="replace")
    for pat in (
        r"fCount",
        r"defaultShowPage",
        r"#dp",
        r"getElementById\(['\"]dp",
        r"fileroot",
        r"inner_page",
        r"outer_page",
        r"\.gif",
        r"\.png",
        r"\.jpg",
        r"encode",
        r"decode",
    ):
        matches = list(re.finditer(pat, text, flags=re.I))
        print("PATTERN", pat, "COUNT", len(matches))
        for m in matches[:30]:
            a = max(0, m.start() - 500)
            b = min(len(text), m.end() + 900)
            print("JS_CONTEXT", re.sub(r"\s+", " ", text[a:b])[:1600])
