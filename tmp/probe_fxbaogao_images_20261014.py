#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

REPORTS = [
    {
        "id": "4665308",
        "name": "财通证券-三业联动发展业绩或迎拐点",
        "dates": ["2025/01/07", "2025/01/06", "2025/01/08"],
        "expected_pages": 17,
        "referer": "https://www.fxbaogao.com/detail/4665308",
    },
    {
        "id": "4736599",
        "name": "国盛证券-硫磺回收与正丙醇双龙头",
        "dates": ["2025/03/18", "2025/03/19", "2025/03/17"],
        "expected_pages": 45,
        "referer": "https://www.fxbaogao.com/detail/4736599",
    },
]

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    }
)

out = Path("fxbaogao_probe")
out.mkdir(exist_ok=True)

for report in REPORTS:
    print("\n" + "=" * 120)
    print("REPORT", report["id"], report["name"])
    found_any = False
    for date_path in report["dates"]:
        print("DATE PATH", date_path)
        consecutive = 0
        for page in range(1, report["expected_pages"] + 1):
            candidates = [
                f"https://public.fxbaogao.com/report-image/{date_path}/{report['id']}-{page}.png",
                f"https://public.fxbaogao.com/report-image/{date_path}/{report['id']}-{page}.jpg",
                f"https://public.fxbaogao.com/report-image/{date_path}/{report['id']}-{page}.webp",
                f"https://public.fxbaogao.com/report-image/{date_path}/{report['id']}_{page}.png",
            ]
            ok = False
            for url in candidates:
                try:
                    response = session.get(
                        url,
                        headers={"Referer": report["referer"]},
                        timeout=(15, 60),
                        allow_redirects=True,
                    )
                    ctype = response.headers.get("content-type", "")
                    if response.status_code != 200 or len(response.content) < 10_000:
                        continue
                    try:
                        image = Image.open(BytesIO(response.content))
                        image.verify()
                        image2 = Image.open(BytesIO(response.content))
                        width, height = image2.size
                        fmt = image2.format
                    except Exception:
                        continue
                    if width < 500 or height < 500:
                        continue
                    sha = hashlib.sha256(response.content).hexdigest()
                    print(
                        f"OK page={page} date={date_path} url={response.url} "
                        f"bytes={len(response.content)} type={ctype} format={fmt} "
                        f"size={width}x{height} sha256={sha}"
                    )
                    if page <= 2:
                        suffix = "." + str(fmt or "png").lower().replace("jpeg", "jpg")
                        (out / f"{report['id']}_{page}{suffix}").write_bytes(response.content)
                    ok = True
                    found_any = True
                    consecutive += 1
                    break
                except Exception as exc:
                    print(f"ERROR page={page} url={url}: {type(exc).__name__}: {exc}")
            if not ok:
                print(f"MISS page={page} date={date_path}")
                # Once a valid run started, two consecutive misses imply the report ended.
                if consecutive > 0:
                    break
        if consecutive:
            print(f"CONTIGUOUS PAGES FOR {date_path}: {consecutive}")
            break
    if not found_any:
        print("NO VALID PAGE IMAGES FOUND")
