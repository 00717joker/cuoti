"""
错题管理智能体 v2 - Flask 后端
功能：
  1. 错题 CRUD + 搜索
  2. 练习结果追踪 + 智能掌握判定（连续 3 次正确 → 掌握）
  3. 权重抽题算法（错误次数 + 新鲜度 + 陈旧度）
  4. 可配置每日题数
  5. CSV 导出 + 统计
"""
import os
import json
import sqlite3
import random
import csv
import io
from datetime import date, datetime, timedelta
from collections import defaultdict
from flask import Flask, request, jsonify, g, render_template, Response

app = Flask(__name__)
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wrong_questions.db')

MASTER_THRESHOLD = 1  # 做对一次即掌握
DAILY_COUNT_DEFAULT = 15

# 错因类型
ERROR_TYPES = {
    'careless':     '粗心失误',
    'concept':      '概念不清',
    'calculation':  '计算错误',
    'method':       '方法不会'
}


# ══════════════════════════════════════════════════════════
# 数据库
# ══════════════════════════════════════════════════════════

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS wrong_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_number TEXT NOT NULL,
                chapter TEXT NOT NULL DEFAULT '',
                section TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                wrong_count INTEGER NOT NULL DEFAULT 1,
                consecutive_correct INTEGER NOT NULL DEFAULT 0,
                mastered INTEGER NOT NULL DEFAULT 0,
                date_added TEXT NOT NULL DEFAULT (date('now','localtime')),
                last_wrong_date TEXT NOT NULL DEFAULT (date('now','localtime')),
                last_practice_date TEXT,
                UNIQUE(question_number, chapter, section)
            );

            CREATE TABLE IF NOT EXISTS daily_practice (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                questions TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS practice_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                result TEXT NOT NULL,
                error_type TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (question_id) REFERENCES wrong_questions(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS study_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                question_count INTEGER NOT NULL DEFAULT 0
            );
        """)

        # ── 兼容旧数据库：补齐缺失的列 ──
        existing_cols = {r[1] for r in db.execute("PRAGMA table_info(wrong_questions)").fetchall()}
        migrations = [
            ('consecutive_correct', 'INTEGER NOT NULL DEFAULT 0'),
            ('mastered',            'INTEGER NOT NULL DEFAULT 0'),
            ('last_practice_date',  'TEXT'),
            ('error_type',          'TEXT NOT NULL DEFAULT ""'),
            ('knowledge_tags',      'TEXT NOT NULL DEFAULT ""'),
            ('difficulty',          'TEXT NOT NULL DEFAULT ""'),
            ('question_type',       'TEXT NOT NULL DEFAULT ""'),
            ('image_data',          'TEXT'),
        ]
        for col, col_def in migrations:
            if col not in existing_cols:
                db.execute(f"ALTER TABLE wrong_questions ADD COLUMN {col} {col_def}")

        # practice_results 表
        pr_cols = {r[1] for r in db.execute("PRAGMA table_info(practice_results)").fetchall()}
        if 'error_type' not in pr_cols:
            db.execute("ALTER TABLE practice_results ADD COLUMN error_type TEXT NOT NULL DEFAULT ''")

        # 确保默认设置存在
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_count', '15')")
        db.commit()


# ── 应用启动时初始化数据库 ──
with app.app_context():
    init_db()

# ── CORS 支持（允许离线版 file:// 访问） ──
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response


def get_setting(key, default=None):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row['value'] if row else default


# ══════════════════════════════════════════════════════════
# 路由
# ══════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')


# ─── 错题 CRUD ────────────────────────────────────────────

@app.route('/api/questions', methods=['GET'])
def get_questions():
    chapter = request.args.get('chapter', '')
    search = request.args.get('search', '').strip()
    db = get_db()

    sql = "SELECT * FROM wrong_questions WHERE 1=1"
    params = []

    if chapter:
        sql += " AND chapter=?"
        params.append(chapter)
    if search:
        sql += " AND (question_number LIKE ? OR chapter LIKE ? OR section LIKE ? OR source LIKE ?)"
        like = f'%{search}%'
        params.extend([like, like, like, like])

    sql += " ORDER BY chapter, section, question_number"
    rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/questions', methods=['POST'])
def add_question():
    data = request.get_json()
    items = data if isinstance(data, list) else [data]

    db = get_db()
    added, skipped = [], []
    for item in items:
        qn = str(item.get('question_number', '')).strip()
        ch = item.get('chapter', '').strip()
        sec = item.get('section', '').strip()
        src = item.get('source', '').strip()
        note = item.get('note', '').strip()
        etype = item.get('error_type', '').strip()
        tags = item.get('knowledge_tags', '').strip()
        difficulty = item.get('difficulty', '').strip()
        qtype = item.get('question_type', '').strip()
        img = item.get('image_data')

        if not qn or not ch:
            skipped.append({'question_number': qn, 'reason': '题号和章节不能为空'})
            continue

        existing = db.execute(
            "SELECT id, wrong_count FROM wrong_questions WHERE question_number=? AND chapter=? AND section=?",
            (qn, ch, sec)
        ).fetchone()

        if existing:
            if img:
                db.execute(
                    "UPDATE wrong_questions SET wrong_count=wrong_count+1, consecutive_correct=0, mastered=0, last_wrong_date=date('now','localtime'), note=?, error_type=?, knowledge_tags=?, difficulty=?, question_type=?, image_data=? WHERE id=?",
                    (note, etype, tags, difficulty, qtype, img, existing['id'])
                )
            else:
                db.execute(
                    "UPDATE wrong_questions SET wrong_count=wrong_count+1, consecutive_correct=0, mastered=0, last_wrong_date=date('now','localtime'), note=?, error_type=?, knowledge_tags=?, difficulty=?, question_type=? WHERE id=?",
                    (note, etype, tags, difficulty, qtype, existing['id'])
                )
            skipped.append({'question_number': qn, 'reason': f'已存在，错误次数+1'})
        else:
            db.execute(
                "INSERT INTO wrong_questions (question_number, chapter, section, source, note, error_type, knowledge_tags, difficulty, question_type, image_data) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (qn, ch, sec, src, note, etype, tags, difficulty, qtype, img)
            )
            added.append(item)
    db.commit()
    return jsonify({'added': len(added), 'skipped': len(skipped), 'details': skipped})


@app.route('/api/questions/<int:qid>', methods=['DELETE'])
def delete_question(qid):
    db = get_db()
    db.execute("DELETE FROM wrong_questions WHERE id=?", (qid,))
    db.execute("DELETE FROM practice_results WHERE question_id=?", (qid,))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/questions/batch-delete', methods=['POST'])
def batch_delete():
    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'ok': False, 'error': '未提供要删除的ID'}), 400
    db = get_db()
    for i in ids:
        db.execute("DELETE FROM practice_results WHERE question_id=?", (i,))
    db.executemany("DELETE FROM wrong_questions WHERE id=?", [(i,) for i in ids])
    db.commit()
    return jsonify({'ok': True, 'deleted': len(ids)})


# ─── 搜索 ─────────────────────────────────────────────────

@app.route('/api/search', methods=['GET'])
def search_questions():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    db = get_db()
    like = f'%{q}%'
    rows = db.execute(
        "SELECT * FROM wrong_questions WHERE question_number LIKE ? OR chapter LIKE ? OR section LIKE ? ORDER BY chapter, section, question_number LIMIT 50",
        (like, like, like)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ─── 章节统计 ─────────────────────────────────────────────

@app.route('/api/chapters', methods=['GET'])
def get_chapters():
    db = get_db()
    rows = db.execute(
        "SELECT chapter, COUNT(*) as count FROM wrong_questions GROUP BY chapter ORDER BY chapter"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ─── 设置 ─────────────────────────────────────────────────

@app.route('/api/settings', methods=['GET'])
def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r['key']: r['value'] for r in rows})


@app.route('/api/settings', methods=['POST'])
def update_settings():
    data = request.get_json()
    db = get_db()
    for key, value in data.items():
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    db.commit()
    return jsonify({'ok': True})


# ─── 练习结果记录 ─────────────────────────────────────────

@app.route('/api/practice-result', methods=['POST'])
def record_practice_result():
    """记录单题练习结果 {question_id, result: 'correct'|'wrong', error_type?}"""
    data = request.get_json()
    qid = data.get('question_id')
    result = data.get('result')
    error_type = data.get('error_type', '')
    today = date.today().isoformat()

    if not qid or result not in ('correct', 'wrong'):
        return jsonify({'ok': False, 'error': '参数错误'}), 400

    db = get_db()

    # 插入结果记录
    db.execute(
        "INSERT INTO practice_results (question_id, date, result, error_type) VALUES (?,?,?,?)",
        (qid, today, result, error_type)
    )

    if result == 'correct':
        db.execute(
            "UPDATE wrong_questions SET consecutive_correct=consecutive_correct+1, last_practice_date=? WHERE id=?",
            (today, qid)
        )
        # 检查是否达到掌握阈值
        q = db.execute("SELECT consecutive_correct FROM wrong_questions WHERE id=?", (qid,)).fetchone()
        if q and q['consecutive_correct'] >= MASTER_THRESHOLD:
            db.execute("UPDATE wrong_questions SET mastered=1 WHERE id=?", (qid,))
    else:
        db.execute(
            "UPDATE wrong_questions SET consecutive_correct=0, mastered=0, wrong_count=wrong_count+1, last_wrong_date=?, last_practice_date=?, error_type=? WHERE id=?",
            (today, today, error_type, qid)
        )

    db.commit()
    return jsonify({'ok': True})


@app.route('/api/practice-results', methods=['GET'])
def get_today_results():
    """获取今日练习结果统计"""
    today = date.today().isoformat()
    db = get_db()
    row = db.execute(
        "SELECT result, COUNT(*) as count FROM practice_results WHERE date=? GROUP BY result",
        (today,)
    ).fetchall()
    stats = {'correct': 0, 'wrong': 0, 'total': 0}
    for r in row:
        stats[r['result']] = r['count']
    stats['total'] = stats['correct'] + stats['wrong']
    return jsonify(stats)


# ─── 统计 ─────────────────────────────────────────────────

@app.route('/api/stats/weekly', methods=['GET'])
def weekly_stats():
    """最近 7 天每日正确率"""
    db = get_db()
    rows = db.execute("""
        SELECT date,
               SUM(CASE WHEN result='correct' THEN 1 ELSE 0 END) as correct,
               SUM(CASE WHEN result='wrong' THEN 1 ELSE 0 END) as wrong
        FROM practice_results
        WHERE date >= date('now','localtime','-6 days')
        GROUP BY date ORDER BY date
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/stats/overview', methods=['GET'])
def overview_stats():
    """总览统计"""
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM wrong_questions").fetchone()['c']
    mastered = db.execute("SELECT COUNT(*) as c FROM wrong_questions WHERE mastered=1").fetchone()['c']
    total_practice = db.execute("SELECT COUNT(*) as c FROM practice_results").fetchone()['c']
    today = date.today().isoformat()
    today_practice = db.execute(
        "SELECT COUNT(DISTINCT question_id) as c FROM practice_results WHERE date=?", (today,)
    ).fetchone()['c']
    return jsonify({
        'total': total,
        'mastered': mastered,
        'remaining': total - mastered,
        'total_practice': total_practice,
        'today_practice': today_practice
    })


@app.route('/api/stats/error-types', methods=['GET'])
def error_type_stats():
    """错因类型分布统计"""
    db = get_db()
    # 从 practice_results 统计各错因出现次数
    rows = db.execute("""
        SELECT error_type, COUNT(*) as count
        FROM practice_results
        WHERE result='wrong' AND error_type != ''
        GROUP BY error_type
        ORDER BY count DESC
    """).fetchall()
    result = []
    for r in rows:
        result.append({
            'type': r['error_type'],
            'label': ERROR_TYPES.get(r['error_type'], r['error_type']),
            'count': r['count']
        })
    total_wrong = db.execute("SELECT COUNT(*) as c FROM practice_results WHERE result='wrong'").fetchone()['c']
    unlabeled = total_wrong - sum(r['count'] for r in result)
    if unlabeled > 0:
        result.append({'type': 'unknown', 'label': '未分类', 'count': unlabeled})
    return jsonify({'distribution': result, 'total_wrong': total_wrong})


@app.route('/api/stats/heatmap', methods=['GET'])
def chapter_heatmap():
    """章节弱项热力图数据：每个章节的题目数、掌握率、错因分布"""
    db = get_db()
    rows = db.execute("""
        SELECT chapter,
               COUNT(*) as total,
               SUM(CASE WHEN mastered=1 THEN 1 ELSE 0 END) as mastered,
               AVG(wrong_count * 1.0) as avg_wrong,
               SUM(CASE WHEN error_type='careless' THEN 1 ELSE 0 END) as careless,
               SUM(CASE WHEN error_type='concept' THEN 1 ELSE 0 END) as concept,
               SUM(CASE WHEN error_type='calculation' THEN 1 ELSE 0 END) as calc,
               SUM(CASE WHEN error_type='method' THEN 1 ELSE 0 END) as method
        FROM wrong_questions
        GROUP BY chapter ORDER BY chapter
    """).fetchall()

    chapters = []
    max_total = max((r['total'] for r in rows), default=1)
    for r in rows:
        mastery = round(r['mastered'] / r['total'] * 100) if r['total'] > 0 else 0
        weakness = 100 - mastery
        chapters.append({
            'chapter': r['chapter'],
            'total': r['total'],
            'mastered': r['mastered'],
            'mastery_pct': mastery,
            'weakness_pct': weakness,
            'avg_wrong': round(r['avg_wrong'], 1),
            'error_types': {
                'careless': r['careless'],
                'concept': r['concept'],
                'calculation': r['calc'],
                'method': r['method']
            }
        })

    return jsonify({'chapters': chapters, 'error_type_labels': ERROR_TYPES})


# ─── 重置掌握状态 ─────────────────────────────────────────

@app.route('/api/reset-mastered', methods=['POST'])
def reset_mastered():
    """将所有题目的掌握状态重置，清除今日缓存，方便第二轮刷题"""
    db = get_db()
    db.execute("UPDATE wrong_questions SET mastered=0, consecutive_correct=0")
    # 清除今日练习缓存，让下次练习重新抽题
    db.execute("DELETE FROM daily_practice WHERE date=?", (date.today().isoformat(),))
    db.commit()
    return jsonify({'ok': True})


# ─── 每日练习 ─────────────────────────────────────────────

@app.route('/api/daily-practice', methods=['GET'])
def get_daily_practice():
    """
    获取练习题目：
    - 无参数：获取今日题目（无缓存则生成）
    - ?date=YYYY-MM-DD：获取指定日期的题目缓存
    """
    date_param = request.args.get('date', '').strip()
    today = date.today().isoformat()
    target_date = date_param if date_param else today
    db = get_db()
    daily_count = int(get_setting('daily_count', DAILY_COUNT_DEFAULT))

    # 查目标日期是否有缓存
    row = db.execute("SELECT * FROM daily_practice WHERE date=?", (target_date,)).fetchone()
    if row:
        question_ids = json.loads(row['questions'])
        if question_ids:
            placeholders = ','.join('?' for _ in question_ids)
            questions = db.execute(
                f"SELECT * FROM wrong_questions WHERE id IN ({placeholders})",
                question_ids
            ).fetchall()
        else:
            questions = []
        # 附带当日结果
        results = db.execute(
            "SELECT question_id, result FROM practice_results WHERE date=?", (target_date,)
        ).fetchall()
        result_map = {r['question_id']: r['result'] for r in results}
        qlist = [dict(q) for q in questions]
        for q in qlist:
            q['today_result'] = result_map.get(q['id'], None)
        return jsonify({'date': target_date, 'cached': True, 'questions': qlist, 'daily_count': daily_count})

    # 如果是过去日期且无缓存，返回空
    if date_param:
        return jsonify({'date': target_date, 'cached': False, 'questions': [], 'daily_count': daily_count, 'message': '该日期无练习记录'})

    # 生成新题目（仅当天）
    all_q = db.execute("SELECT * FROM wrong_questions WHERE mastered=0").fetchall()
    if not all_q:
        return jsonify({'date': today, 'questions': [], 'daily_count': daily_count, 'message': '所有错题已掌握或无错题'})

    qdicts = [dict(q) for q in all_q]
    selected = weighted_sample(qdicts, daily_count, db)

    selected_ids = [q['id'] for q in selected]
    db.execute(
        "INSERT OR REPLACE INTO daily_practice (date, questions) VALUES (?,?)",
        (today, json.dumps(selected_ids))
    )
    db.commit()

    return jsonify({'date': today, 'cached': False, 'questions': selected, 'daily_count': daily_count})


@app.route('/api/daily-practice/refresh', methods=['POST'])
def refresh_daily_practice():
    today = date.today().isoformat()
    db = get_db()
    daily_count = int(get_setting('daily_count', DAILY_COUNT_DEFAULT))

    all_q = db.execute("SELECT * FROM wrong_questions WHERE mastered=0").fetchall()
    if not all_q:
        return jsonify({'ok': False, 'error': '没有可练习的错题'}), 400

    qdicts = [dict(q) for q in all_q]
    selected = weighted_sample(qdicts, daily_count, db)
    selected_ids = [q['id'] for q in selected]
    db.execute(
        "INSERT OR REPLACE INTO daily_practice (date, questions) VALUES (?,?)",
        (today, json.dumps(selected_ids))
    )
    db.commit()
    return jsonify({'date': today, 'questions': selected})


@app.route('/api/daily-practice/dates', methods=['GET'])
def get_practice_dates():
    """获取有练习缓存的日期列表（最近30天）"""
    db = get_db()
    rows = db.execute("SELECT date FROM daily_practice ORDER BY date DESC LIMIT 30").fetchall()
    return jsonify([r['date'] for r in rows])


# ─── 导出 ─────────────────────────────────────────────────

@app.route('/api/export', methods=['GET'])
def export_csv():
    db = get_db()
    rows = db.execute("""
        SELECT question_number, chapter, section, source,
               wrong_count, consecutive_correct,
               CASE WHEN mastered=1 THEN 'yes' ELSE '-' END as mastered,
               error_type, knowledge_tags, difficulty, question_type,
               date_added, last_wrong_date, last_practice_date
        FROM wrong_questions ORDER BY chapter, section, question_number
    """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['题号', '章节', '小节', '来源', '错误次数', '连续正确', '已掌握', '错因', '知识点标签', '难度', '题型', '添加日期', '最后错误日', '最后练习日'])
    for r in rows:
        writer.writerow([r['question_number'], r['chapter'], r['section'], r['source'],
                         r['wrong_count'], r['consecutive_correct'], r['mastered'],
                         ERROR_TYPES.get(r['error_type'], r['error_type']),
                         r['knowledge_tags'], r['difficulty'], r['question_type'],
                         r['date_added'], r['last_wrong_date'], r['last_practice_date'] or ''])

    output.seek(0)
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=wrong_questions_export.csv'}
    )


