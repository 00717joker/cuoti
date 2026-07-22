import sqlite3
import json

LOCAL_DB = r'd:\cuoti\wrong_questions.db'
BACKUP_FILE = r'd:\cuoti\initial_data.json'

conn = sqlite3.connect(LOCAL_DB)
conn.row_factory = sqlite3.Row

backup_data = {
    'version': 'v3',
    'backup_time': __import__('datetime').datetime.now().isoformat(),
    'questions': [],
    'practice_results': [],
    'settings': [],
    'daily_practice': []
}

cursor = conn.execute('SELECT * FROM wrong_questions')
backup_data['questions'] = [dict(row) for row in cursor]

cursor = conn.execute('SELECT * FROM practice_results')
backup_data['practice_results'] = [dict(row) for row in cursor]

cursor = conn.execute('SELECT * FROM settings')
backup_data['settings'] = [dict(row) for row in cursor]

cursor = conn.execute('SELECT * FROM daily_practice')
backup_data['daily_practice'] = [dict(row) for row in cursor]

with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
    json.dump(backup_data, f, ensure_ascii=False, indent=2)

print(f'备份完成！共 {len(backup_data["questions"])} 道题')
print(f'文件大小: {__import__("os").path.getsize(BACKUP_FILE) / 1024:.1f} KB')
