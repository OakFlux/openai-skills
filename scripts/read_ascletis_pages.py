import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

pages = [
    'https://www.fxbaogao.com/detail/4757488',
    'https://www.fxbaogao.com/detail/5048656',
    'https://www.fxbaogao.com/detail/5205547',
    'https://www.baogaobox.com/reports/250402000054301.html',
    'https://www.baogaobox.com/reports/251229000088797.html',
    'https://research.poems.com.hk/page/Phillip/kc/researchnews/e_index.asp?alink=dailynews%2F20240124%2Fdaily_e.htm&pagenum=2',
]

for index, url in enumerate(pages):
    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=60)
    response.encoding = response.apparent_encoding or 'utf-8'
    open(f'page_{index}.html', 'w', encoding='utf-8').write(response.text)
    print('PAGE', index, response.status_code, response.url, len(response.content))
    soup = BeautifulSoup(response.text, 'html.parser')
    for tag in soup.find_all(['a', 'img', 'iframe']):
        value = tag.get('href') or tag.get('src')
        if value:
            absolute = urljoin(response.url, value)
            if 'pdf' in absolute.lower() or 'download' in absolute.lower():
                print('LINK', absolute)
