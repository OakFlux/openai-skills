#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import package_ascletis_financial_reports_20260906 as base

_original_query_rows = base.query_rows


def query_rows(stock_id: str, start: str, end: str, lang: str):
    rows = _original_query_rows(stock_id, start, end, lang)
    for row in rows:
        # Classification must use only the actual announcement headline. HKEX LONG_TEXT
        # contains category labels such as both Annual Report and ESG Information,
        # which can otherwise cause an ESG report to be misidentified as an annual report.
        row["title_norm"] = base.compact(row.get("title", ""))
    return rows


def contains_year(row: dict, year: int) -> bool:
    title = base.normalize(row.get("title", ""))
    title_compact = base.compact(title)
    return any(marker in title or base.compact(marker) in title_compact for marker in base.YEAR_CN[year])


base.query_rows = query_rows
base.contains_year = contains_year
base.main()
