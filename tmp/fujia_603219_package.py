from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OUT = Path("富佳股份_603219_年报_招股说明书_最新季报")
OUT.mkdir(parents=True, exist_ok=True)
RENDER_DIR = Path("/tmp/fujia_render")
RENDER_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
RETRY = Retry(
    total=8,
    connect=8,
    read=8,
    status=8,
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)
SESSION.mount("https://", HTTPAdapter(max_retries=RETRY))
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    }
)

DOCS = [
    {
        "file": "00_富佳股份_首次公开发行股票招股说明书_2021-11-10.pdf",
        "name": "首次公开发行股票招股说明书",
        "date": "2021-11-10",
        "kind": "prospectus",
        "min_pages": 300,
        "detail": "https://vip.stock.finance.sina.com.cn/corp/view/vISSUE_RaiseExplanationDetail.php?id=7648532&stockid=603219",
        "fallbacks": [
            "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESH_STOCK/2021/2021-11/2021-11-10/7648532.PDF",
            "https://qccdata.qichacha.com/ReportData/PDF/098138e1bcfd59d7a216da853f935fbc.pdf",
        ],
    },
    {
        "file": "01_富佳股份_2021年年度报告.pdf",
        "name": "2021年年度报告",
        "year": 2021,
        "date": "2022-04-21",
        "kind": "annual",
        "min_pages": 70,
        "detail": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=8022948&stockid=603219",
        "fallbacks": [
            "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESH_STOCK/2022/2022-4/2022-04-21/8022948.PDF"
        ],
    },
    {
        "file": "02_富佳股份_2022年年度报告.pdf",
        "name": "2022年年度报告",
        "year": 2022,
        "date": "2023-03-22",
        "kind": "annual",
        "min_pages": 70,
        "detail": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=8901389&stockid=603219",
        "fallbacks": [
            "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESH_STOCK/2023/2023-3/2023-03-22/8901389.PDF"
        ],
    },
    {
        "file": "03_富佳股份_2023年年度报告.pdf",
        "name": "2023年年度报告",
        "year": 2023,
        "date": "2024-04-27",
        "kind": "annual",
        "min_pages": 70,
        "detail": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=10127859&stockid=603219",
        "fallbacks": [
            "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESH_STOCK/2024/2024-4/2024-04-27/10127859.PDF"
        ],
    },
    {
        "file": "04_富佳股份_2024年年度报告.pdf",
        "name": "2024年年度报告",
        "year": 2024,
        "date": "2025-04-26",
        "kind": "annual",
        "min_pages": 70,
        "detail": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=10992633&stockid=603219",
        "fallbacks": [
            "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESH_STOCK/2025/2025-4/2025-04-26/10992633.PDF"
        ],
    },
    {
        "file": "05_富佳股份_2025年年度报告.pdf",
        "name": "2025年年度报告",
        "year": 2025,
        "date": "2026-03-21",
        "kind": "annual",
        "min_pages": 70,
        "detail": "https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12008471&stockid=603219",
        "fallbacks": [
            "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESH_STOCK/2026/2026-3/2026-03-21/12008471.PDF"
        ],
    },
    {
        "file": "06_富佳股份_2026年第三季度报告.pdf",
        "name": "2026年第三季度报告",
        "year": 2026,
        "date": "2026-10-28",
        "kind": "quarter",
        "min_pages": 8,
        "detail": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12664265&stockid=603219",
        "fallbacks": [
            "https://file.finance.sina.com.cn/211.154.219.97%3A9494/MRGG/CNSESH_STOCK/2026/2026-10/2026-10-28/12664265.PDF"
        ],
    },
]


