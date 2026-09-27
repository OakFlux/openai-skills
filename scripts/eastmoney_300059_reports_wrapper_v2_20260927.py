from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import requests

CODE = "300059"
COMPANY = "东方财富"

original_get = requests.Session.get
resolver = requests.Session()
resolver.trust_env = False
resolver.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Referer": "https://www.cninfo.com.cn/",
        "Accept": "application/json,text/plain,*/*",
    }
)

payloads: list[Any] = []
for url in [
    "https://www.cninfo.com.cn/new/data/szse_stock.json",
    "https://www.cninfo.com.cn/new/data/stock.json",
]:
    try:
        response = original_get(resolver, url, timeout=(30, 180), allow_redirects=True)
        print("STOCK_LIST", response.status_code, len(response.content), response.url, flush=True)
        response.raise_for_status()
        payloads.append(response.json())
    except Exception as exc:
        print("STOCK_LIST_ERR", url, repr(exc), flush=True)

Path("cninfo_stock_lists.json").write_text(
    json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8"
)

matches: list[dict[str, Any]] = []


def walk(obj: Any) -> None:
    if isinstance(obj, dict):
        blob = json.dumps(obj, ensure_ascii=False)
        code = str(
            obj.get("code")
            or obj.get("secCode")
            or obj.get("stockCode")
            or obj.get("SECCODE")
            or ""
        )
        name = str(
            obj.get("zwjc")
            or obj.get("secName")
            or obj.get("stockName")
            or obj.get("name")
            or ""
        )
        org_id = obj.get("orgId") or obj.get("orgID") or obj.get("orgid")
        if org_id and (CODE in code or CODE in blob) and (COMPANY in name or COMPANY in blob):
            matches.append({"code": CODE, "zwjc": COMPANY, "orgId": str(org_id)})
        for value in obj.values():
            walk(value)
    elif isinstance(obj, list):
        for value in obj:
            walk(value)


for payload in payloads:
    walk(payload)

if not matches:
    raise RuntimeError("Could not resolve CNInfo orgId for 东方财富 300059 from official stock list")

fake_payload = matches[:5]
print("RESOLVED_STOCK", json.dumps(fake_payload, ensure_ascii=False), flush=True)


class FakeResponse:
    def __init__(self, url: str, payload: Any):
        self.url = url
        self.status_code = 200
        self._payload = payload
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def patched_get(self: requests.Session, url: str, *args: Any, **kwargs: Any):
    if "/new/information/topSearch/query" in url:
        return FakeResponse(url, fake_payload)
    return original_get(self, url, *args, **kwargs)


requests.Session.get = patched_get
runpy.run_path("scripts/eastmoney_300059_reports_20260927.py", run_name="__main__")