# ══════════════════════════════════════════════════════════
# 知识点标签
# ══════════════════════════════════════════════════════════

@app.route('/api/tags', methods=['GET'])
def get_all_tags():
    """获取所有已使用的知识点标签"""
    db = get_db()
    rows = db.execute("SELECT knowledge_tags FROM wrong_questions WHERE knowledge_tags != ''").fetchall()
    tag_set = set()
    for r in rows:
        for t in r['knowledge_tags'].split(','):
            t = t.strip()
            if t:
                tag_set.add(t)
    return jsonify(sorted(tag_set))


# ══════════════════════════════════════════════════════════
# 专项突破模式
# ══════════════════════════════════════════════════════════

@app.route('/api/targeted-practice', methods=['POST'])
def targeted_practice():
    """
    专项突破：按章节/错因/知识点标签/难度/题型筛选题目
    {chapter, error_type, tag, difficulty, question_type, count}
    """
    data = request.get_json() or {}
    chapter = data.get('chapter', '').strip()
    error_type = data.get('error_type', '').strip()
    tag = data.get('tag', '').strip()
    difficulty = data.get('difficulty', '').strip()
    question_type = data.get('question_type', '').strip()
    count = int(data.get('count', 15))
    only_unmastered = data.get('only_unmastered', True)

    db = get_db()
    sql = "SELECT * FROM wrong_questions WHERE 1=1"
    params = []

    if only_unmastered:
        sql += " AND mastered=0"

    if chapter:
        sql += " AND chapter=?"
        params.append(chapter)
    if error_type:
        sql += " AND error_type=?"
        params.append(error_type)
    if tag:
        sql += " AND knowledge_tags LIKE ?"
        params.append(f'%{tag}%')
    if difficulty:
        sql += " AND difficulty=?"
        params.append(difficulty)
    if question_type:
        sql += " AND question_type=?"
        params.append(question_type)

    sql += " ORDER BY wrong_count DESC, last_practice_date ASC"
    rows = db.execute(sql, params).fetchall()

    questions = [dict(r) for r in rows]
    if len(questions) <= count:
        random.shuffle(questions)
    else:
        questions = weighted_sample(questions, count, db)

    return jsonify({'questions': questions, 'total_available': len(rows)})


