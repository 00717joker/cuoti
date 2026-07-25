import sqlite3

conn = sqlite3.connect('wrong_questions.db')
try:
    conn.execute('ALTER TABLE wrong_questions ADD COLUMN subject TEXT DEFAULT "math"')
    conn.commit()
    print('Successfully added subject column')
except sqlite3.OperationalError as e:
    print(f'Error: {e}')
finally:
    conn.close()
