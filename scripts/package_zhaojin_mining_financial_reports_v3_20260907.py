#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re

import package_zhaojin_mining_financial_reports_v2_20260907 as strict

job = strict.job


def canonical_annual_candidates(rows: list[dict], year: int) -> list[dict]:
    """Keep only the full canonical annual report released in the following spring."""
    result = []
    excluded = (
        "展示文件",
        "DISPLAYDOCUMENT",
        "企業年度報告書",
        "企业年度报告书",
        "SUPPLEMENTAL",
        "補充",
        "补充",
        "修訂",
        "修订",
        "REVISED",
        "SUMMARY",
        "摘要",
    )
    for row in rows:
        title = row["title_norm"]
        if not job.base.is_annual(row) or not job.base.has_year(row, year):
            continue
        if any(job.base.compact(marker) in title for marker in excluded):
            continue
        # A fiscal-year annual report should be issued in the next calendar year's spring.
        if row["release_dt"].year != year + 1 or not (3 <= row["release_dt"].month <= 6):
            continue
        # Require a canonical report-like title, not another filing that merely mentions the annual report.
        canonical_markers = (
            f"{year}年報",
            f"{year}年报",
            f"ANNUALREPORT{year}",
            f"{year}ANNUALREPORT",
        )
        if not any(job.base.compact(marker) in title for marker in canonical_markers):
            continue
        result.append(row)
    return result


def detect_report_year(row: dict) -> int:
    for year in range(2026, 2019, -1):
        if job.base.has_year(row, year):
            return year
    return row["release_dt"].year


def strict_select_documents(rows: list[dict]) -> list[dict]:
    selected: list[dict] = []

    for year in range(2020, 2026):
        candidates = canonical_annual_candidates(rows, year)
        print(
            "STRICT ANNUAL CANDIDATES",
            year,
            [(r["lang"], r["release_text"], r["title"], r["url"]) for r in candidates],
            flush=True,
        )
        if not candidates:
            raise RuntimeError(f"未找到招金矿业 {year} 年完整规范年度报告")
        row = job.base.choose(candidates)
        selected.append({
            "kind": "年度报告",
            "year": year,
            "filename": f"招金矿业_{year}年年度报告.pdf",
            "row": row,
            "minimum_pages": 60,
        })

    quarterly_candidates = [
        row for row in rows
        if job.is_quarterly_financial(row)
        and (row["release_dt"].year == 2026 or job.base.has_year(row, 2026))
    ]
    print(
        "STRICT QUARTERLY CANDIDATES",
        [(r["lang"], r["release_text"], r["title"], r["url"]) for r in quarterly_candidates],
        flush=True,
    )
    if quarterly_candidates:
        latest_dt = max(r["release_dt"] for r in quarterly_candidates)
        row = job.base.choose([r for r in quarterly_candidates if r["release_dt"] == latest_dt])
        kind = job.quarterly_kind(row)
        year = detect_report_year(row)
        selected.append({
            "kind": kind,
            "year": year,
            "filename": f"招金矿业_{year}年{kind}_最新季度财务披露.pdf",
            "row": row,
            "minimum_pages": 5,
        })

    formal_interims = sorted(
        [row for row in rows if job.base.is_formal_interim(row)],
        key=lambda r: (r["release_dt"], job.base.lang_priority(r)),
        reverse=True,
    )
    if formal_interims:
        latest_dt = formal_interims[0]["release_dt"]
        row = job.base.choose([r for r in formal_interims if r["release_dt"] == latest_dt])
        year = detect_report_year(row)
        selected.append({
            "kind": "中期报告",
            "year": year,
            "filename": f"招金矿业_{year}年中期报告_最新完整中期报告.pdf",
            "row": row,
            "minimum_pages": 25,
        })

    interim_results = sorted(
        [row for row in rows if job.base.is_interim_results(row)],
        key=lambda r: (r["release_dt"], job.base.lang_priority(r)),
        reverse=True,
    )
    if interim_results:
        latest_dt = interim_results[0]["release_dt"]
        row = job.base.choose([r for r in interim_results if r["release_dt"] == latest_dt])
        year = detect_report_year(row)
        formal_years = [item["year"] for item in selected if item["kind"] == "中期报告"]
        if not any(item["row"]["url"] == row["url"] for item in selected) and (
            not formal_years or year > max(formal_years)
        ):
            selected.append({
                "kind": "中期业绩公告",
                "year": year,
                "filename": f"招金矿业_{year}年中期业绩公告_最新财务披露.pdf",
                "row": row,
                "minimum_pages": 10,
            })

    deduplicated: list[dict] = []
    seen_urls: set[str] = set()
    for item in selected:
        url = item["row"]["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduplicated.append(item)

    for index, item in enumerate(deduplicated, 1):
        item["filename"] = f"{index:02d}_" + re.sub(r"^\d{2}_", "", item["filename"])

    print("STRICT FINAL SELECTED DOCUMENTS", flush=True)
    for item in deduplicated:
        row = item["row"]
        print(
            item["kind"], item["year"], row["lang"], row["release_text"], row["title"], row["url"],
            flush=True,
        )
    return deduplicated


job.select_documents = strict_select_documents


if __name__ == "__main__":
    job.main()
