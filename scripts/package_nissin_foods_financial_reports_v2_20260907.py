#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_nissin_foods_financial_reports_20260907 as base


def select_documents_with_quarterly(rows: list[dict]) -> list[dict]:
    selected = base.select_documents(rows)

    quarterly_candidates = []
    for row in rows:
        title = row["title_norm"]
        three_months = (
            "THREEMONTHSENDED31MARCH2026" in title
            or "截至2026年3月31日止三個月" in title
            or "截至2026年3月31日止三个月" in title
        )
        company_financials = (
            "UNAUDITEDCONSOLIDATEDFINANCIALINFORMATIONOFTHECOMPANY" in title
            or "本公司" in title
            or "未經審核綜合財務資料" in title
            or "未经审核综合财务资料" in title
        )
        if three_months and company_financials and row["release_dt"].year == 2026:
            quarterly_candidates.append(row)

    print(
        "QUARTERLY CANDIDATES",
        [(r["lang"], r["release_text"], r["title"], r["url"]) for r in quarterly_candidates],
        flush=True,
    )
    if not quarterly_candidates:
        raise RuntimeError("未找到日清食品2026年第一季度财务资料")

    quarterly = {
        "kind": "第一季度报告",
        "year": 2026,
        "filename": "日清食品_2026年第一季度财务资料_最新季度报告.pdf",
        "row": base.choose(quarterly_candidates),
        "minimum_pages": 8,
    }

    # Six annual reports, latest full interim report, Q1 report, then latest interim-results disclosure.
    insert_at = len(selected) - 1 if selected and selected[-1]["kind"] == "中期业绩公告" else len(selected)
    selected.insert(insert_at, quarterly)

    for index, item in enumerate(selected, 1):
        item["filename"] = f"{index:02d}_" + re.sub(r"^\d{2}_", "", item["filename"])

    print("FINAL SELECTED DOCUMENTS", flush=True)
    for item in selected:
        row = item["row"]
        print(item["kind"], item["year"], row["lang"], row["release_text"], row["title"], row["url"], flush=True)
    return selected


def validate_pdf_with_quarterly(path: Path, item: dict) -> dict:
    metadata = base.validate_pdf(path, item)
    if item["kind"] != "第一季度报告":
        return metadata

    reader = PdfReader(str(path), strict=False)
    text_parts = []
    for page in reader.pages:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:
            pass
    text = base.compact("\n".join(text_parts))
    quarterly_ok = any(
        marker in text
        for marker in (
            "QUARTERLYRESULTS",
            "THREEMONTHSENDED31MARCH2026",
            "截至2026年3月31日止三個月",
            "截至2026年3月31日止三个月",
            "第一季度",
        )
    )
    if not quarterly_ok:
        raise RuntimeError(f"{path.name} 未识别到第一季度财务资料标记")
    metadata["type_text_verified"] = True
    return metadata


def main() -> None:
    if base.WORK.exists():
        shutil.rmtree(base.WORK)
    if base.DIST.exists():
        shutil.rmtree(base.DIST)
    base.PDF_DIR.mkdir(parents=True, exist_ok=True)
    base.RENDER_DIR.mkdir(parents=True, exist_ok=True)
    base.DIST.mkdir(parents=True, exist_ok=True)

    session = base.build_session()
    stock_id = base.find_stock_id(session)
    print("USING STOCK ID", stock_id, flush=True)
    rows = base.gather_records(session, stock_id)
    selected = select_documents_with_quarterly(rows)

    records = []
    for sequence, item in enumerate(selected, 1):
        data, resolved_url = base.fetch_pdf(session, item["row"]["url"])
        destination = base.PDF_DIR / item["filename"]
        destination.write_bytes(data)
        metadata = validate_pdf_with_quarterly(destination, item)
        record = {
            "sequence": sequence,
            "filename": item["filename"],
            "year": item["year"],
            "kind": item["kind"],
            "official_title": item["row"]["title"],
            "release_time": item["row"]["release_text"],
            "language": "繁体中文" if item["row"]["lang"] == "ZH" else "英文",
            "requested_url": item["row"]["url"],
            "resolved_url": resolved_url,
            **metadata,
        }
        records.append(record)
        print("VERIFIED", json.dumps(record, ensure_ascii=False), flush=True)

    if len(records) != 9:
        raise RuntimeError(f"文件数量异常：预期9份，实际{len(records)}份")
    if len({record["sha256"] for record in records}) != len(records):
        raise RuntimeError("检测到重复PDF")
    if [r["year"] for r in records if r["kind"] == "年度报告"] != list(range(2020, 2026)):
        raise RuntimeError("年报年份不完整")
    if not any(r["kind"] == "第一季度报告" and r["year"] == 2026 for r in records):
        raise RuntimeError("缺少2026年第一季度报告")

    base.make_package(records, selected)


if __name__ == "__main__":
    main()