# ══════════════════════════════════════════════════════════
# 趋势图数据
# ══════════════════════════════════════════════════════════

@app.route('/api/stats/trends', methods=['GET'])
def trend_stats():
    """
    趋势数据：按天/周的正确率变化、掌握进度
    period: daily(30天) | weekly(12周)
    """
    period = request.args.get('period', 'daily')
    db = get_db()

    if period == 'weekly':
        rows = db.execute("""
            SELECT strftime('%Y-W%W', date) as week,
                   SUM(CASE WHEN result='correct' THEN 1 ELSE 0 END) as correct,
                   SUM(CASE WHEN result='wrong' THEN 1 ELSE 0 END) as wrong,
                   COUNT(DISTINCT date) as days_practiced
            FROM practice_results
            WHERE date >= date('now','localtime','-84 days')
            GROUP BY week ORDER BY week
        """).fetchall()
        trend = []
        for r in rows:
            total = r['correct'] + r['wrong']
            trend.append({
                'label': r['week'],
                'correct': r['correct'],
                'wrong': r['wrong'],
                'total': total,
                'accuracy': round(r['correct'] / total * 100) if total > 0 else 0,
                'days_practiced': r['days_practiced']
            })
    else:
        rows = db.execute("""
            SELECT date,
                   SUM(CASE WHEN result='correct' THEN 1 ELSE 0 END) as correct,
                   SUM(CASE WHEN result='wrong' THEN 1 ELSE 0 END) as wrong
            FROM practice_results
            WHERE date >= date('now','localtime','-30 days')
            GROUP BY date ORDER BY date
        """).fetchall()
        trend = []
        for r in rows:
            total = r['correct'] + r['wrong']
            trend.append({
                'label': r['date'],
                'correct': r['correct'],
                'wrong': r['wrong'],
                'total': total,
                'accuracy': round(r['correct'] / total * 100) if total > 0 else 0
            })

    # 掌握率变化（按天快照）
    mastery_trend = db.execute("""
        SELECT
            date,
            (SELECT COUNT(*) FROM wrong_questions WHERE mastered=1 AND date_added <= pr.date) as mastered,
            (SELECT COUNT(*) FROM wrong_questions WHERE date_added <= pr.date) as total
        FROM (SELECT DISTINCT date FROM practice_results ORDER BY date) pr
    """).fetchall()

    mastery_data = []
    for r in mastery_trend:
        pct = round(r['mastered'] / r['total'] * 100) if r['total'] > 0 else 0
        mastery_data.append({
            'date': r['date'],
            'mastered': r['mastered'],
            'total': r['total'],
            'pct': pct
        })

    # 学习时长趋势
    duration_rows = db.execute("""
        SELECT date, SUM(duration_seconds) as total_seconds, SUM(question_count) as total_questions
        FROM study_sessions
        WHERE date >= date('now','localtime','-30 days')
        GROUP BY date ORDER BY date
    """).fetchall()
    duration_trend = [{'date': r['date'], 'minutes': round(r['total_seconds'] / 60),
                        'questions': r['total_questions']} for r in duration_rows]

    return jsonify({
        'trend': trend,
        'mastery_trend': mastery_data,
        'duration_trend': duration_trend
    })


# ══════════════════════════════════════════════════════════
# 深度智能分析（4合1）
# ══════════════════════════════════════════════════════════

