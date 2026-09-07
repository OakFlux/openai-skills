#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_nissin_foods_financial_reports_20260907 as base

COMPANY_CN = "招金礦業股份有限公司"
COMPANY_CN_SIMPLIFIED = "招金矿业股份有限公司"
COMPANY_EN = "Zhaojin Mining Industry Company Limited"
STOCK_CODE = "01818"
AS_OF_DATE = "2026-09-07"

ROOT = Path.cwd()
WORK = ROOT / "_work_zhaojin_mining_20260907"
PACKAGE = WORK / "招金矿业_2020-2025年报及最新季度中期财务披露_截至2026-09-07"
PDF_DIR = PACKAGE / "PDF"
RENDER_DIR = WORK / "renders"
DIST = ROOT / "dist_zhaojin_mining_20260907"
FINAL_ZIP = DIST / "Zhaojin_Mining_1818_2020-2025_Annual_and_Latest_Reports.zip"

# Reuse the tested HKEX querying/downloading functions while replacing issuer-specific globals.
base.COMPANY_CN = COMPANY_CN
base.COMPANY_EN = COMPANY_EN
base.STOCK_CODE = STOCK_CODE
base.AS_OF_DATE = AS_OF_DATE
base.WORK = WORK
base.PACKAGE = PACKAGE
base.PDF_DIR = PDF_DIR
base.RENDER_DIR = RENDER_DIR
base.DIST = DIST
base.FINAL_ZIP = FINAL_ZIP


def detect_report_year(row: dict) -> int:
    for year in range(2026, 2019, -1):
        if base.has_year(row, year):
            return year
    return row["release_dt"].year


def is_quarterly_financial(row: dict) -> bool:
    title = row["title_norm"]
    period_markers = (
        "QUARTERLYREPORT",
        "QUARTERLYRESULTS",
        "FIRSTQUARTER",
        "1STQUARTER",
        "THIRDQUARTER",
        "3RDQUARTER",
        "THREEMONTHSENDED",
        "NINEMONTHSENDED",
        "第一季度",
        "第三季度",
        "首三個月",
        "首三个月",
        "截至2026年3月31日止三個月",
        "截至2026年3月31日止三个月",
        "截至2026年9月30日止九個月",
        "截至2026年9月30日止九个月",
    )
    financial_markers = (
        "FINANCIALINFORMATION",
        "FINANCIALRESULTS",
        "UNAUDITEDRESULTS",
        "QUARTERLYREPORT",
        "QUARTERLYRESULTS",
        "財務資料",
        "财务资料",
        "財務信息",
        "财务信息",
        "業績",
        "业绩",
        "季度報告",
        "季度报告",
        "季報",
        "季报",
    )
    excluded = (
        "MONTHLYRETURN",
        "月報表",
        "月报表",
        "PRODUCTIONGUIDANCE",
        "PRODUCTIONUPDATE",
        "產量指引",
        "产量指引",
        "營運數據",
        "运营数据",
    )
    parent_only = (
        any(marker in title for marker in ("CONTROLLINGSHAREHOLDER", "控股股東", "控股股东"))
        and not any(marker in title for marker in ("OFTHECOMPANY", "本公司", "ZHAOJINMINING", "招金礦業", "招金矿业"))
    )
    return (
        any(marker in title for marker in period_markers)
        and any(marker in title for marker in financial_markers)
        and not any(marker in title for marker in excluded)
        and not parent_only
    )


def quarterly_kind(row: dict) -> str:
    title = row["title_norm"]
    if any(marker in title for marker in (
        "THIRDQUARTER", "3RDQUARTER", "NINEMONTHSENDED", "第三季度", "九個月", "九个月", "9月30日"
    )):
        return "第三季度报告"
    return "第一季度报告"


