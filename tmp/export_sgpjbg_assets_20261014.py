#!/usr/bin/env python3
from pathlib import Path
import json
import requests

OUT = Path("sgpjbg_assets")
OUT.mkdir(exist_ok=True)

urls = {
    "report_399121.html": "https://www.sgpjbg.com/sgpjbg/View.aspx?id=399121",
    "report_651928.html": "https://www.sgpjbg.com/baogao/651928.html",
    "view-mini-fz.js": "https://cache.sgpjbg.com/Master/view/view-mini-fz.js",
    "encoder.js": "https://cache.sgpjbg.com/css/view/encoder.js",
    "view2.js": "https://cache.sgpjbg.com/master/view/view2.js",
    "read.css": "https://cache.sgpjbg.com/css/view/read.css",
}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}
manifest = []
for name, url in urls.items():
    try:
        r = requests.get(url, headers=headers, timeout=(20, 120), allow_redirects=True)
        record = {
            "name": name,
            "url": url,
            "status": r.status_code,
            "final_url": r.url,
            "content_type": r.headers.get("content-type"),
            "bytes": len(r.content),
        }
        manifest.append(record)
        print(record)
        if r.status_code == 200:
            (OUT / name).write_bytes(r.content)
    except Exception as exc:
        manifest.append({"name": name, "url": url, "error": f"{type(exc).__name__}: {exc}"})
        print(manifest[-1])

(OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