@app.route('/api/stats/deep-analysis', methods=['GET'])
def deep_analysis():
    """
    深度分析 4 合 1 端点，返回：
      cross_analysis   — 章节 × 错因 × 难度 三维交叉诊断
      forgetting_curve — 已掌握题目回生预警 + 遗忘曲线
      efficiency       — 学习效率画像（速度/正确率/最佳时段）
      action_plan      — 个性化七天行动计划
    """
    db = get_db()
    today = date.today()

    all_q = db.execute("SELECT * FROM wrong_questions").fetchall()
    all_pr = db.execute("SELECT * FROM practice_results ORDER BY date").fetchall()
    all_ss = db.execute("SELECT * FROM study_sessions ORDER BY date, start_time").fetchall()

    # ── 全局基础统计 ──
    total_q = len(all_q)
    mastered_q = sum(1 for q in all_q if q['mastered'])
    remaining_q = total_q - mastered_q

    # ═══════════════════════════════════════════════════
    # 模块1：错误模式交叉诊断 (Chapter × ErrorType × Difficulty)
    # ═══════════════════════════════════════════════════
    cross_matrix = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'mastered': 0, 'wrong_sum': 0}))
    for q in all_q:
        ch = q['chapter'] or '未分类'
        et = q['error_type'] or 'unknown'
        diff = q['difficulty'] or 'unknown'
        key = (ch, et, diff)
        cross_matrix[ch][(et, diff)]['total'] += 1
        cross_matrix[ch][(et, diff)]['wrong_sum'] += q['wrong_count']
        if q['mastered']:
            cross_matrix[ch][(et, diff)]['mastered'] += 1

    dimensions = []
    for ch, etypes in cross_matrix.items():
        for (et, diff), d in etypes.items():
            if d['total'] >= 2:
                mastery_pct = round(d['mastered'] / d['total'] * 100)
                avg_wrong = round(d['wrong_sum'] / d['total'], 1)
                severity = 'critical' if mastery_pct < 30 else 'warning' if mastery_pct < 60 else 'ok'
                dimensions.append({
                    'chapter': ch,
                    'error_type': et,
                    'error_label': ERROR_TYPES.get(et, et),
                    'difficulty': diff,
                    'total': d['total'],
                    'mastered': d['mastered'],
                    'mastery_pct': mastery_pct,
                    'avg_wrong': avg_wrong,
                    'severity': severity
                })

    dimensions.sort(key=lambda x: x['mastery_pct'])

    # 生成交叉分析洞察
    cross_findings = []
    critical_dims = [d for d in dimensions if d['severity'] == 'critical']
    if critical_dims:
        d = critical_dims[0]
        cross_findings.append(f'最致命弱点：【{d["chapter"]}】{d["difficulty"]}难度的{d["error_label"]}题掌握率仅{d["mastery_pct"]}%，需优先攻坚')
    warning_dims = [d for d in dimensions if d['severity'] == 'warning']
    if warning_dims:
        d = warning_dims[0]
        cross_findings.append(f'需重点关注：【{d["chapter"]}】{d["difficulty"]}难度的{d["error_label"]}题掌握率{d["mastery_pct"]}%，容易反复出错')

    # 错因在章节中的分布
    error_by_chapter = defaultdict(lambda: defaultdict(int))
    for q in all_q:
        if q['error_type']:
            error_by_chapter[q['chapter'] or '未分类'][q['error_type']] += 1

    # ═══════════════════════════════════════════════════
    # 模块2：遗忘曲线与回生预警
    # ═══════════════════════════════════════════════════
    # 按题目分组练习记录，重建掌握/遗忘时间线
    pr_by_q = defaultdict(list)
    for pr in all_pr:
        pr_by_q[pr['question_id']].append(dict(pr))

    regressed_questions = []
    forgetting_by_chapter = defaultdict(int)
    forgetting_by_tag = defaultdict(int)
    forgetting_intervals = []  # [(days_until_forget, count)]

    for q in all_q:
        qid = q['id']
        records = sorted(pr_by_q.get(qid, []), key=lambda r: r['date'])
        if not records:
            continue

        mastered_date = None
        consecutive = 0
        for i, r in enumerate(records):
            if r['result'] == 'correct':
                consecutive += 1
            else:
                consecutive = 0
            if consecutive >= MASTER_THRESHOLD:
                mastered_date = r['date']
                # 检查 mastery 点之后是否又有做错的记录
                for j in range(i + 1, len(records)):
                    if records[j]['result'] == 'wrong':
                        days_to_forget = (datetime.strptime(records[j]['date'], '%Y-%m-%d') -
                                          datetime.strptime(mastered_date, '%Y-%m-%d')).days
                        regressed_questions.append({
                            'id': qid,
                            'question_number': q['question_number'],
                            'chapter': q['chapter'] or '未分类',
                            'section': q['section'] or '',
                            'mastered_date': mastered_date,
                            'forgot_date': records[j]['date'],
                            'days_to_forget': days_to_forget,
                            'error_type': q['error_type'] or 'unknown',
                            'knowledge_tags': (q['knowledge_tags'] or '').split(',') if q['knowledge_tags'] else []
                        })
                        forgetting_by_chapter[q['chapter'] or '未分类'] += 1
                        for tag in regressed_questions[-1]['knowledge_tags']:
                            t = tag.strip()
                            if t:
                                forgetting_by_tag[t] += 1
                        forgetting_intervals.append(days_to_forget)
                        break
                break  # 找到第一个 mastery 点后结束

    # 遗忘区间分布
    interval_bins = {'1-3天': 0, '4-7天': 0, '8-14天': 0, '15-30天': 0, '30天以上': 0}
    for days in forgetting_intervals:
        if days <= 3:
            interval_bins['1-3天'] += 1
        elif days <= 7:
            interval_bins['4-7天'] += 1
        elif days <= 14:
            interval_bins['8-14天'] += 1
        elif days <= 30:
            interval_bins['15-30天'] += 1
        else:
            interval_bins['30天以上'] += 1

    # 遗忘曲线数据点（按天数分组统计保留率）
    if forgetting_intervals:
        curve_data = []
        max_interval = max(forgetting_intervals)
        for day in range(1, min(max_interval + 1, 31)):
            still_ok = sum(1 for d in forgetting_intervals if d > day)
            curve_data.append({
                'days': day,
                'retention': round(still_ok / len(forgetting_intervals) * 100)
            })
    else:
        curve_data = []

    regression_rate = round(len(regressed_questions) / total_q * 100, 1) if total_q > 0 else 0

    # ═══════════════════════════════════════════════════
    # 模块3：学习效率画像
    # ═══════════════════════════════════════════════════
    sessions = []
    for s in all_ss:
        date_str = s['date']
        day_results = [pr for pr in all_pr if pr['date'] == date_str]
        day_correct = sum(1 for pr in day_results if pr['result'] == 'correct')
        day_total = len(day_results)
        day_accuracy = round(day_correct / day_total * 100) if day_total > 0 else 0

        hour = 0
        try:
            hour = datetime.strptime(s['start_time'][:19], '%Y-%m-%dT%H:%M:%S').hour
        except:
            pass

        # 速度：每分钟做题数
        minutes = max(s['duration_seconds'] / 60, 0.5)
        speed = round(s['question_count'] / minutes, 1)
        # 专注度：连续练习时长越长越专注（前提是正确率不下降）
        focus = 'high' if s['duration_seconds'] >= 1800 and day_accuracy >= 60 else \
                'medium' if s['duration_seconds'] >= 600 else 'low'

        sessions.append({
            'date': date_str,
            'duration_minutes': round(s['duration_seconds'] / 60),
            'question_count': s['question_count'],
            'accuracy': day_accuracy,
            'speed': speed,  # 题/分钟
            'hour': hour,
            'time_period': '早上' if 6 <= hour < 12 else '下午' if 12 <= hour < 18 else '晚上' if 18 <= hour < 24 else '凌晨',
            'focus': focus
        })

    # 时段效率分析
    period_stats = defaultdict(lambda: {'total_sessions': 0, 'total_accuracy': 0, 'total_speed': 0, 'total_questions': 0})
    for s in sessions:
        p = s['time_period']
        period_stats[p]['total_sessions'] += 1
        period_stats[p]['total_accuracy'] += s['accuracy']
        period_stats[p]['total_speed'] += s['speed']
        period_stats[p]['total_questions'] += s['question_count']

    peak_periods = []
    for p, stats in period_stats.items():
        n = stats['total_sessions']
        peak_periods.append({
            'period': p,
            'sessions': n,
            'avg_accuracy': round(stats['total_accuracy'] / n) if n > 0 else 0,
            'avg_speed': round(stats['total_speed'] / n, 1) if n > 0 else 0,
            'total_questions': stats['total_questions']
        })
    peak_periods.sort(key=lambda x: x['avg_accuracy'], reverse=True)

    # 效率趋势
    if len(sessions) >= 4:
        half = len(sessions) // 2
        early_sessions = sessions[:half]
        late_sessions = sessions[half:]
        early_acc = round(sum(s['accuracy'] for s in early_sessions) / len(early_sessions)) if early_sessions else 0
        late_acc = round(sum(s['accuracy'] for s in late_sessions) / len(late_sessions)) if late_sessions else 0
        early_speed = round(sum(s['speed'] for s in early_sessions) / len(early_sessions), 1) if early_sessions else 0
        late_speed = round(sum(s['speed'] for s in late_sessions) / len(late_sessions), 1) if late_sessions else 0
        efficiency_trend = 'improving' if late_acc >= early_acc * 1.05 else 'declining' if late_acc < early_acc * 0.9 else 'stable'
    else:
        early_acc = late_acc = early_speed = late_speed = 0
        efficiency_trend = 'insufficient_data'

    # ═══════════════════════════════════════════════════
    # 模块4：个性化七天行动计划
    # ═══════════════════════════════════════════════════
    # 章节排序（最弱优先）
    chapter_mastery = []
    ch_agg = defaultdict(lambda: {'total': 0, 'mastered': 0, 'wrong_sum': 0})
    for q in all_q:
        ch = q['chapter'] or '未分类'
        ch_agg[ch]['total'] += 1
        ch_agg[ch]['wrong_sum'] += q['wrong_count']
        if q['mastered']:
            ch_agg[ch]['mastered'] += 1

    for ch, d in ch_agg.items():
        pct = round(d['mastered'] / d['total'] * 100) if d['total'] > 0 else 0
        chapter_mastery.append({
            'chapter': ch,
            'total': d['total'],
            'mastered': d['mastered'],
            'remaining': d['total'] - d['mastered'],
            'mastery_pct': pct,
            'avg_wrong': round(d['wrong_sum'] / d['total'], 1) if d['total'] > 0 else 0
        })
    chapter_mastery.sort(key=lambda x: x['mastery_pct'])

    # 生成计划
    plan_days = []
    weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    weak_chapters = [c for c in chapter_mastery if c['remaining'] > 0 and c['mastery_pct'] < 70]
    strong_chapters = [c for c in chapter_mastery if c['remaining'] == 0 and c['total'] > 0]

    daily_total = int(get_setting('daily_count', DAILY_COUNT_DEFAULT))

    # 取前6个最弱章节分配
    focus_chapters = weak_chapters[:6]

    for i in range(7):
        day_name = weekdays[i]
        if i < len(focus_chapters):
            ch = focus_chapters[i]
            min_q = min(ch['remaining'], max(5, daily_total // 2))
            # 根据该章节的错因分布给出聚焦建议
            ch_errors = error_by_chapter.get(ch['chapter'], {})
            top_error = max(ch_errors, key=ch_errors.get) if ch_errors else ''
            error_label = ERROR_TYPES.get(top_error, '综合')

            plan_days.append({
                'day': day_name,
                'chapter': ch['chapter'],
                'question_count': min(daily_total, max(8, ch['remaining'])),
                'mastery_pct': ch['mastery_pct'],
                'remaining': ch['remaining'],
                'focus_error': error_label,
                'focus_error_key': top_error,
                'tips': f'重点排查{error_label}类题目，每题复盘错误根源',
                'type': 'weak'
            })
        elif i < 6 and strong_chapters:
            ch = strong_chapters[i % len(strong_chapters)]
            plan_days.append({
                'day': day_name,
                'chapter': ch['chapter'],
                'question_count': min(daily_total // 2, 8),
                'mastery_pct': ch['mastery_pct'],
                'remaining': 0,
                'focus_error': '综合复习',
                'focus_error_key': '',
                'tips': '已掌握章节回顾巩固，防止遗忘',
                'type': 'review'
            })
        elif remaining_q > 0 and not weak_chapters and not strong_chapters:
            plan_days.append({
                'day': day_name,
                'chapter': '全部章节',
                'question_count': daily_total,
                'mastery_pct': 0,
                'remaining': remaining_q,
                'focus_error': '综合复习',
                'focus_error_key': '',
                'tips': '均衡练习各章节，加权抽题算法自动分配',
                'type': 'balanced'
            })
        else:
            plan_days.append({
                'day': day_name,
                'chapter': '-',
                'question_count': 0,
                'mastery_pct': 0,
                'remaining': 0,
                'focus_error': '休息日',
                'focus_error_key': '',
                'tips': '保持复习节奏即可',
                'type': 'rest'
            })

    # 周目标
    weekly_questions = sum(d['question_count'] for d in plan_days)
    weekly_target = f'本周计划攻克 {weekly_questions} 题，重点突破{len(focus_chapters)}个薄弱章节'

    # ═══════════════════════════════════════════════════
    # 组装返回
    # ═══════════════════════════════════════════════════
    return jsonify({
        'summary': {
            'total_questions': total_q,
            'mastered': mastered_q,
            'remaining': remaining_q,
            'mastery_pct': round(mastered_q / total_q * 100) if total_q > 0 else 0,
            'total_sessions': len(all_ss),
            'total_practice_days': len(set(pr['date'] for pr in all_pr))
        },
        'cross_analysis': {
            'dimensions': dimensions[:20],  # 最多20条
            'findings': cross_findings,
            'total_dimensions': len(dimensions),
            'critical_count': len(critical_dims),
            'warning_count': len(warning_dims)
        },
        'forgetting_curve': {
            'regressed_questions': regressed_questions[:15],
            'total_regressed': len(regressed_questions),
            'regression_rate': regression_rate,
            'forgetting_intervals': interval_bins,
            'curve_data': curve_data,
            'forgetting_by_chapter': [{'chapter': k, 'count': v} for k, v in
                                       sorted(forgetting_by_chapter.items(), key=lambda x: x[1], reverse=True)],
            'forgetting_by_tag': [{'tag': k, 'count': v} for k, v in
                                   sorted(forgetting_by_tag.items(), key=lambda x: x[1], reverse=True)[:10]]
        },
        'efficiency': {
            'sessions': sessions[-30:],
            'peak_periods': peak_periods,
            'efficiency_trend': efficiency_trend,
            'early_avg_accuracy': early_acc,
            'late_avg_accuracy': late_acc,
            'early_avg_speed': early_speed,
            'late_avg_speed': late_speed,
            'total_minutes': sum(s['duration_minutes'] for s in sessions),
            'total_questions': sum(s['question_count'] for s in sessions),
            'avg_daily_questions': round(sum(s['question_count'] for s in sessions) / max(len(sessions), 1)),
            'best_period': peak_periods[0]['period'] if peak_periods else '数据不足'
        },
        'action_plan': {
            'days': plan_days,
            'weekly_target': weekly_target,
            'daily_count': daily_total,
            'focus_chapters': [c['chapter'] for c in focus_chapters],
            'total_weak_chapters': len(weak_chapters)
        }
    })


# ══════════════════════════════════════════════════════════
# 错误模式聚类
# ══════════════════════════════════════════════════════════

@app.route('/api/stats/clusters', methods=['GET'])
def error_clusters():
    """检测近期连续在同一知识点/错因上出错的模式"""
    db = get_db()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    # 最近7天做错的题目
    recent_wrong = db.execute("""
        SELECT pr.question_id, pr.date as err_date, pr.error_type, wq.knowledge_tags, wq.chapter,
               wq.difficulty, wq.question_number
        FROM practice_results pr
        JOIN wrong_questions wq ON pr.question_id = wq.id
        WHERE pr.result = 'wrong' AND pr.date >= ?
        ORDER BY pr.date DESC
    """, (week_ago,)).fetchall()

    if not recent_wrong:
        return jsonify({'clusters': [], 'alerts': [], 'message': '近7天无错误记录，继续保持！'})

    # 按标签聚类
    tag_clusters = defaultdict(list)
    tag_date_count = defaultdict(set)  # tag → 出错天数
    for r in recent_wrong:
        tags = (r['knowledge_tags'] or '').split(',')
        for t in tags:
            t = t.strip()
            if t:
                tag_clusters[t].append(dict(r))
                tag_date_count[t].add(r['err_date'])

    # 按错因聚类
    error_clusters = defaultdict(list)
    for r in recent_wrong:
        et = r['error_type'] or 'unknown'
        error_clusters[et].append(dict(r))

    # 按 (章节, 错因) 聚类
    chapter_error = defaultdict(list)
    for r in recent_wrong:
        key = (r['chapter'] or '未分类', r['error_type'] or 'unknown')
        chapter_error[key].append(dict(r))

    alerts = []
    # 标签聚类告警
    for tag, items in sorted(tag_clusters.items(), key=lambda x: len(x[1]), reverse=True):
        if len(items) >= 3:
            chapters = set(r['chapter'] for r in items if r.get('chapter'))
            alerts.append({
                'type': 'tag',
                'level': 'critical' if len(items) >= 5 else 'warning',
                'tag': tag,
                'count': len(items),
                'days': len(tag_date_count.get(tag, set())),
                'chapters': list(chapters)[:5],
                'message': f'"{tag}" 知识点近7天出错 {len(items)} 次，建议专题回顾相关理论'
            })

    # 章节×错因聚类告警
    for (ch, et), items in sorted(chapter_error.items(), key=lambda x: len(x[1]), reverse=True):
        if len(items) >= 3:
            et_label = ERROR_TYPES.get(et, et)
            alerts.append({
                'type': 'chapter_error',
                'level': 'critical' if len(items) >= 5 else 'warning',
                'chapter': ch,
                'count': len(items),
                'error_type': et,
                'error_label': et_label,
                'message': f'【{ch}】的{et_label}已连续出错 {len(items)} 次，建议专项突破该章节的{et_label}题型'
            })

    # 难度降级建议
    diff_wrong = defaultdict(int)
    for r in recent_wrong:
        d = r['difficulty'] or 'unknown'
        diff_wrong[d] += 1
    if diff_wrong.get('hard', 0) > diff_wrong.get('easy', 0) * 3:
        alerts.append({
            'type': 'difficulty',
            'level': 'warning',
            'message': f'难题出错率偏高（{diff_wrong["hard"]}次），建议适当回归基础题巩固后再挑战难题'
        })

    # 同章节多标签出错
    ch_tags = defaultdict(lambda: defaultdict(int))
    for r in recent_wrong:
        ch = r['chapter'] or '未分类'
        tags = (r['knowledge_tags'] or '').split(',')
        for t in tags:
            t = t.strip()
            if t:
                ch_tags[ch][t] += 1

    for ch, tags in ch_tags.items():
        for tag, cnt in tags.items():
            if cnt >= 3:
                exists = any(a.get('tag') == tag and a.get('type') == 'tag' for a in alerts)
                if not exists:
                    alerts.append({
                        'type': 'tag',
                        'level': 'warning',
                        'tag': tag,
                        'count': cnt,
                        'chapters': [ch],
                        'message': f'"{tag}" 知识点在【{ch}】中出错 {cnt} 次，建议标注为重点复习对象'
                    })

    alerts.sort(key=lambda a: a['count'] if 'count' in a else 0, reverse=True)

    return jsonify({
        'clusters': {
            'by_tag': [{'tag': k, 'count': len(v)} for k, v in
                        sorted(tag_clusters.items(), key=lambda x: len(x[1]), reverse=True)[:10]],
            'by_error': [{'error_type': k, 'error_label': ERROR_TYPES.get(k, k), 'count': len(v)}
                          for k, v in sorted(error_clusters.items(), key=lambda x: len(x[1]), reverse=True)]
        },
        'alerts': alerts[:8],
        'total_wrong_7d': len(recent_wrong),
        'message': f'近7天共记录 {len(recent_wrong)} 次错误，发现 {len(alerts)} 个聚类模式' if alerts else '近7天无显著错误模式'
    })


# ══════════════════════════════════════════════════════════
# 周报/阶段性总结
# ══════════════════════════════════════════════════════════

@app.route('/api/stats/weekly-report', methods=['GET'])
def weekly_report():
    """生成最近7天的学习周报"""
    db = get_db()
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    prev_week_ago = (today - timedelta(days=14)).isoformat()

    # 本周练习结果
    this_week_pr = db.execute("""
        SELECT result, COUNT(*) as count FROM practice_results
        WHERE date >= ? GROUP BY result
    """, (week_ago,)).fetchall()
    this_correct = 0
    this_total = 0
    for r in this_week_pr:
        this_total += r['count']
        if r['result'] == 'correct':
            this_correct = r['count']
    this_acc = round(this_correct / this_total * 100) if this_total > 0 else 0

    # 上周练习结果（对比）
    prev_week_pr = db.execute("""
        SELECT result, COUNT(*) as count FROM practice_results
        WHERE date >= ? AND date < ? GROUP BY result
    """, (prev_week_ago, week_ago)).fetchall()
    prev_correct = 0
    prev_total = 0
    for r in prev_week_pr:
        prev_total += r['count']
        if r['result'] == 'correct':
            prev_correct = r['count']
    prev_acc = round(prev_correct / prev_total * 100) if prev_total > 0 else 0

    # 本周每日趋势
    daily = db.execute("""
        SELECT date, COUNT(*) as cnt,
               SUM(CASE WHEN result='correct' THEN 1 ELSE 0 END) as correct
        FROM practice_results WHERE date >= ?
        GROUP BY date ORDER BY date
    """, (week_ago,)).fetchall()
    daily_trend = [{'date': r['date'],
                     'total': r['cnt'],
                     'correct': r['correct'],
                     'accuracy': round(r['correct'] / r['cnt'] * 100) if r['cnt'] > 0 else 0}
                    for r in daily]

    # 本周掌握变化
    this_mastered = db.execute(
        "SELECT COUNT(*) as c FROM wrong_questions WHERE mastered=1"
    ).fetchone()['c']
    week_start_mastered = db.execute(
        "SELECT COUNT(*) as c FROM wrong_questions WHERE mastered=1 AND last_practice_date < ?",
        (week_ago,)
    ).fetchone()['c']
    new_mastered = this_mastered - week_start_mastered

    # 本周最多出错的章节
    top_wrong_chapters = db.execute("""
        SELECT wq.chapter, COUNT(*) as cnt
        FROM practice_results pr
        JOIN wrong_questions wq ON pr.question_id = wq.id
        WHERE pr.result = 'wrong' AND pr.date >= ?
        GROUP BY wq.chapter ORDER BY cnt DESC LIMIT 5
    """, (week_ago,)).fetchall()

    # 本周最多出错标签
    top_wrong_tags_rows = db.execute("""
        SELECT wq.knowledge_tags
        FROM practice_results pr
        JOIN wrong_questions wq ON pr.question_id = wq.id
        WHERE pr.result = 'wrong' AND pr.date >= ? AND wq.knowledge_tags != ''
    """, (week_ago,)).fetchall()
    tag_counter = defaultdict(int)
    for r in top_wrong_tags_rows:
        for t in (r['knowledge_tags'] or '').split(','):
            t = t.strip()
            if t:
                tag_counter[t] += 1
    top_wrong_tags = [{'tag': k, 'count': v} for k, v in
                       sorted(tag_counter.items(), key=lambda x: x[1], reverse=True)[:5]]

    # 本周学习时长
    week_ss = db.execute("""
        SELECT SUM(duration_seconds) as total_seconds,
               SUM(question_count) as total_questions,
               COUNT(DISTINCT date) as days
        FROM study_sessions WHERE date >= ?
    """, (week_ago,)).fetchone()
    total_minutes = round((week_ss['total_seconds'] or 0) / 60)
    days_studied = week_ss['days'] or 0
    total_questions = week_ss['total_questions'] or 0

    # 最佳工作日
    best_day = None
    if daily_trend:
        best_day = max(daily_trend, key=lambda d: d['accuracy'] if d['total'] >= 3 else 0)
        if best_day['total'] < 3:
            best_day = None

    # 评语生成
    score = 0
    commentary = []
    if days_studied >= 5:
        commentary.append('坚持天数优秀')
        score += 30
    elif days_studied >= 3:
        commentary.append('坚持天数良好')
        score += 15
    else:
        commentary.append('本周练习天数偏少，下周加油')
        score += 5

    if this_acc >= 70:
        commentary.append('正确率较高')
        score += 25
    elif this_acc >= 50:
        commentary.append('正确率中等，仍有提升空间')
        score += 15
    else:
        commentary.append('正确率偏低，建议放慢节奏确保理解')
        score += 5

    if new_mastered >= 10:
        commentary.append(f'新掌握{new_mastered}题，进步显著')
        score += 25
    elif new_mastered >= 5:
        commentary.append(f'新掌握{new_mastered}题，稳中有进')
        score += 15
    else:
        commentary.append('本周掌握进度偏慢')
        score += 5

    if this_acc > prev_acc and prev_total > 0:
        commentary.append('正确率较上周上升')
        score += 20
    elif prev_total > 0:
        commentary.append('正确率与上周持平或略有下降')
        score += 10
    else:
        score += 10

    grade = 'S' if score >= 80 else 'A' if score >= 60 else 'B' if score >= 40 else 'C'

    return jsonify({
        'period': f'{week_ago} ~ {today.isoformat()}',
        'score': score,
        'grade': grade,
        'commentary': commentary,
        'stats': {
            'total_questions': total_questions,
            'accuracy': this_acc,
            'prev_accuracy': prev_acc,
            'days_studied': days_studied,
            'total_minutes': total_minutes,
            'new_mastered': new_mastered,
            'total_mastered': this_mastered
        },
        'daily_trend': daily_trend,
        'top_wrong_chapters': [{'chapter': r['chapter'] or '未分类', 'count': r['cnt']}
                                for r in top_wrong_chapters],
        'top_wrong_tags': top_wrong_tags,
        'best_day': {'date': best_day['date'], 'accuracy': best_day['accuracy']} if best_day else None
    })


# ══════════════════════════════════════════════════════════
# 目标倒推计划
# ══════════════════════════════════════════════════════════

@app.route('/api/stats/goal-plan', methods=['GET'])
def goal_plan():
    """根据考试日期反推每日计划"""
    db = get_db()
    today = date.today()

    exam_date_str = get_setting('exam_date', '')
    if not exam_date_str:
        return jsonify({'set': False, 'message': '请先在设置中填写考试日期'})

    try:
        exam_date = datetime.strptime(exam_date_str, '%Y-%m-%d').date()
    except:
        return jsonify({'set': False, 'message': '考试日期格式错误'})

    remaining_days = (exam_date - today).days
    if remaining_days <= 0:
        return jsonify({'set': True, 'remaining_days': 0,
                         'message': '考试日期已过！请更新设置', 'urgent': True})

    # 当前进度
    total_q = db.execute("SELECT COUNT(*) as c FROM wrong_questions").fetchone()['c']
    mastered_q = db.execute("SELECT COUNT(*) as c FROM wrong_questions WHERE mastered=1").fetchone()['c']
    remaining_q = total_q - mastered_q
    overall_pct = round(mastered_q / total_q * 100) if total_q > 0 else 0

    if remaining_q == 0:
        return jsonify({
            'set': True, 'remaining_days': remaining_days,
            'remaining_questions': 0, 'overall_pct': 100,
            'message': '所有错题已掌握！可以用剩余时间做模拟测试巩固',
            'daily_target': 0, 'review_daily': 15
        })

    # 每日需要掌握量
    daily_target = round(remaining_q / remaining_days, 1)
    # 考虑回顾已掌握题
    review_daily = min(10, max(3, mastered_q // max(remaining_days // 3, 1))) if mastered_q > 0 else 0

    # 紧迫度
    urgent_level = 'relaxed'
    if remaining_days <= 7:
        urgent_level = 'critical'
        urgent_msg = f'仅剩 {remaining_days} 天！每天必须攻克 {daily_target} 道题'
    elif remaining_days <= 14:
        urgent_level = 'tight'
        urgent_msg = f'时间紧迫，剩余 {remaining_days} 天，每日需完成 {daily_target} 题'
    elif remaining_days <= 30:
        urgent_level = 'normal'
        urgent_msg = f'时间充裕，每日 {daily_target} 题即可在考试前攻克全部错题'
    else:
        urgent_level = 'relaxed'
        urgent_msg = f'时间充足，每日只需 {daily_target} 题，建议适当增加每日题量加速进度'

    # 章节优先级
    chapters = db.execute("""
        SELECT chapter, COUNT(*) as total,
               SUM(CASE WHEN mastered=1 THEN 1 ELSE 0 END) as mastered
        FROM wrong_questions GROUP BY chapter ORDER BY chapter
    """).fetchall()

    chapter_plan = []
    for ch in chapters:
        ch_remain = ch['total'] - ch['mastered']
        if ch_remain > 0:
            ch_pct = round(ch['mastered'] / ch['total'] * 100)
            # 该章节每日建议
            ch_weight = ch_remain / remaining_q if remaining_q > 0 else 0
            ch_daily = max(1, round(daily_target * ch_weight))
            chapter_plan.append({
                'chapter': ch['chapter'] or '未分类',
                'total': ch['total'],
                'mastered': ch['mastered'],
                'remaining': ch_remain,
                'mastery_pct': ch_pct,
                'daily_suggest': ch_daily,
                'urgency': 'critical' if ch_pct < 30 else 'warning' if ch_pct < 60 else 'ok'
            })

    chapter_plan.sort(key=lambda x: x['mastery_pct'])

    # 里程碑节点
    milestones = []
    if remaining_days >= 30:
        milestones.append({'days_left': 30, 'date': (today + timedelta(days=remaining_days - 30)).isoformat(),
                            'target_pct': round((mastered_q + daily_target * (remaining_days - 30)) / total_q * 100),
                            'label': '距考试30天'})
    if remaining_days >= 14:
        milestones.append({'days_left': 14, 'date': (today + timedelta(days=remaining_days - 14)).isoformat(),
                            'target_pct': round((mastered_q + daily_target * (remaining_days - 14)) / total_q * 100),
                            'label': '距考试14天'})
    if remaining_days >= 7:
        milestones.append({'days_left': 7, 'date': (today + timedelta(days=remaining_days - 7)).isoformat(),
                            'target_pct': round((mastered_q + daily_target * (remaining_days - 7)) / total_q * 100),
                            'label': '距考试7天'})

    return jsonify({
        'set': True,
        'exam_date': exam_date_str,
        'remaining_days': remaining_days,
        'remaining_questions': remaining_q,
        'total_questions': total_q,
        'overall_pct': overall_pct,
        'daily_target': daily_target,
        'review_daily': review_daily,
        'urgent_level': urgent_level,
        'urgent_message': urgent_msg,
        'chapter_plan': chapter_plan[:10],
        'milestones': milestones
    })


# ══════════════════════════════════════════════════════════
# 学习曲线预测
# ══════════════════════════════════════════════════════════

@app.route('/api/stats/prediction', methods=['GET'])
def learning_prediction():
    """基于历史数据预测攻克全部错题还需多久"""
    db = get_db()
    today = date.today()

    total = db.execute("SELECT COUNT(*) as c FROM wrong_questions").fetchone()['c']
    mastered = db.execute("SELECT COUNT(*) as c FROM wrong_questions WHERE mastered=1").fetchone()['c']
    remaining = total - mastered

    if remaining == 0:
        return jsonify({
            'remaining': 0, 'total': total, 'mastered': mastered,
            'predicted_days': 0, 'prediction_date': today.isoformat(),
            'daily_pace_needed': 0, 'message': '恭喜！所有错题已全部掌握！'
        })

    # 最近14天数据
    fourteen_days_ago = (today - timedelta(days=14)).isoformat()
    recent_results = db.execute("""
        SELECT date, COUNT(DISTINCT question_id) as practiced,
               SUM(CASE WHEN result='correct' THEN 1 ELSE 0 END) as correct
        FROM practice_results WHERE date >= ?
        GROUP BY date ORDER BY date
    """, (fourteen_days_ago,)).fetchall()

    # 每日平均做题量
    practicing_days = len(recent_results)
    total_practiced = sum(r['practiced'] for r in recent_results)
    daily_avg = round(total_practiced / practicing_days, 1) if practicing_days > 0 else 0

    # 每日平均掌握量（通过 mastered 变化推算）
    daily_mastered = 0
    if practicing_days >= 2:
        # 统计14天前和现在的掌握差
        old_mastered_count = db.execute(
            "SELECT COUNT(*) as c FROM wrong_questions WHERE mastered=1 AND date_added <= ?",
            (fourteen_days_ago,)
        ).fetchone()['c']
        mastery_diff = mastered - old_mastered_count
        daily_mastered = round(mastery_diff / practicing_days, 1) if practicing_days > 0 else 0

    # 预测：剩余数 / 日均掌握速度
    if daily_mastered > 0:
        predicted_days = int(remaining / daily_mastered)
    elif daily_avg > 0 and total_practiced > 0:
        # 用做题量估算掌握速度（假设当前掌握比例为参考）
        correct_in_period = sum(r['correct'] for r in recent_results)
        overall_accuracy = correct_in_period / total_practiced if total_practiced > 0 else 0.5
        # 修正：实际掌握需要连续正确
        effective_rate = daily_avg * overall_accuracy * 0.5  # 保守估计
        predicted_days = int(remaining / effective_rate) if effective_rate > 0 else -1
    else:
        predicted_days = -1

    prediction_date = None
    if predicted_days > 0:
        prediction_date = (today + timedelta(days=predicted_days)).isoformat()

    # 章节级预测
    chapter_predictions = []
    chapters = db.execute("""
        SELECT chapter,
               COUNT(*) as total,
               SUM(CASE WHEN mastered=1 THEN 1 ELSE 0 END) as mastered
        FROM wrong_questions GROUP BY chapter ORDER BY chapter
    """).fetchall()

    for ch in chapters:
        ch_remain = ch['total'] - ch['mastered']
        if ch_remain > 0:
            rate = daily_mastered if daily_mastered > 0 else (daily_avg * 0.3) if daily_avg > 0 else 1
            ch_days = int(ch_remain / rate)
            chapter_predictions.append({
                'chapter': ch['chapter'],
                'total': ch['total'],
                'mastered': ch['mastered'],
                'remaining': ch_remain,
                'predicted_days': ch_days,
                'prediction_date': (today + timedelta(days=ch_days)).isoformat()
            })

    # 最近7天趋势判断（加速/匀速/减速）
    week_ago = (today - timedelta(days=7)).isoformat()
    week_results = db.execute("""
        SELECT date, COUNT(DISTINCT question_id) as practiced,
               SUM(CASE WHEN result='correct' THEN 1 ELSE 0 END) as correct
        FROM practice_results WHERE date >= ? AND date < ?
        GROUP BY date ORDER BY date
    """, (fourteen_days_ago, week_ago)).fetchall()

    this_week = db.execute("""
        SELECT date, COUNT(DISTINCT question_id) as practiced,
               SUM(CASE WHEN result='correct' THEN 1 ELSE 0 END) as correct
        FROM practice_results WHERE date >= ?
        GROUP BY date ORDER BY date
    """, (week_ago,)).fetchall()

    prev_week_total = sum(r['practiced'] for r in week_results)
    this_week_total = sum(r['practiced'] for r in this_week)
    prev_week_days = len(week_results)
    this_week_days = len(this_week)

    prev_avg = round(prev_week_total / prev_week_days, 1) if prev_week_days > 0 else 0
    this_avg = round(this_week_total / this_week_days, 1) if this_week_days > 0 else 0

    if this_avg > prev_avg * 1.15:
        trend = 'accelerating'
        trend_label = '加速中'
    elif this_avg < prev_avg * 0.7 and prev_avg > 0:
        trend = 'slowing'
        trend_label = '需加把劲'
    else:
        trend = 'steady'
        trend_label = '稳步推进'

    # 按照当前速度，计算完成目标需要的每日最低题数（假设30天目标）
    target_days = 30
    needed_per_day = round(remaining / target_days, 1) if target_days > 0 else 0

    return jsonify({
        'total': total,
        'mastered': mastered,
        'remaining': remaining,
        'daily_avg_practiced': daily_avg,
        'daily_avg_mastered': daily_mastered,
        'predicted_days': predicted_days,
        'prediction_date': prediction_date,
        'prediction_period': f"共 {total} 题 · 已掌握 {mastered} · 剩余 {remaining}",
        'chapter_predictions': chapter_predictions,
        'trend': trend,
        'trend_label': trend_label,
        'prev_week_avg': prev_avg,
        'this_week_avg': this_avg,
        'needed_per_day': needed_per_day,
        'target_days': target_days,
        'practicing_days': practicing_days
    })


# ══════════════════════════════════════════════════════════
# 薄弱点智能诊断
# ══════════════════════════════════════════════════════════

@app.route('/api/stats/diagnosis', methods=['GET'])
def smart_diagnosis():
    """智能诊断报告：找出最薄弱环节并给出建议"""
    db = get_db()

    # 各章节掌握率
    chapters = db.execute("""
        SELECT chapter,
               COUNT(*) as total,
               SUM(CASE WHEN mastered=1 THEN 1 ELSE 0 END) as mastered,
               AVG(wrong_count * 1.0) as avg_wrong,
               SUM(CASE WHEN error_type='careless' THEN 1 ELSE 0 END) as careless,
               SUM(CASE WHEN error_type='concept' THEN 1 ELSE 0 END) as concept,
               SUM(CASE WHEN error_type='calculation' THEN 1 ELSE 0 END) as calc,
               SUM(CASE WHEN error_type='method' THEN 1 ELSE 0 END) as method
        FROM wrong_questions GROUP BY chapter ORDER BY chapter
    """).fetchall()

    weak_chapters = []
    for r in chapters:
        mastery = round(r['mastered'] / r['total'] * 100) if r['total'] > 0 else 0
        if mastery < 50 and r['total'] >= 3:
            # 找出该章节最多的错因
            etypes = {'careless': r['careless'], 'concept': r['concept'],
                      'calculation': r['calc'], 'method': r['method']}
            top_etype = max(etypes, key=etypes.get)
            weak_chapters.append({
                'chapter': r['chapter'],
                'mastery_pct': mastery,
                'total': r['total'],
                'avg_wrong': round(r['avg_wrong'], 1),
                'top_error': ERROR_TYPES.get(top_etype, top_etype),
                'top_error_count': etypes[top_etype]
            })

    # 错因严重度排序
    error_dist = db.execute("""
        SELECT error_type, COUNT(*) as count
        FROM wrong_questions WHERE error_type != ''
        GROUP BY error_type ORDER BY count DESC
    """).fetchall()

    error_ranking = []
    total_errors = sum(r['count'] for r in error_dist)
    for r in error_dist:
        error_ranking.append({
            'type': r['error_type'],
            'label': ERROR_TYPES.get(r['error_type'], r['error_type']),
            'count': r['count'],
            'pct': round(r['count'] / total_errors * 100) if total_errors > 0 else 0
        })

    # 长期未练习的题目
    stale = db.execute("""
        SELECT question_number, chapter, section, wrong_count,
               COALESCE(last_practice_date, last_wrong_date, date_added) as last_date
        FROM wrong_questions
        WHERE mastered=0
          AND COALESCE(last_practice_date, last_wrong_date, date_added) < date('now','localtime','-14 days')
        ORDER BY COALESCE(last_practice_date, last_wrong_date, date_added)
        LIMIT 10
    """).fetchall()

    stale_list = [dict(r) for r in stale]

    # 近期正确率趋势（判断进步/退步）
    recent = db.execute("""
        SELECT result, COUNT(*) as count
        FROM practice_results
        WHERE date >= date('now','localtime','-7 days')
        GROUP BY result
    """).fetchall()
    recent_correct = 0
    recent_total = 0
    for r in recent:
        if r['result'] == 'correct':
            recent_correct = r['count']
        recent_total += r['count']

    # 更早期对比
    earlier = db.execute("""
        SELECT result, COUNT(*) as count
        FROM practice_results
        WHERE date >= date('now','localtime','-14 days')
          AND date < date('now','localtime','-7 days')
        GROUP BY result
    """).fetchall()
    earlier_correct = 0
    earlier_total = 0
    for r in earlier:
        if r['result'] == 'correct':
            earlier_correct = r['count']
        earlier_total += r['count']

    recent_acc = round(recent_correct / recent_total * 100) if recent_total > 0 else 0
    earlier_acc = round(earlier_correct / earlier_total * 100) if earlier_total > 0 else 0

    trend_direction = 'up' if recent_acc >= earlier_acc else 'down'

    # 生成建议
    suggestions = []
    if weak_chapters:
        top_weak = weak_chapters[0]
        suggestions.append(f'最薄弱章节是【{top_weak["chapter"]}】，掌握率仅{top_weak["mastery_pct"]}%，推荐专项突破')

    if error_ranking:
        top_error = error_ranking[0]
        suggestions.append(f'最常见错因是【{top_error["label"]}】(占{top_error["pct"]}%)，需要针对性训练')

    if stale_list:
        suggestions.append(f'有{len(stale)}道题超过14天未练习，建议及时回顾')

    if trend_direction == 'down' and recent_acc < 60:
        suggestions.append('近期正确率有下降趋势，建议放慢节奏，确保每题真正理解')

    total_mastery = db.execute(
        "SELECT COUNT(*) as c FROM wrong_questions WHERE mastered=1"
    ).fetchone()['c']
    total_all = db.execute("SELECT COUNT(*) as c FROM wrong_questions").fetchone()['c']
    overall_pct = round(total_mastery / total_all * 100) if total_all > 0 else 0

    # 学习时长汇总
    time_stats = db.execute("""
        SELECT
            SUM(duration_seconds) as total_seconds,
            COUNT(DISTINCT date) as days_studied,
            SUM(question_count) as total_questions
        FROM study_sessions
    """).fetchone()

    return jsonify({
        'weak_chapters': sorted(weak_chapters, key=lambda x: x['mastery_pct']),
        'error_ranking': error_ranking,
        'stale_questions': stale_list,
        'recent_accuracy': recent_acc,
        'earlier_accuracy': earlier_acc,
        'trend_direction': trend_direction,
        'suggestions': suggestions,
        'overall': {
            'mastered': total_mastery,
            'total': total_all,
            'pct': overall_pct
        },
        'time_stats': {
            'total_minutes': round((time_stats['total_seconds'] or 0) / 60),
            'days_studied': time_stats['days_studied'] or 0,
            'total_questions': time_stats['total_questions'] or 0
        }
    })


# ══════════════════════════════════════════════════════════
# 学习时间记录
# ══════════════════════════════════════════════════════════

@app.route('/api/study-time', methods=['POST'])
def record_study_time():
    """记录学习时间 {duration_seconds, question_count, start_time?, end_time?}"""
    data = request.get_json()
    today = date.today().isoformat()
    duration = int(data.get('duration_seconds', 0))
    question_count = int(data.get('question_count', 0))
    start_time = data.get('start_time', datetime.now().isoformat())
    end_time = data.get('end_time', datetime.now().isoformat())

    db = get_db()
    db.execute(
        "INSERT INTO study_sessions (date, start_time, end_time, duration_seconds, question_count) VALUES (?,?,?,?,?)",
        (today, start_time, end_time, duration, question_count)
    )
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/study-time', methods=['GET'])
def get_study_time():
    """获取学习时间统计，period: today|week|month|all"""
    period = request.args.get('period', 'today')
    db = get_db()

    if period == 'today':
        date_filter = "date = date('now','localtime')"
    elif period == 'week':
        date_filter = "date >= date('now','localtime','-6 days')"
    elif period == 'month':
        date_filter = "date >= date('now','localtime','-29 days')"
    else:
        date_filter = "1=1"

    row = db.execute(f"""
        SELECT
            SUM(duration_seconds) as total_seconds,
            COUNT(DISTINCT date) as days,
            SUM(question_count) as total_questions,
            COUNT(*) as sessions
        FROM study_sessions WHERE {date_filter}
    """).fetchone()

    # 每日明细 (最近7天)
    daily = db.execute("""
        SELECT date, SUM(duration_seconds) as seconds, SUM(question_count) as questions
        FROM study_sessions
        WHERE date >= date('now','localtime','-6 days')
        GROUP BY date ORDER BY date
    """).fetchall()

    return jsonify({
        'total_minutes': round((row['total_seconds'] or 0) / 60),
        'days': row['days'] or 0,
        'total_questions': row['total_questions'] or 0,
        'sessions': row['sessions'] or 0,
        'daily': [{'date': r['date'], 'minutes': round(r['seconds'] / 60),
                    'questions': r['questions']} for r in daily]
    })

# ══════════════════════════════════════════════════════════
# 数据同步（离线版 ↔ 在线版）
# ══════════════════════════════════════════════════════════

@app.route('/api/sync/export', methods=['GET'])
def sync_export():
    """导出全量数据为 JSON，与离线版格式兼容"""
    db = get_db()

    # 错题（含 image_data）
    questions = db.execute("SELECT * FROM wrong_questions ORDER BY chapter, section, question_number").fetchall()
    qlist = []
    for r in questions:
        item = dict(r)
        qlist.append(item)

    # 练习结果
    results = db.execute("SELECT * FROM practice_results ORDER BY date").fetchall()
    rlist = [dict(r) for r in results]

    # 每日练习记录
    daily = db.execute("SELECT * FROM daily_practice ORDER BY date").fetchall()
    dlist = [dict(r) for r in daily]

    return jsonify({
        'version': 1,
        'exported_at': datetime.now().isoformat(),
        'wrong_questions': qlist,
        'practice_results': rlist,
        'daily_practice': dlist
    })


@app.route('/api/sync/import', methods=['POST'])
def sync_import():
    """从 JSON 导入全量数据，合并到现有数据库"""
    data = request.get_json()
    if not data or 'wrong_questions' not in data:
        return jsonify({'ok': False, 'error': '格式不正确'}), 400

    db = get_db()
    imported = 0
    updated = 0

    # 导入错题（按 章节+小节+题号 去重合并）
    for item in data.get('wrong_questions', []):
        existing = db.execute(
            "SELECT id FROM wrong_questions WHERE question_number=? AND chapter=? AND section=?",
            (item.get('question_number', ''), item.get('chapter', ''), item.get('section', ''))
        ).fetchone()

        if existing:
            # 更新错误次数等字段
            db.execute("""
                UPDATE wrong_questions
                SET wrong_count = COALESCE(?, wrong_count),
                    consecutive_correct = COALESCE(?, consecutive_correct),
                    mastered = COALESCE(?, mastered),
                    error_type = COALESCE(NULLIF(?, ''), error_type),
                    note = COALESCE(NULLIF(?, ''), note),
                    knowledge_tags = COALESCE(NULLIF(?, ''), knowledge_tags),
                    difficulty = COALESCE(NULLIF(?, ''), difficulty),
                    question_type = COALESCE(NULLIF(?, ''), question_type),
                    image_data = COALESCE(?, image_data),
                    last_wrong_date = COALESCE(?, last_wrong_date),
                    last_practice_date = COALESCE(?, last_practice_date)
                WHERE id = ?
            """, (
                item.get('wrong_count'), item.get('consecutive_correct'),
                item.get('mastered'), item.get('error_type'),
                item.get('note'), item.get('knowledge_tags'),
                item.get('difficulty'), item.get('question_type'),
                item.get('image_data'),
                item.get('last_wrong_date'), item.get('last_practice_date'),
                existing['id']
            ))
            updated += 1
        else:
            db.execute("""
                INSERT INTO wrong_questions
                (question_number, chapter, section, source, error_type, note,
                 wrong_count, consecutive_correct, mastered,
                 knowledge_tags, difficulty, question_type,
                 image_data, date_added, last_wrong_date, last_practice_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get('question_number', ''), item.get('chapter', ''),
                item.get('section', ''), item.get('source', ''),
                item.get('error_type', ''), item.get('note', ''),
                item.get('wrong_count', 1), item.get('consecutive_correct', 0),
                item.get('mastered', 0),
                item.get('knowledge_tags', ''), item.get('difficulty', ''),
                item.get('question_type', ''),
                item.get('image_data'),
                item.get('date_added', date.today().isoformat()),
                item.get('last_wrong_date', ''), item.get('last_practice_date', '')
            ))
            imported += 1

    # 导入练习结果（按 question_id + date 去重）
    pr_imported = 0
    for r in data.get('practice_results', []):
        existing = db.execute(
            "SELECT id FROM practice_results WHERE question_id=? AND date=? AND result=?",
            (r.get('question_id'), r.get('date'), r.get('result'))
        ).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO practice_results (question_id, date, result, error_type) VALUES (?, ?, ?, ?)",
                (r.get('question_id'), r.get('date'), r.get('result'), r.get('error_type', ''))
            )
            pr_imported += 1

    # 导入每日练习
    dp_imported = 0
    for d in data.get('daily_practice', []):
        existing = db.execute(
            "SELECT date FROM daily_practice WHERE date=?",
            (d.get('date'),)
        ).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO daily_practice (date, questions, created_at) VALUES (?, ?, ?)",
                (d.get('date'), d.get('questions', '[]'), d.get('created_at', ''))
            )
            dp_imported += 1

    db.commit()

    return jsonify({
        'ok': True,
        'questions_imported': imported,
        'questions_updated': updated,
        'results_imported': pr_imported,
        'daily_imported': dp_imported
    })


def weighted_sample(questions: list, total: int, db) -> list:
    """
    权重抽题算法：
    1. 排除已掌握的题目
    2. 按章节均衡分配名额
    3. 章节内部按权重抽取：
       - 错误次数权重（k * wrong_count）
       - 新鲜度加成（7 天内新增 +50%）
       - 陈旧度加成（30 天未练习 +100% 强制概率）
    """
    if len(questions) <= total:
        random.shuffle(questions)
        return questions

    # 按章节分组
    chapter_map = defaultdict(list)
    for q in questions:
        chapter_map[q['chapter']].append(q)

    chapters = list(chapter_map.keys())

    # 章节数 >= total — 每个抽 1 题
    if len(chapters) >= total:
        chosen_chapters = random.sample(chapters, total)
        result = []
        for ch in chosen_chapters:
            result.append(_weighted_pick(chapter_map[ch]))
        random.shuffle(result)
        return result

    result = []
    remaining = total
    today = date.today().isoformat()

    # 每章先分配 1 题
    for ch in chapters:
        pick = _weighted_pick(chapter_map[ch])
        result.append(pick)
        chapter_map[ch] = [q for q in chapter_map[ch] if q['id'] != pick['id']]
        remaining -= 1

    # 剩余名额轮询（优先分配给题目多的章节）
    active_chapters = [ch for ch in chapters if chapter_map[ch]]
    while remaining > 0 and active_chapters:
        for ch in list(active_chapters):
            if remaining <= 0:
                break
            if chapter_map[ch]:
                pick = _weighted_pick(chapter_map[ch])
                result.append(pick)
                chapter_map[ch] = [q for q in chapter_map[ch] if q['id'] != pick['id']]
                remaining -= 1
            if not chapter_map[ch]:
                active_chapters.remove(ch)

    random.shuffle(result)
    return result


def _weighted_pick(questions: list) -> dict:
    """按权重从列表中随机选 1 题"""
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    month_ago = (date.today() - timedelta(days=30)).isoformat()

    weights = []
    for q in questions:
        w = q.get('wrong_count', 1)

        # 新鲜度加成：7 天内新增的题权重 +50%
        if q.get('date_added', '') >= week_ago:
            w = int(w * 1.5) + 1

        # 陈旧强制：30 天未练习的题大幅加权
        lp = q.get('last_practice_date') or ''
        if lp and lp < month_ago:
            w = max(w, 5)

        # 从未练习过的题也要有一定权重
        if not lp:
            w = max(w, 3)

        weights.append(w)

    total_w = sum(weights)
    if total_w == 0:
        return random.choice(questions)

    r = random.uniform(0, total_w)
    cumulative = 0
    for i, w in enumerate(weights):
        cumulative += w
        if r <= cumulative:
            return questions[i]

    return questions[-1]


# ══════════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    import socket
    init_db()
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'

    # 获取本机 LAN IP
    lan_ip = '127.0.0.1'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except:
        pass

    print(f"=" * 56)
    print(f"  错题管理智能体 v2 已启动")
    print(f"  本机访问: http://127.0.0.1:{port}")
    print(f"  手机访问: http://{lan_ip}:{port}")
    print(f"=" * 56)
    app.run(debug=debug, host='0.0.0.0', port=port)
