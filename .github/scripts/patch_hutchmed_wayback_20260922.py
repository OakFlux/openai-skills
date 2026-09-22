from pathlib import Path

path = Path('.github/scripts/hutchmed_package_20260922.py')
text = path.read_text(encoding='utf-8')

start_marker = '# 2006-2007 statutory annual reports were published as official chapter PDFs on the legacy company site.\n'
end_marker = '# 2008-2011: the current official legacy archive retains annual/full-year results presentation files.\n'
if start_marker not in text or end_marker not in text:
    raise SystemExit('Legacy annual-report block markers not found')
start = text.index(start_marker)
end = text.index(end_marker)
replacement = '''# 2006-2011: the surviving official legacy archive provides annual-report / full-year-results presentation PDFs.\n# These files are retained under their true document type; they are not labelled as complete statutory annual reports.\n'''
text = text[:start] + replacement + text[end:]

old_dictionary = '''legacy_presentations = {
    2008: "pre0903.pdf",
    2009: "pre1003.pdf",
    2010: "pre1103.pdf",
    2011: "pre1203.pdf",
}
'''
new_dictionary = '''legacy_presentations = {
    2006: "pre0703.pdf",
    2007: "pre0803.pdf",
    2008: "pre0903.pdf",
    2009: "pre1003.pdf",
    2010: "pre1103.pdf",
    2011: "pre1203.pdf",
}
'''
if old_dictionary not in text:
    raise SystemExit('Legacy presentation dictionary marker not found')
text = text.replace(old_dictionary, new_dictionary, 1)

old_note = 'The current official archive retains this annual/full-year results presentation; the complete statutory annual-report PDF for this year was not available from the current official archive.'
new_note = 'Official company annual-report/full-year-results presentation retained in the surviving legacy archive. A complete statutory annual-report PDF for this year was not available from the current official archive; this file is therefore labelled by its actual document type.'
if old_note not in text:
    raise SystemExit('Legacy presentation note marker not found')
text = text.replace(old_note, new_note, 1)

path.write_text(text, encoding='utf-8')
print('Patched package build to use surviving official 2006-2011 annual-result files')
