from pathlib import Path

source_path = Path("scripts/build_miaokelando_filings_20261012.py")
source = source_path.read_text(encoding="utf-8")

old_function = '''def is_full_annual_report(title: str, year: int) -> bool:
    if f"{year}年年度报告" not in title:
        return False
    blocked = (
        "摘要",
        "更正公告",
        "修订公告",
        "取消",
        "业绩说明会",
        "预约",
        "英文版",
        "审计报告",
        "董事会决议",
        "监事会决议",
    )
    return not any(term in title for term in blocked)
'''

new_function = '''def is_full_annual_report(row: dict[str, Any], year: int) -> bool:
    title = title_of(row)
    columns = column_names(row)
    if f"{year}年年度报告" not in title:
        return False
    blocked = (
        "摘要",
        "更正公告",
        "修订公告",
        "回复公告",
        "回复函",
        "监管工作函",
        "工作函",
        "问询函",
        "问询回复",
        "取消",
        "业绩说明会",
        "预约",
        "英文版",
        "审计报告",
        "董事会决议",
        "监事会决议",
    )
    if any(term in title for term in blocked):
        return False
    return "年度报告全文" in columns or columns.strip() == "年度报告"
'''

old_call = 'candidates = [row for row in all_rows if is_full_annual_report(title_of(row), year)]'
new_call = 'candidates = [row for row in all_rows if is_full_annual_report(row, year)]'

if old_function not in source:
    raise RuntimeError("Expected annual-report filter function was not found")
if old_call not in source:
    raise RuntimeError("Expected annual-report candidate expression was not found")

source = source.replace(old_function, new_function, 1)
source = source.replace(old_call, new_call, 1)

exec(compile(source, str(source_path), "exec"), {"__name__": "__main__"})
