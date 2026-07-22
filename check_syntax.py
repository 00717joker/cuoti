import re
with open(r'e:\新建文件夹\错题管理_离线版.html','r',encoding='utf-8') as f:
    html = f.read()

m = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
js = m.group(1)
opens = js.count('{')
closes = js.count('}')

print(f'File size: {len(html)}')
print(f'Braces: {opens} vs {closes} -> {"OK" if opens==closes else "MISMATCH"}')
print(f'bulkPut present: {"bulkPut" in js}')
print(f'safeGetPut present: {"safeGetPut" in js}')
