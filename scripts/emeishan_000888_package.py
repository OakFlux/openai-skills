from __future__ import annotations

import csv, hashlib, json, re, subprocess, time, zipfile
from pathlib import Path
import requests
from pypdf import PdfReader

COMPANY = "峨眉山A"
FULL_NAME = "峨眉山旅游股份有限公司"
CODE = "000888"
PREPARED = "2026-11-04"
ROOT_NAME = "峨眉山A_000888_2020-2025年报及最新季报"
ROOT = Path(ROOT_NAME)
PDF_DIR = ROOT / "PDF文件"
PREVIEW_DIR = ROOT / "首页预览"
PDF_DIR.mkdir(parents=True, exist_ok=True)
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

REPORTS = [
    (1, "2020年年度报告", "annual", "2021-04-16", "https://static.cninfo.com.cn/finalpage/2021-04-16/1209700660.PDF", 100, ["2020年年度报告"]),
    (2, "2021年年度报告", "annual", "2022-04-28", "https://static.cninfo.com.cn/finalpage/2022-04-28/1213167703.PDF", 100, ["2021年年度报告"]),
    (3, "2022年年度报告", "annual", "2023-04-28", "https://static.cninfo.com.cn/finalpage/2023-04-28/1216654154.PDF", 100, ["2022年年度报告"]),
    (4, "2023年年度报告", "annual", "2024-03-30", "https://static.cninfo.com.cn/finalpage/2024-03-30/1219466541.PDF", 100, ["2023年年度报告"]),
    (5, "2024年年度报告", "annual", "2025-04-25", "https://static.cninfo.com.cn/finalpage/2025-04-25/1223279751.PDF", 100, ["2024年年度报告"]),
    (6, "2025年年度报告", "annual", "2026-04-28", "https://static.cninfo.com.cn/finalpage/2026-04-28/1225205820.PDF", 100, ["2025年年度报告"]),
    (7, "2026年第三季度报告", "quarter", "2026-10-30", "https://static.cninfo.com.cn/finalpage/2026-10-30/1225640356.PDF", 5, ["2026年第三季度报告", "2026年三季度报告"]),
]

session = requests.Session()
session.trust_env = False
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.cninfo.com.cn/",
})


def download(url: str) -> tuple[bytes, str, str]:
    last = None
    for attempt in range(6):
        try:
            r = session.get(url, timeout=(30, 360), allow_redirects=True, headers={"Accept": "application/pdf,*/*;q=0.8", "Referer": "https://www.cninfo.com.cn/"})
            print("DOWNLOAD", r.status_code, len(r.content), r.url, r.headers.get("content-type"))
            r.raise_for_status()
            if r.content.startswith(b"%PDF-") and len(r.content) > 50_000:
                return r.content, r.url, r.headers.get("content-type", "")
            last = RuntimeError(f"not PDF: {r.content[:40]!r}")
        except Exception as exc:
            last = exc
            print("DOWNLOAD_ERROR", attempt, url, repr(exc))
        time.sleep(attempt + 1)
    raise RuntimeError(f"Download failed for {url}: {last}")

accepted_names = [CODE, FULL_NAME, "峨眉山A", "峨眉山Ａ", "峨眉旅游"]
manifest = []

