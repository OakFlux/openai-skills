import hashlib
import json
import re
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path

import requests
from pypdf import PdfReader

STOCK_CODE = "601000"
COMPANY = "唐山港"
FULL_COMPANY = "唐山港集团股份有限公司"
AS_OF = "20261016"

ROOT = Path(f"唐山港_{STOCK_CODE}_2020-2025年报及最新季报_截至{AS_OF}")
ANNUAL_DIR = ROOT / "01_年度报告"
QUARTER_DIR = ROOT / "02_最新季度报告"
PREVIEW_DIR = ROOT / "首页预览"
for directory in (ANNUAL_DIR, QUARTER_DIR, PREVIEW_DIR):
    directory.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36",
        "Referer": f"https://data.eastmoney.com/notices/stock/{STOCK_CODE}.html",
        "Accept": "application/json,text/plain,*/*",
    }
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_notice_date(row: dict) -> datetime:
    raw = row.get("notice_date") or row.get("display_time") or row.get("eiTime") or "1900-01-01"
    return datetime.fromisoformat(str(raw)[:10])


def column_names(row: dict) -> list[str]:
    return [str(x.get("column_name") or "") for x in (row.get("columns") or [])]


def fetch_announcements() -> list[dict]:
    endpoint = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    all_rows: list[dict] = []
    for page in range(1, 41):
        params = {
            "sr": "-1",
            "page_size": "100",
            "page_index": str(page),
            "ann_type": "A",
            "client_source": "web",
            "stock_list": STOCK_CODE,
            "f_node": "0",
            "s_node": "0",
        }
        response = session.get(endpoint, params=params, timeout=45)
        response.raise_for_status()
        rows = (response.json().get("data") or {}).get("list") or []
        print("ANN_PAGE", page, "ROWS", len(rows))
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 100:
            break
    if not all_rows:
        raise RuntimeError("No announcements returned for Tangshan Port")
    print("ANN_TOTAL", len(all_rows))
    return all_rows


