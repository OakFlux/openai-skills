#!/usr/bin/env python3
from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URLS = [
    "https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/795687742795/index.phtml",
    "https://www.fxbaogao.com/detail/4736599",
    "https://www.fxbaogao.com/detail/4665308",
    "https://www.sgpjbg.com/bgdown/399121.html",
    "https://www.sgpjbg.com/baogao/399121.html",
]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}

for url in URLS:
    print("\n" + "=" * 120)
    print("URL", url)
    try:
        r = requests.get(url, headers=headers, timeout=(20, 120), allow_redirects=True)
        print("STATUS", r.status_code, "LEN", len(r.content), "FINAL", r.url, "CTYPE", r.headers.get("content-type"))
        raw = r.content
        text = None
        for enc in (r.encoding, "utf-8", "gb18030"):
            if not enc:
                continue
            try:
                text = raw.decode(enc)
                break
            except Exception:
                pass
        if text is None:
            text = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        print("TITLE", soup.title.get_text(" ", strip=True) if soup.title else "")
        found = []
        for tag in soup.find_all(["a", "iframe", "embed", "object", "script", "img", "source"]):
            for attr in ("href", "src", "data", "data-src", "data-url", "url"):
                val = tag.get(attr)
                if not val:
                    continue
                full = urljoin(r.url, val)
                label = tag.get_text(" ", strip=True)[:120]
                low = full.lower()
                if any(k in low for k in (".pdf", "download", "view", "report", "file", "attachment", "preview", "doc")) or any(k in label.lower() for k in ("下载", "pdf", "完整", "原文")):
                    found.append((tag.name, attr, full, label))
        seen = set()
        for item in found:
            if item[2] in seen:
                continue
            seen.add(item[2])
            print("LINK", item)
        # Print contexts around likely binary/document markers.
        for pattern in (r"\.pdf", r"download", r"fileUrl", r"pdfUrl", r"attachment", r"bgdown", r"view\?id", r"rptid"):
            hits = list(re.finditer(pattern, text, flags=re.I))
            print("PATTERN", pattern, "HITS", len(hits))
            for m in hits[:12]:
                start = max(0, m.start() - 220)
                end = min(len(text), m.end() + 320)
                snippet = re.sub(r"\s+", " ", text[start:end])
                print("SNIP", snippet[:700])
    except Exception as exc:
        print("ERROR", type(exc).__name__, exc)
