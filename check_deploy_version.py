import requests

# 检查服务器返回的HTML是否包含特定内容
response = requests.get('https://cuoti-production.up.railway.app/')
html = response.text

# 检查版本信息
print("=== 检查服务器返回的HTML内容 ===")
print(f"HTML长度: {len(html)}")
print()

# 检查是否有科目切换相关内容
check_items = ['subjectTabs', 'subject-btn', '高数', '线代', '概率', 'switchSubject']
for item in check_items:
    found = item in html
    print(f"{item}: {'✓ FOUND' if found else '✗ NOT FOUND'}")

print()
print("=== 检查服务器API版本 ===")
api_response = requests.get('https://cuoti-production.up.railway.app/api/stats/overview')
print(f"API状态码: {api_response.status_code}")
print(f"API数据: {api_response.json()}")