def choose_annual(rows: list[dict], year: int) -> dict:
    token = f"{year}年年度报告"
    candidates: list[dict] = []
    for row in rows:
        title = str(row.get("title") or "").replace(" ", "")
        cols = column_names(row)
        if token not in title:
            continue
        if any(bad in title for bad in ("摘要", "英文", "社会责任", "ESG", "审计报告", "问询函", "审核意见")):
            continue
        if cols and not any("年度报告全文" in name for name in cols):
            continue
        candidates.append(row)
    if not candidates:
        raise RuntimeError(f"No full annual report found for {year}")

    def rank(row: dict):
        title = str(row.get("title") or "")
        revision = 1 if any(word in title for word in ("更新", "修订", "更正")) else 0
        return revision, parse_notice_date(row)

    selected = max(candidates, key=rank)
    print(
        "SELECTED_ANNUAL",
        year,
        json.dumps(
            {
                "art_code": selected.get("art_code"),
                "title": selected.get("title"),
                "notice_date": selected.get("notice_date"),
                "columns": column_names(selected),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return selected


def quarter_period(title: str):
    compact = title.replace(" ", "")
    if any(bad in compact for bad in ("摘要", "提示性", "披露", "业绩预告", "快报", "问询函")):
        return None
    match = re.search(r"(20\d{2})年(第一|一|第三|三)季度报告", compact)
    if not match:
        match = re.search(r"(20\d{2})年([一三])季报", compact)
    if not match:
        return None
    year = int(match.group(1))
    q_text = match.group(2)
    quarter = 1 if q_text in ("第一", "一") else 3
    return year, quarter


def choose_latest_quarter(rows: list[dict]) -> tuple[dict, tuple[int, int]]:
    candidates: list[tuple[tuple[int, int], dict]] = []
    for row in rows:
        title = str(row.get("title") or "")
        period = quarter_period(title)
        if not period:
            continue
        cols = column_names(row)
        if cols and not any(("一季度报告全文" in name or "三季度报告全文" in name) for name in cols):
            continue
        candidates.append((period, row))
    if not candidates:
        raise RuntimeError("No formal quarterly report found")
    period, selected = max(candidates, key=lambda item: (item[0][0], item[0][1], parse_notice_date(item[1])))
    print(
        "SELECTED_QUARTER",
        f"{period[0]}Q{period[1]}",
        json.dumps(
            {
                "art_code": selected.get("art_code"),
                "title": selected.get("title"),
                "notice_date": selected.get("notice_date"),
                "columns": column_names(selected),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return selected, period


def download_pdf(art_code: str, target: Path) -> tuple[str, list[dict]]:
    candidates = [
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{art_code}_1.pdf",
    ]
    traces: list[dict] = []
    headers = {
        "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
        "Referer": f"https://data.eastmoney.com/notices/stock/{STOCK_CODE}.html",
    }
    for url in candidates:
        for attempt in range(1, 4):
            try:
                response = session.get(url, headers=headers, timeout=90, allow_redirects=True)
                data = response.content
                traces.append(
                    {
                        "url": url,
                        "status": response.status_code,
                        "bytes": len(data),
                        "head": repr(data[:20]),
                    }
                )
                if response.status_code == 200 and data.startswith(b"%PDF") and len(data) > 50_000:
                    target.write_bytes(data)
                    return url, traces
            except Exception as exc:
                traces.append({"url": url, "error": repr(exc)})
            time.sleep(attempt)
    raise RuntimeError(f"Unable to download a valid PDF for {art_code}: {traces}")


def extract_first_pages_text(path: Path, pages: int) -> str:
    txt_path = path.with_suffix(".txt")
    try:
        subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(min(8, pages)), str(path), str(txt_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        text = txt_path.read_text(encoding="utf-8", errors="replace")
    finally:
        txt_path.unlink(missing_ok=True)
    if len(text.strip()) < 100:
        reader = PdfReader(str(path))
        text = "\n".join((reader.pages[i].extract_text() or "") for i in range(min(8, len(reader.pages))))
    return text


def validate_and_record(item: dict, target: Path) -> dict:
    source_url, traces = download_pdf(str(item["art_code"]), target)

    qcheck = subprocess.run(
        ["qpdf", "--check", str(target)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    if qcheck.returncode not in (0, 3):
        raise RuntimeError(f"qpdf structural error for {target}: {qcheck.returncode}\n{qcheck.stdout}")

    reader = PdfReader(str(target))
    pages = len(reader.pages)
    minimum_pages = 20 if item["kind"] == "annual" else 3
    if pages < minimum_pages:
        raise RuntimeError(f"Unexpectedly short report {target}: {pages} pages")

    text = extract_first_pages_text(target, pages)
    identity_hits = [x for x in (COMPANY, FULL_COMPANY, STOCK_CODE) if x in text]
    if not identity_hits:
        raise RuntimeError(f"Company identity not found in first pages of {target}")
    year = str(item["period"])[:4]
    if year not in text:
        raise RuntimeError(f"Report period {item['period']} not found in first pages of {target}")

    preview_stem = PREVIEW_DIR / target.stem
    subprocess.run(
        ["pdftoppm", "-f", "1", "-singlefile", "-png", "-r", "120", str(target), str(preview_stem)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    preview_png = preview_stem.with_suffix(".png")
    if not preview_png.exists() or preview_png.stat().st_size < 10_000:
        raise RuntimeError(f"First-page rendering failed for {target}")

    entry = {
        "kind": item["kind"],
        "period": item["period"],
        "title": item["title"],
        "notice_date": item["notice_date"],
        "art_code": item["art_code"],
        "filename": target.name,
        "source_page": f"https://data.eastmoney.com/notices/detail/{STOCK_CODE}/{item['art_code']}.html",
        "source_url": source_url,
        "pages": pages,
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
        "identity_hits": identity_hits,
        "qpdf_status": qcheck.returncode,
        "qpdf_output": qcheck.stdout.strip()[:2000],
        "download_attempts": traces,
    }
    print("VERIFIED", json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return entry


rows = fetch_announcements()
selected_docs: list[dict] = []
for year in range(2020, 2026):
    row = choose_annual(rows, year)
    selected_docs.append(
        {
            "kind": "annual",
            "period": str(year),
            "title": str(row.get("title") or ""),
            "notice_date": str(row.get("notice_date") or "")[:10],
            "art_code": str(row.get("art_code") or ""),
            "target": ANNUAL_DIR / f"{year}_唐山港_{year}年年度报告.pdf",
        }
    )

quarter_row, quarter = choose_latest_quarter(rows)
quarter_period_label = f"{quarter[0]}Q{quarter[1]}"
quarter_cn_label = f"{quarter[0]}年{'第一' if quarter[1] == 1 else '第三'}季度报告"
selected_docs.append(
    {
        "kind": "quarter",
        "period": quarter_period_label,
        "title": str(quarter_row.get("title") or ""),
        "notice_date": str(quarter_row.get("notice_date") or "")[:10],
        "art_code": str(quarter_row.get("art_code") or ""),
        "target": QUARTER_DIR / f"{quarter_period_label}_唐山港_{quarter_cn_label}.pdf",
    }
)

manifest: list[dict] = []
for item in selected_docs:
    manifest.append(validate_and_record(item, item["target"]))

annual_periods = [x["period"] for x in manifest if x["kind"] == "annual"]
if annual_periods != [str(y) for y in range(2020, 2026)]:
    raise RuntimeError(f"Annual report coverage mismatch: {annual_periods}")
if len([x for x in manifest if x["kind"] == "quarter"]) != 1:
    raise RuntimeError("Quarterly report count mismatch")

readme = [
    f"# 唐山港（{STOCK_CODE}）2020-2025年年度报告及最新季度报告",
    "",
    "整理日期：2026-10-16",
    "",
    "## 收录范围",
    "",
    "- 2020-2025年完整年度报告，共6份。",
    f"- 截至2026-10-16可取得的最新独立季度报告：{quarter_cn_label}，共1份。",
    "- 年度报告均选取完整报告，排除摘要、英文版、问询函和提示性公告。",
    "",
    "## 文件清单",
    "",
]
for index, entry in enumerate(manifest, 1):
    readme.extend(
        [
            f"{index}. {entry['title']}",
            f"   - 文件：{entry['filename']}",
            f"   - 公告日期：{entry['notice_date']}",
            f"   - 页数：{entry['pages']}",
            f"   - SHA-256：`{entry['sha256']}`",
            f"   - 公告页：{entry['source_page']}",
            "",
        ]
    )
readme.extend(
    [
        "## 校验说明",
        "",
        "- 每份PDF均检查文件头、PDF结构、实际页数、公司名称、证券代码、报告期及首页渲染。",
        "- 压缩包创建后执行CRC完整性测试。",
    ]
)
(ROOT / "README.md").write_text("\n".join(readme), encoding="utf-8")
(ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
(ROOT / "SHA256SUMS.txt").write_text(
    "\n".join(f"{entry['sha256']}  {entry['filename']}" for entry in manifest) + "\n",
    encoding="utf-8",
)

cn_zip = Path(f"唐山港_{STOCK_CODE}_2020-2025年报及最新季报_截至{AS_OF}.zip")
en_zip = Path(f"tangshan_port_{STOCK_CODE}_2020_2025_annual_latest_quarter_{AS_OF}.zip")
for zip_path in (cn_zip, en_zip):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(ROOT.rglob("*")):
            if path.is_file():
                archive.write(path, path.as_posix())
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC failure: {bad}")
        pdf_names = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
        if len(pdf_names) != 7:
            raise RuntimeError(f"Unexpected PDF count in ZIP: {len(pdf_names)}")
    print("FINAL_ZIP", zip_path, zip_path.stat().st_size, "SHA256", sha256(zip_path))

print(
    "COUNTS",
    json.dumps(
        {
            "annual": 6,
            "quarter": 1,
            "pdfs": 7,
            "pages": sum(entry["pages"] for entry in manifest),
            "latest_quarter": quarter_period_label,
        },
        ensure_ascii=False,
        sort_keys=True,
    ),
)
