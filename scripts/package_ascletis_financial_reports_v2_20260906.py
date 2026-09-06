#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import package_ascletis_financial_reports_20260906 as base


def is_annual(row: dict) -> bool:
    t = row["title_norm"]
    positive = any(x in t for x in (
        "ANNUALREPORT", "ANNUALFINANCIALREPORT", "年報", "年报", "年度報告", "年度报告"
    ))
    # HKEX category strings may themselves contain “ESG Information”, so ESG/Sustainability
    # cannot be used as blanket exclusions. True ESG reports do not normally contain the
    # exact annual-report markers above.
    excluded = any(x in t for x in (
        "INTERIM", "中期", "RESULTS", "業績", "业绩", "SUMMARY", "摘要",
        "NOTICE", "CIRCULAR", "PROXY", "通告", "通知", "股東週年", "股东周年",
    ))
    return positive and not excluded


base.is_annual = is_annual
base.main()
