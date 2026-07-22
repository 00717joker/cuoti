import json
import requests
import sqlite3

conn = sqlite3.connect(r'd:\cuoti\wrong_questions.db')
conn.row_factory = sqlite3.Row
cursor = conn.execute('SELECT * FROM wrong_questions')
questions = [dict(row) for row in cursor]
conn.close()

print(f'Total questions in local DB: {len(questions)}')

api_url = 'https://cuoti-production.up.railway.app/api/questions'

added = 0
updated = 0
skipped = 0

for q in questions:
    payload = {
        'question_number': q.get('question_number', ''),
        'chapter': q.get('chapter', ''),
        'section': q.get('section', ''),
        'source': q.get('source', ''),
        'error_type': q.get('error_type', ''),
        'difficulty': q.get('difficulty', ''),
        'question_type': q.get('question_type', ''),
        'knowledge_tags': q.get('knowledge_tags', ''),
        'note': q.get('note', ''),
        'wrong_count': q.get('wrong_count', 0),
        'consecutive_correct': q.get('consecutive_correct', 0),
        'mastered': q.get('mastered', 0),
        'date_added': q.get('date_added'),
        'last_wrong_date': q.get('last_wrong_date'),
        'last_practice_date': q.get('last_practice_date'),
        'image_data': q.get('image_data'),
    }
    
    response = requests.post(api_url, json=payload)
    
    if response.status_code == 201:
        added += 1
    elif response.status_code == 200:
        result = response.json()
        if result.get('added', 0) > 0:
            added += 1
        elif 'details' in result:
            for detail in result['details']:
                if '错误次数+1' in detail.get('reason', ''):
                    updated += 1
                else:
                    skipped += 1

print(f'Added: {added}, Updated: {updated}, Skipped: {skipped}')

final_count = requests.get(api_url)
print(f'Final total questions online: {len(final_count.json())}')
