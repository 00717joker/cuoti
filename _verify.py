import re, sys

for path in [r'e:\新建文件夹\错题管理_离线版.html', r'e:\新建文件夹\templates\index.html']:
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
    js = m.group(1)
    opens = js.count('{')
    closes = js.count('}')
    name = path.split('\\')[-1]
    ok = opens == closes
    print(f'{name}: {len(html)} chars, braces {opens}/{closes} -> {"OK" if ok else "FAIL"}')
    if not ok:
        print(f'  MISMATCH! diff={opens-closes}')
        sys.exit(1)
print('All OK')