for seq, period, kind, date, url, min_pages, period_tokens in REPORTS:
    data, final_url, ctype = download(url)
    filename = f"{seq:02d}_{period}.pdf"
    path = PDF_DIR / filename
    path.write_bytes(data)
    reader = PdfReader(str(path), strict=False)
    pages = len(reader.pages)
    if pages < min_pages:
        raise RuntimeError(f"too short: {filename}: {pages}")
    idx = list(range(min(12, pages)))
    if pages > 12:
        idx += list(range(max(12, pages - 2), pages))
    compact = re.sub(r"\s+", "", "\n".join((reader.pages[i].extract_text() or "") for i in sorted(set(idx))))
    if not any(name.replace(" ", "") in compact for name in accepted_names):
        raise RuntimeError(f"issuer validation failed: {filename}")
    if not any(token.replace(" ", "") in compact for token in period_tokens):
        raise RuntimeError(f"period validation failed: {filename}")
    chk = subprocess.run(["qpdf", "--warning-exit-0", "--check", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if chk.returncode != 0:
        raise RuntimeError(f"qpdf failed: {filename}: {chk.stdout}")
    prefix = PREVIEW_DIR / path.stem
    rnd = subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-png", "-r", "120", "-singlefile", str(path), str(prefix)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    preview = Path(str(prefix) + ".png")
    if rnd.returncode != 0 or not preview.exists() or preview.stat().st_size < 5_000:
        raise RuntimeError(f"preview failed: {filename}: {rnd.stdout}")
    item = {
        "sequence": seq, "period": period, "kind": kind, "announcement_date": date,
        "filename": filename, "pages": pages, "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "source_url": final_url,
        "content_type": ctype, "preview_file": str(Path("首页预览") / preview.name),
    }
    manifest.append(item)
    print("VALIDATED", period, pages, len(data), item["sha256"])

if len(manifest) != 7:
    raise RuntimeError(f"expected 7 reports, got {len(manifest)}")

lines = [
    f"{FULL_NAME}（{CODE}.SZ）2020—2025年年度报告及最新季度报告资料包",
    f"整理日期：{PREPARED}", "", "文件构成：",
    "- 2020—2025年年度报告全文，共6份。",
    "- 截至整理日已披露的最新独立季度报告：2026年第三季度报告，共1份。", "",
    "已排除年度报告摘要、半年度报告、审计附件、提示性公告及其他非正文文件。", "",
]
for x in manifest:
    lines += [
        f"{x['sequence']}. {x['filename']}", f"   报告期间：{x['period']}",
        f"   公告日期：{x['announcement_date']}", f"   PDF页数：{x['pages']}；文件大小：{x['bytes']}字节",
        f"   官方来源：{x['source_url']}", f"   SHA-256：{x['sha256']}", "",
    ]
lines += [
    "核验项目：PDF文件头、公司名称或证券代码、报告期间、实际页数、qpdf结构、首页渲染、逐文件SHA-256及ZIP CRC。",
    "文件仅供研究、教学与信息存档使用。",
]
(ROOT / "README_报告清单与核验说明.txt").write_text("\n".join(lines), encoding="utf-8")

payload = {"company": FULL_NAME, "stock_code": CODE, "prepared_on": PREPARED, "file_count": 7, "annual_report_count": 6, "latest_quarter": manifest[-1]["period"], "files": manifest}
(ROOT / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
with (ROOT / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["序号", "报告期间", "类型", "公告日期", "文件名", "页数", "字节数", "SHA-256", "官方来源", "首页预览"])
    for x in manifest:
        w.writerow([x["sequence"], x["period"], x["kind"], x["announcement_date"], x["filename"], x["pages"], x["bytes"], x["sha256"], x["source_url"], x["preview_file"]])
(ROOT / "SHA256SUMS.txt").write_text("\n".join(f"{x['sha256']}  PDF文件/{x['filename']}" for x in manifest) + "\n", encoding="utf-8")

zip_path = Path("emeishan_000888_2020_2025_annual_latest_quarter.zip")
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in sorted(ROOT.rglob("*")):
        if p.is_file():
            z.write(p, arcname=str(Path(ROOT_NAME) / p.relative_to(ROOT)))
with zipfile.ZipFile(zip_path) as z:
    bad = z.testzip()
    pdfs = [n for n in z.namelist() if n.lower().endswith(".pdf")]
    if bad or len(pdfs) != 7:
        raise RuntimeError(f"ZIP validation failed bad={bad} pdfs={len(pdfs)}")

result = {
    "package": zip_path.name, "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "file_count": 7, "annual_report_count": 6, "latest_quarter": manifest[-1]["period"],
    "files": [{k: x[k] for k in ["period", "announcement_date", "pages", "bytes", "sha256", "source_url"]} for x in manifest],
}
Path("result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print("PACKAGE_RESULT", json.dumps(result, ensure_ascii=False, indent=2))
