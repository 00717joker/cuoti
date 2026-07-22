"""批量导入错题数据"""
import sqlite3, os, json

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wrong_questions.db')

# 解析用户提供的错题数据
raw = """
U1 单元
讲义例题
1.3、1.6、1.12、1.25、1.26、1.28、1.37
课后例题
1.3、1.4、1.5、1.12、1.13、1.14
练习题
9、11、13、14、15、16、17、18、19、20、21、22、23

U2 单元
讲义例题
2.1、2.4、2.5、2.6、2.7、2.10、2.11、2.12、2.14、2.15、2.18
课后例题
2.2、2.3、2.4、2.7、2.8、2.8
练习题
1、2、3、4、6、7、9

U3 单元
讲义例题
3.2、3.3、3.5、3.6、3.8、3.9、3.10、3.11
课后例题
3.5、3.6、3.7、3.8、3.9
练习题
1、2、3、5、6、8、9、10、12、13、15

U4 单元
讲义例题
4.2、4.4、4.6、4.9、4.10、4.14、4.16、4.18、4.19
课后例题
4.2、4.4、4.5、4.7、4.8
练习题
3、4、5、8、9、11、12、16、17、18

U5 单元
讲义例题
5.1、5.2、5.3、5.4、5.5、5.6、5.9、5.11、5.12、5.13、5.14
课后例题
5.1、5.2、5.4、5.7、5.9
练习题
3、5、8、11、12、14、16、22、23、24

U6 单元
讲义例题
6.10、6.20
课后例题
6.4、6.6、6.7、6.8、6.10
练习题
5、6、7、9、10、11、12、13

U7 单元
讲义例题
7.1
课后例题
7.1、7.3
练习题
5

U8 单元
讲义例题
8.3、8.4、8.7、8.8、8.9、8.10、8.11、8.12、8.13、8.17、8.18、8.19
课后例题
8.3、8.5、8.7
练习题
1、4、5、13、15、21

U9 单元（高频重点单元）
讲义例题
9.5、9.6、9.12、9.14、9.15、9.19、9.21、9.23、9.25、9.26、9.27、9.28
课后例题
9.1、9.2、9.5、9.8、9.9、9.10、9.11、9.12、9.13、9.14、9.15、9.17、9.19、9.21
练习题
2、3、4、6、7、8、12、13、15、20、25、26、29、32、33、34、35

U10 单元
讲义例题
10.2、10.5
课后例题
无
练习题
5、7、8、13、15
"""

def parse_data(text):
    items = []
    current_chapter = ''
    current_section = ''

    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        # 匹配章节
        if '单元' in line:
            current_chapter = line.split('（')[0].strip()
            continue

        # 匹配小节
        if line in ('讲义例题', '课后例题', '练习题'):
            current_section = line
            continue

        # 无 → 跳过
        if line == '无':
            continue

        # 解析题号列表（用 、 或 , 分隔）
        nums = line.replace(',', '、').replace('，', '、').split('、')
        for num in nums:
            num = num.strip()
            if num and num != '无':
                items.append({
                    'question_number': num,
                    'chapter': current_chapter,
                    'section': current_section,
                    'source': current_chapter
                })

    return items


def main():
    items = parse_data(raw)
    print(f'共解析出 {len(items)} 道错题')

    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA journal_mode=WAL")
    db = conn.cursor()

    added, skipped = 0, 0
    for item in items:
        qn = item['question_number']
        ch = item['chapter']
        sec = item['section']
        src = item['source']

        # 检查是否存在
        existing = db.execute(
            "SELECT id, wrong_count FROM wrong_questions WHERE question_number=? AND chapter=? AND section=?",
            (qn, ch, sec)
        ).fetchone()

        if existing:
            db.execute(
                "UPDATE wrong_questions SET wrong_count=wrong_count+1, last_wrong_date=date('now','localtime') WHERE id=?",
                (existing[0],)
            )
            skipped += 1
        else:
            db.execute(
                "INSERT INTO wrong_questions (question_number, chapter, section, source) VALUES (?,?,?,?)",
                (qn, ch, sec, src)
            )
            added += 1

    conn.commit()
    conn.close()

    print(f'新增 {added} 题，跳过 {skipped} 题（已存在）')


if __name__ == '__main__':
    main()
