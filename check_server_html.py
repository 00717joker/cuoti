import requests

response = requests.get('https://cuoti-production.up.railway.app/')
html = response.text

if 'subjectTabs' in html:
    print('FOUND subjectTabs in server HTML')
else:
    print('NOT FOUND subjectTabs in server HTML')
    
if 'subject-btn' in html:
    print('FOUND subject-btn in server HTML')
else:
    print('NOT FOUND subject-btn in server HTML')

print('\n--- Checking for subject-tabs in HTML ---')
start = html.find('subject-tabs')
if start > 0:
    print(f'Found at position {start}')
    print(html[start-50:start+100])
else:
    print('Not found')