def select_documents(rows: list[dict]) -> list[dict]:
    selected: list[dict] = []

    for year in range(2020, 2026):
        candidates = [row for row in rows if base.is_annual(row) and base.has_year(row, year)]
        if not candidates:
            candidates = [
                row for row in rows
                if base.is_annual(row)
                and row["release_dt"].year == year + 1
                and 3 <= row["release_dt"].month <= 6
            ]
        print(
            "ANNUAL CANDIDATES",
            year,
            [(r["lang"], r["release_text"], r["title"], r["url"]) for r in candidates],
            flush=True,
        )
        if not candidates:
            raise RuntimeError(f"未找到招金矿业 {year} 年完整年度报告")
        row = base.choose(candidates)
        selected.append({
            "kind": "年度报告",
            "year": year,
            "filename": f"招金矿业_{year}年年度报告.pdf",
            "row": row,
            "minimum_pages": 60,
        })

    # Include a genuine 2026 quarterly financial disclosure when one exists.
    quarterly_candidates = [
        row for row in rows
        if is_quarterly_financial(row)
        and (row["release_dt"].year == 2026 or base.has_year(row, 2026))
    ]
    print(
        "QUARTERLY CANDIDATES",
        [(r["lang"], r["release_text"], r["title"], r["url"]) for r in quarterly_candidates],
        flush=True,
    )
    if quarterly_candidates:
        latest_dt = max(r["release_dt"] for r in quarterly_candidates)
        row = base.choose([r for r in quarterly_candidates if r["release_dt"] == latest_dt])
        kind = quarterly_kind(row)
        year = detect_report_year(row)
        selected.append({
            "kind": kind,
            "year": year,
            "filename": f"招金矿业_{year}年{kind}_最新季度财务披露.pdf",
            "row": row,
            "minimum_pages": 5,
        })

    # Keep the latest full interim report. It may lag the latest results announcement.
    formal_interims = sorted(
        [row for row in rows if base.is_formal_interim(row)],
        key=lambda r: (r["release_dt"], base.lang_priority(r)),
        reverse=True,
    )
    if formal_interims:
        latest_dt = formal_interims[0]["release_dt"]
        row = base.choose([r for r in formal_interims if r["release_dt"] == latest_dt])
        year = detect_report_year(row)
        selected.append({
            "kind": "中期报告",
            "year": year,
            "filename": f"招金矿业_{year}年中期报告_最新完整中期报告.pdf",
            "row": row,
            "minimum_pages": 25,
        })

    # Add a newer interim-results announcement when the latest full interim report has not yet been issued.
    interim_results = sorted(
        [row for row in rows if base.is_interim_results(row)],
        key=lambda r: (r["release_dt"], base.lang_priority(r)),
        reverse=True,
    )
    if interim_results:
        latest_dt = interim_results[0]["release_dt"]
        row = base.choose([r for r in interim_results if r["release_dt"] == latest_dt])
        year = detect_report_year(row)
        formal_years = [item["year"] for item in selected if item["kind"] == "中期报告"]
        duplicate_url = any(item["row"]["url"] == row["url"] for item in selected)
        if not duplicate_url and (not formal_years or year > max(formal_years)):
            selected.append({
                "kind": "中期业绩公告",
                "year": year,
                "filename": f"招金矿业_{year}年中期业绩公告_最新财务披露.pdf",
                "row": row,
                "minimum_pages": 10,
            })

    # Remove accidental duplicate documents, then assign stable sequence numbers.
    deduplicated: list[dict] = []
    seen_urls: set[str] = set()
    for item in selected:
        url = item["row"]["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduplicated.append(item)
    selected = deduplicated

    for index, item in enumerate(selected, 1):
        item["filename"] = f"{index:02d}_" + re.sub(r"^\d{2}_", "", item["filename"])

    print("FINAL SELECTED DOCUMENTS", flush=True)
    for item in selected:
        row = item["row"]
        print(
            item["kind"], item["year"], row["lang"], row["release_text"], row["title"], row["url"],
            flush=True,
        )
    return selected


def validate_pdf(path: Path, item: dict) -> dict:
    # The base validator performs PDF header, encryption, page-count, qpdf and render checks.
    base_item = dict(item)
    if "季度" in item["kind"]:
        base_item["kind"] = "中期业绩公告"
    metadata = base.validate_pdf(path, base_item)

    if "季度" not in item["kind"]:
        return metadata

    reader = PdfReader(str(path), strict=False)
    text = base.compact(base.extract_probe(reader, len(reader.pages)))
    title = item["row"]["title_norm"]
    quarter_markers = (
        "QUARTERLYREPORT", "QUARTERLYRESULTS", "FIRSTQUARTER", "THIRDQUARTER",
        "THREEMONTHSENDED", "NINEMONTHSENDED", "第一季度", "第三季度", "三個月", "三个月", "九個月", "九个月",
    )
    quarter_verified = any(marker in text for marker in quarter_markers) or any(marker in title for marker in quarter_markers)
    if not quarter_verified:
        raise RuntimeError(f"{path.name} 未识别到季度财务披露标记")
    metadata["type_text_verified"] = True
    return metadata


def make_package(records: list[dict]) -> None:
    PACKAGE.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    csv_path = PACKAGE / "来源与校验清单.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "序号", "文件名", "报告年度", "报告类型", "港交所原公告标题", "披露时间", "语言",
            "页数", "文件大小_字节", "SHA256", "PDF结构校验", "首尾页渲染校验",
            "公司名称文本校验", "年度文本校验", "类型文本校验", "港交所PDF地址",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({
                "序号": record["sequence"],
                "文件名": record["filename"],
                "报告年度": record["year"],
                "报告类型": record["kind"],
                "港交所原公告标题": record["official_title"],
                "披露时间": record["release_time"],
                "语言": record["language"],
                "页数": record["pages"],
                "文件大小_字节": record["bytes"],
                "SHA256": record["sha256"],
                "PDF结构校验": record["qpdf_ok"],
                "首尾页渲染校验": record["first_last_rendered"],
                "公司名称文本校验": record["company_text_verified"],
                "年度文本校验": record["year_text_verified"],
                "类型文本校验": record["type_text_verified"],
                "港交所PDF地址": record["resolved_url"],
            })

    quarterly = [r for r in records if "季度" in r["kind"]]
    formal_interims = [r for r in records if r["kind"] == "中期报告"]
    latest_results = [r for r in records if r["kind"] == "中期业绩公告"]

    lines = [
        f"{COMPANY_CN_SIMPLIFIED}（{COMPANY_EN}）",
        "香港交易所股份代号：01818 / 1818",
        f"整理日期：{AS_OF_DATE}",
        "",
        "一、收录口径",
        "1. 收录2020、2021、2022、2023、2024、2025年度报告全文，共6份。",
    ]
    if quarterly:
        q = quarterly[-1]
        lines.append(f"2. 收录公司最新可识别的独立季度财务披露：{q['year']}年{q['kind']}。")
    else:
        lines.append("2. 截至整理日，未检索到公司按A股口径发布的独立第一季度或第三季度完整报告。")
    if formal_interims:
        f = formal_interims[-1]
        lines.append(f"3. 同时收录最新完整中期报告：{f['year']}年中期报告。")
    if latest_results:
        r = latest_results[-1]
        lines.append(f"4. 由于更晚年度的完整中期报告尚未发布，补充收录{r['year']}年中期业绩公告，覆盖截至整理日最新财务披露。")

    lines += ["", "二、文件清单"]
    for record in records:
        lines.append(
            f"{record['sequence']}. {record['filename']}｜{record['language']}｜{record['pages']}页｜SHA-256：{record['sha256']}"
        )
    lines += [
        "",
        "三、校验说明",
        "全部PDF来自香港交易所披露易，逐份检查PDF文件头、加密状态、实际页数及qpdf结构。",
        "每份文件均渲染首尾页确认可正常显示，并在可提取文本及港交所元数据中核对公司、年度与报告类型。",
        "详细来源、披露日期、页数和哈希值见《来源与校验清单.csv》及《manifest.json》。",
    ]
    (PACKAGE / "README_文件说明.txt").write_text("\n".join(lines), encoding="utf-8")
    (PACKAGE / "manifest.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (PACKAGE / "SHA256SUMS.txt").write_text(
        "".join(f"{record['sha256']}  PDF/{record['filename']}\n" for record in records),
        encoding="utf-8",
    )

    with zipfile.ZipFile(FINAL_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(WORK)))

    with zipfile.ZipFile(FINAL_ZIP, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError("ZIP CRC 校验失败：" + bad)
        entries = archive.namelist()

    package_hash = base.sha256(FINAL_ZIP)
    (DIST / "PACKAGE_SHA256.txt").write_text(
        f"{package_hash}  {FINAL_ZIP.name}\n",
        encoding="utf-8",
    )
    print(
        "FINAL ZIP",
        json.dumps({
            "path": str(FINAL_ZIP),
            "bytes": FINAL_ZIP.stat().st_size,
            "sha256": package_hash,
            "entries": len(entries),
            "pdf_count": len([name for name in entries if name.lower().endswith(".pdf")]),
        }, ensure_ascii=False),
        flush=True,
    )


def main() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    if DIST.exists():
        shutil.rmtree(DIST)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    session = base.build_session()
    stock_id = base.find_stock_id(session)
    print("USING STOCK ID", stock_id, flush=True)
    rows = base.gather_records(session, stock_id)
    selected = select_documents(rows)

    records: list[dict] = []
    for sequence, item in enumerate(selected, 1):
        data, resolved_url = base.fetch_pdf(session, item["row"]["url"])
        destination = PDF_DIR / item["filename"]
        destination.write_bytes(data)
        metadata = validate_pdf(destination, item)
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

    annual_years = [r["year"] for r in records if r["kind"] == "年度报告"]
    if annual_years != list(range(2020, 2026)):
        raise RuntimeError(f"年报年份不完整：{annual_years}")
    if len(records) < 7 or len(records) > 9:
        raise RuntimeError(f"文件数量异常：预期7至9份，实际{len(records)}份")
    if len({r["sha256"] for r in records}) != len(records):
        raise RuntimeError("检测到重复PDF")
    if not any(r["kind"] in ("中期报告", "中期业绩公告") or "季度" in r["kind"] for r in records):
        raise RuntimeError("缺少最新阶段性财务披露")

    make_package(records)


if __name__ == "__main__":
    main()