def clean_title(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    return re.sub(r"\s+", "", html.unescape(value))


def query_sse() -> list[dict]:
    endpoint = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
    headers = {
        "Referer": "https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=603219",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    records: list[dict] = []
    try:
        for page_no in range(1, 8):
            params = {
                "isPagination": "true",
                "productId": "603219",
                "keyWord": "",
                "securityType": "0101,120100,020100,020200,120200",
                "reportType2": "",
                "reportType": "ALL",
                "beginDate": "2020-01-01",
                "endDate": "2026-10-28",
                "pageHelp.pageSize": "100",
                "pageHelp.pageNo": str(page_no),
                "pageHelp.beginPage": str(page_no),
                "pageHelp.endPage": str(page_no + 4),
                "_": str(int(time.time() * 1000)),
            }
            response = SESSION.get(endpoint, headers=headers, params=params, timeout=(30, 120))
            response.raise_for_status()
            payload = response.json()
            page_help = payload.get("pageHelp") or {}
            data = page_help.get("data") or payload.get("result") or payload.get("data") or []
            if not isinstance(data, list) or not data:
                break
            records.extend(data)
            if len(data) < 100:
                break
        print(f"SSE records: {len(records)}")
    except Exception as exc:
        print(f"SSE query unavailable: {exc!r}")
        return []
    return records


SSE_RECORDS = query_sse()


def record_title(record: dict) -> str:
    for key in ("TITLE", "title", "BULLETIN_TITLE", "bulletinTitle"):
        if record.get(key):
            return clean_title(str(record[key]))
    return ""


def record_url(record: dict) -> str | None:
    for key in ("URL", "url", "BULLETIN_URL", "bulletinUrl", "docURL"):
        if record.get(key):
            return urljoin("https://www.sse.com.cn", str(record[key]))
    return None


def official_candidate(doc: dict) -> str | None:
    for record in SSE_RECORDS:
        title = record_title(record)
        url = record_url(record)
        if not title or not url:
            continue
        if doc["kind"] == "annual":
            matched = f"{doc['year']}年年度报告" in title and "摘要" not in title and "持续督导" not in title
        elif doc["kind"] == "quarter":
            matched = "2026年第三季度报告" in title and "摘要" not in title
        else:
            matched = "首次公开发行股票招股说明书" in title and not any(
                term in title for term in ("摘要", "申报稿", "意向书", "附录")
            )
        if matched:
            print("SSE match", doc["name"], title, url)
            return url
    return None


def page_candidates(detail_url: str) -> list[str]:
    try:
        response = SESSION.get(detail_url, timeout=(30, 120))
        if response.status_code != 200:
            return []
        text = html.unescape(response.text)
        pattern = re.compile(r"https?://[^\"'<>\s]+?\.pdf(?:\?[^\"'<>\s]*)?", re.I)
        return pattern.findall(text)
    except Exception as exc:
        print("Detail page failed", detail_url, repr(exc))
        return []


def validate_pdf(path: Path, min_pages: int) -> int:
    data = path.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise RuntimeError("missing PDF header")
    if len(data) < 80_000:
        raise RuntimeError(f"file too small: {len(data)}")
    pages = len(PdfReader(str(path), strict=False).pages)
    if pages < min_pages:
        raise RuntimeError(f"page count {pages} below minimum {min_pages}")
    completed = subprocess.run(["qpdf", "--check", str(path)], capture_output=True, text=True)
    if completed.returncode not in (0, 3):
        raise RuntimeError(f"qpdf failed: {completed.stdout}\n{completed.stderr}")
    first_stem = RENDER_DIR / f"{path.stem}_first"
    last_stem = RENDER_DIR / f"{path.stem}_last"
    subprocess.run(
        ["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-png", "-r", "72", str(path), str(first_stem)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["pdftoppm", "-f", str(pages), "-l", str(pages), "-singlefile", "-png", "-r", "72", str(path), str(last_stem)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not first_stem.with_suffix(".png").exists() or not last_stem.with_suffix(".png").exists():
        raise RuntimeError("render check failed")
    return pages


def download(doc: dict) -> dict:
    candidates: list[str] = []
    official = official_candidate(doc)
    if official:
        candidates.append(official)
    candidates.extend(page_candidates(doc["detail"]))
    candidates.extend(doc["fallbacks"])

    unique: list[str] = []
    seen = set()
    for url in candidates:
        if url.startswith("http://"):
            url = "https://" + url[len("http://") :]
        if url not in seen:
            seen.add(url)
            unique.append(url)

    destination = OUT / doc["file"]
    last_error: Exception | None = None
    for url in unique:
        for attempt in range(1, 5):
            try:
                print("TRY", doc["name"], url, "attempt", attempt)
                response = SESSION.get(
                    url,
                    headers={
                        "Referer": doc["detail"],
                        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
                    },
                    timeout=(30, 360),
                    allow_redirects=True,
                )
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                destination.write_bytes(response.content)
                pages = validate_pdf(destination, int(doc["min_pages"]))
                sha = hashlib.sha256(response.content).hexdigest()
                print("DOWNLOADED", destination.name, "pages", pages, "bytes", len(response.content), "source", response.url)
                return {
                    "file": doc["file"],
                    "document": doc["name"],
                    "published": doc["date"],
                    "pages": pages,
                    "bytes": len(response.content),
                    "sha256": sha,
                    "source": response.url,
                    "announcement_page": doc["detail"],
                }
            except Exception as exc:
                last_error = exc
                destination.unlink(missing_ok=True)
                print("FAILED", doc["name"], repr(exc))
                time.sleep(attempt * 2)
    raise RuntimeError(f"Could not download {doc['name']}: {last_error!r}")


def main() -> None:
    results = [download(doc) for doc in DOCS]
    if len(results) != 7:
        raise RuntimeError(f"unexpected result count: {len(results)}")
    if [row["document"] for row in results[1:6]] != [f"{year}年年度报告" for year in range(2021, 2026)]:
        raise RuntimeError("annual report sequence mismatch")
    if results[0]["document"] != "首次公开发行股票招股说明书" or results[0]["pages"] < 300:
        raise RuntimeError("prospectus validation failed")
    if results[6]["document"] != "2026年第三季度报告" or results[6]["published"] != "2026-10-28":
        raise RuntimeError("latest quarterly report validation failed")

    lines = [
        "富佳股份（603219）年报、招股说明书及最新季报资料包",
        "整理日期：2026-10-28",
        "",
        "收录范围：",
        "1. 上市以来全部年度报告全文：2021—2025年，共5份。",
        "2. 首次公开发行股票招股说明书全文：2021年11月10日披露，共1份。",
        "3. 截至2026年10月28日最新季度报告：2026年第三季度报告，共1份。",
        "4. 未收录年度报告摘要、持续督导报告、招股说明书摘要或半年度报告。",
        "",
        "文件清单：",
    ]
    checksums: list[str] = []
    total_pages = 0
    total_bytes = 0
    for item in results:
        total_pages += int(item["pages"])
        total_bytes += int(item["bytes"])
        lines.extend(
            [
                f"- {item['file']}",
                f"  文件：{item['document']}；披露日期：{item['published']}；页数：{item['pages']}；大小：{item['bytes']}字节",
                f"  PDF来源：{item['source']}",
                f"  公告信息页：{item['announcement_page']}",
                f"  SHA-256：{item['sha256']}",
            ]
        )
        checksums.append(f"{item['sha256']}  {item['file']}")
    lines.extend(
        [
            "",
            f"PDF数量：{len(results)}份。",
            f"合计页数：{total_pages}页。",
            f"PDF合计大小：{total_bytes}字节。",
            "全部PDF已完成文件头、文件大小、页数、qpdf结构和首末页渲染检查。",
            "资料仅用于研究与存档。",
        ]
    )
    (OUT / "00_文件清单与来源说明.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    test_zip = Path("/tmp/fujia_package_test.zip")
    with zipfile.ZipFile(test_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(OUT.iterdir()):
            archive.write(path, arcname=f"{OUT.name}/{path.name}")
    with zipfile.ZipFile(test_zip) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"zip test failed at {bad}")
        pdf_count = sum(1 for name in archive.namelist() if name.lower().endswith(".pdf"))
        if pdf_count != 7:
            raise RuntimeError(f"zip PDF count mismatch: {pdf_count}")
    test_zip.unlink()
    print("\n".join(lines))


if __name__ == "__main__":
    main()
