import json
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})

urls = [
    "https://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml?symbol=sz300905&t1=all",
    "https://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml?symbol=300905&t1=all",
    "https://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml?symbol=sz300905",
]
all_links = []
for url in urls:
    response = session.get(url, timeout=90, allow_redirects=True)
    response.encoding = response.apparent_encoding or "gb18030"
    print("PAGE", url, response.status_code, len(response.content), response.url, response.encoding)
    soup = BeautifulSoup(response.text, "html.parser")
    links = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = urljoin(response.url, anchor["href"])
        if "vReport_Show" in href or "rptid" in href or "宝丽迪" in text or "300905" in text:
            record = {"source": url, "text": text, "href": href}
            links.append(record)
            all_links.append(record)
    print("LINKS", json.dumps(links, ensure_ascii=False, indent=2))

dedup = []
seen = set()
for record in all_links:
    key = (record["text"], record["href"])
    if key not in seen:
        seen.add(key)
        dedup.append(record)

with open("output/baolidi_sina_report_links.json", "w", encoding="utf-8") as handle:
    json.dump(dedup, handle, ensure_ascii=False, indent=2)
print("FINAL_LINK_COUNT", len(dedup))
