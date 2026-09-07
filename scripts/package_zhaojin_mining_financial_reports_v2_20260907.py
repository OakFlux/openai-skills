#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import time
from pathlib import Path

from pypdf import PdfReader

import package_zhaojin_mining_financial_reports_20260907 as job


def strict_find_stock_id(session) -> str:
    """Resolve HKEX stockId strictly from ticker 01818, never from reused issuer names."""
    exact_matches = []
    for endpoint in job.base.STOCK_LISTS:
        response = session.get(endpoint, params={"_": int(time.time() * 1000)}, timeout=(30, 180))
        print("STRICT STOCK LIST", response.status_code, len(response.content), response.url, flush=True)
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload.get("data") or payload.get("result") or []
        for row in rows:
            raw_code = str(row.get("c") or row.get("code") or "").strip()
            code = raw_code.zfill(5)
            if code == job.STOCK_CODE:
                exact_matches.append(row)
                print("STRICT STOCK MATCH", json.dumps(row, ensure_ascii=False), flush=True)

    ids = [str(row.get("i") or row.get("id") or "") for row in exact_matches if row.get("i") or row.get("id")]
    if not ids:
        raise RuntimeError(f"未能从港交所股份清单严格匹配招金矿业（{job.STOCK_CODE}）")
    chosen = max(set(ids), key=ids.count)
    if len(set(ids)) != 1:
        raise RuntimeError(f"招金矿业股份代码对应多个 stockId：{sorted(set(ids))}")
    return chosen


_original_validate_pdf = job.validate_pdf


def strict_validate_pdf(path: Path, item: dict) -> dict:
    """Run all structural checks, then independently require Zhaojin issuer identity."""
    metadata = _original_validate_pdf(path, item)
    reader = PdfReader(str(path), strict=False)
    pages = len(reader.pages)
    text = job.base.compact(job.base.extract_probe(reader, pages))
    issuer_markers = (
        job.COMPANY_CN,
        job.COMPANY_CN_SIMPLIFIED,
        job.COMPANY_EN,
        "招金礦業",
        "招金矿业",
        "ZHAOJINMINING",
        "01818",
    )
    issuer_ok = any(job.base.compact(marker) in text for marker in issuer_markers)
    print("STRICT ISSUER VALIDATION", path.name, issuer_ok, "text_chars", len(text), flush=True)
    if not issuer_ok:
        raise RuntimeError(f"{path.name} 未在可提取文本中严格识别到招金矿业名称或股份代码01818")
    metadata["company_text_verified"] = True
    return metadata


job.base.find_stock_id = strict_find_stock_id
job.validate_pdf = strict_validate_pdf


if __name__ == "__main__":
    job.main()
