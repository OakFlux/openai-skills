#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import runpy
from urllib.parse import urlsplit

import requests

POSITIONS = [
    15, 35, 29, 24, 33, 16, 1, 38, 10, 9, 19, 31, 40, 27, 22, 23,
    25, 13, 6, 11, 39, 18, 20, 8, 14, 21, 32, 26, 2, 30, 7, 4, 17,
    5, 3, 28, 34, 37, 12, 36,
]
MASK = "3000176000856006061501533003690027800375"


def calc_acw_sc_v2(html: str):
    match = re.search(r"arg1=['\"]([0-9A-Fa-f]+)['\"]", html or "")
    if not match:
        return None
    arg1 = match.group(1)
    if len(arg1) < max(POSITIONS):
        return None
    reordered = "".join(arg1[position - 1] for position in POSITIONS)
    result = []
    for index in range(0, min(len(reordered), len(MASK)), 2):
        value = int(reordered[index:index + 2], 16) ^ int(MASK[index:index + 2], 16)
        result.append(f"{value:02x}")
    return "".join(result)


_original_get = requests.Session.get


def patched_get(self, url, *args, **kwargs):
    response = _original_get(self, url, *args, **kwargs)
    body = response.content or b""
    content_type = response.headers.get("content-type", "").lower()
    if (
        response.status_code == 200
        and not body.startswith(b"%PDF-")
        and ("html" in content_type or body.lstrip().startswith(b"<"))
        and "arg1=" in response.text
    ):
        cookie = calc_acw_sc_v2(response.text)
        if cookie:
            host = urlsplit(response.url).hostname or "static.sse.com.cn"
            self.cookies.set("acw_sc__v2", cookie, domain=host, path="/")
            self.cookies.set("acw_sc__v2", cookie, domain=".sse.com.cn", path="/")
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("params", None)
            headers = dict(retry_kwargs.get("headers") or {})
            headers.setdefault("Referer", response.url)
            retry_kwargs["headers"] = headers
            retry = _original_get(self, response.url, *args, **retry_kwargs)
            print("ACW_RETRY", retry.status_code, len(retry.content), retry.url)
            return retry
    return response


requests.Session.get = patched_get
runpy.run_path("scripts/build_ningboport_filings.py", run_name="__main__")
