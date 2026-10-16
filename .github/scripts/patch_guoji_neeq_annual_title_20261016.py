from pathlib import Path

path = Path('.github/scripts/guoji_zhongzhuang_package_20261016.py')
text = path.read_text(encoding='utf-8')
old = '''            pattern = rf"{year}年(?:度)?报告(?:（(?:修订版|更正版|更正后|修订稿)）|\\((?:修订版|更正版|更正后|修订稿)\\)|(?:修订版|更正版|更正后|修订稿))?"
'''
new = '''            pattern = rf"{year}年(?:年度|度)?报告(?:（(?:修订版|更正版|更正后|修订稿)）|\\((?:修订版|更正版|更正后|修订稿)\\)|(?:修订版|更正版|更正后|修订稿))?"
'''
if old not in text:
    raise SystemExit('NEEQ annual title pattern not found')
path.write_text(text.replace(old, new), encoding='utf-8')
print('Patched NEEQ annual report title matching.')
