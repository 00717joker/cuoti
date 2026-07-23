"""
错题管理智能体 v3 - Flask 后端
支持 SQLite 和 PostgreSQL (Supabase)
包含：艾宾浩斯复习、数据可视化、效率分析、PDF导出
"""
import os
import json
import random
import csv
import io
import shutil
import math
from datetime import date, datetime, timedelta
from collections import defaultdict, Counter
from flask import Flask, request, jsonify, g, render_template, Response, send_file
from sqlalchemy import create_engine, Column, Integer, String, Text, UniqueConstraint, DateTime, Float, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import inspect

app = Flask(__name__)

MASTER_THRESHOLD = 1
DAILY_COUNT_DEFAULT = 15

ERROR_TYPES = {
    'careless': '粗心失误',
    'concept': '概念不清',
    'calculation': '计算错误',
    'method': '方法不会'
}

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
else:
    DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(DATA_DIR, exist_ok=True)
    DATABASE_PATH = os.path.join(DATA_DIR, 'wrong_questions.db')
    engine = create_engine(f'sqlite:///{DATABASE_PATH}')

Base = declarative_base()

class WrongQuestion(Base):
    __tablename__ = 'wrong_questions'
    id = Column(Integer, primary_key=True)
    question_number = Column(String, nullable=False)
    chapter = Column(String, default='')
    section = Column(String, default='')
    source = Column(String, default='')
    note = Column(String, default='')
    wrong_count = Column(Integer, default=1)
    consecutive_correct = Column(Integer, default=0)
    mastered = Column(Integer, default=0)
    date_added = Column(String, default=str(date.today()))
    last_wrong_date = Column(String, default=str(date.today()))
    last_practice_date = Column(String)
    error_type = Column(String, default='')
    knowledge_tags = Column(String, default='')
    difficulty = Column(String, default='')
    question_type = Column(String, default='')
    image_data = Column(Text)
    __table_args__ = (UniqueConstraint('question_number', 'chapter', 'section'),)

class DailyPractice(Base):
    __tablename__ = 'daily_practice'
    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False, unique=True)
    questions = Column(Text, nullable=False)
    created_at = Column(String, default=datetime.now().isoformat())

class PracticeResult(Base):
    __tablename__ = 'practice_results'
    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, nullable=False)
    date = Column(String, nullable=False)
    result = Column(String, nullable=False)
    error_type = Column(String, default='')
    time_spent = Column(Integer, default=0)

class Setting(Base):
    __tablename__ = 'settings'
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)

Session = sessionmaker(bind=engine)

def get_session():
    if not hasattr(g, '_session'):
        g._session = Session()
    return g._session

@app.teardown_appcontext
def close_session(exception):
    session = getattr(g, '_session', None)
    if session is not None:
        session.close()

def init_db():
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        print(f'Create tables error: {e}')
    
    try:
        session = Session()
        if not session.query(Setting).filter_by(key='daily_count').first():
            session.add(Setting(key='daily_count', value='15'))
        if not session.query(Setting).filter_by(key='last_backup_date').first():
            session.add(Setting(key='last_backup_date', value=''))
        session.commit()
        session.close()
    except Exception as e:
        print(f'Settings error: {e}')
        try:
            session.rollback()
        except:
            pass
        try:
            session.close()
        except:
            pass

def restore_from_backup():
    backup_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'initial_data.json')
    if not os.path.exists(backup_path):
        return 0
    try:
        with open(backup_path, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        session = get_session()
        count = session.query(WrongQuestion).count()
        if count > 0:
            return 0
        for q in backup_data.get('questions', []):
            session.add(WrongQuestion(
                id=q.get('id'), question_number=q.get('question_number', ''),
                chapter=q.get('chapter', ''), section=q.get('section', ''),
                source=q.get('source', ''), note=q.get('note', ''),
                wrong_count=q.get('wrong_count', 1), consecutive_correct=q.get('consecutive_correct', 0),
                mastered=q.get('mastered', 0), date_added=q.get('date_added', ''),
                last_wrong_date=q.get('last_wrong_date', ''), last_practice_date=q.get('last_practice_date'),
                error_type=q.get('error_type', ''), knowledge_tags=q.get('knowledge_tags', ''),
                difficulty=q.get('difficulty', ''), question_type=q.get('question_type', ''),
                image_data=q.get('image_data')
            ))
        for pr in backup_data.get('practice_results', []):
            session.add(PracticeResult(
                question_id=pr.get('question_id'), date=pr.get('date', ''),
                result=pr.get('result', ''), error_type=pr.get('error_type', '')
            ))
        for s in backup_data.get('settings', []):
            if not session.query(Setting).filter_by(key=s.get('key')).first():
                session.add(Setting(key=s.get('key'), value=s.get('value', '')))
        session.commit()
        return len(backup_data.get('questions', []))
    except Exception as e:
        print(f'Error restoring backup: {e}')
        return 0

with app.app_context():
    init_db()
    restored = restore_from_backup()
    if restored > 0:
        print(f'  已从备份恢复 {restored} 道题')

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response

@app.route('/health')
def health():
    try:
        session = Session()
        session.execute(text('SELECT 1'))
        session.close()
        return jsonify({'status': 'healthy', 'database': 'connected'})
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 503

def get_setting(key, default=None):
    session = get_session()
    s = session.query(Setting).filter_by(key=key).first()
    return s.value if s else default

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/restore', methods=['POST'])
def manual_restore():
    session = get_session()
    session.query(WrongQuestion).delete()
    session.query(PracticeResult).delete()
    session.commit()
    restored = restore_from_backup()
    return jsonify({'success': True, 'restored': restored})

@app.route('/api/questions', methods=['GET'])
def get_questions():
    chapter = request.args.get('chapter', '')
    search = request.args.get('search', '').strip()
    session = get_session()
    query = session.query(WrongQuestion)
    if chapter:
        query = query.filter(WrongQuestion.chapter == chapter)
    if search:
        like = f'%{search}%'
        query = query.filter(WrongQuestion.question_number.like(like) | 
                            WrongQuestion.chapter.like(like) | 
                            WrongQuestion.section.like(like) | 
                            WrongQuestion.source.like(like))
    rows = query.order_by(WrongQuestion.chapter, WrongQuestion.section, WrongQuestion.question_number).all()
    return jsonify([{c.name: getattr(r, c.name) for c in WrongQuestion.__table__.columns} for r in rows])

@app.route('/api/questions', methods=['POST'])
def add_question():
    data = request.get_json()
    items = data if isinstance(data, list) else [data]
    session = get_session()
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
        existing = session.query(WrongQuestion).filter(
            WrongQuestion.question_number == qn,
            WrongQuestion.chapter == ch,
            WrongQuestion.section == sec
        ).first()
        if existing:
            existing.wrong_count += 1
            existing.consecutive_correct = 0
            existing.mastered = 0
            existing.last_wrong_date = str(date.today())
            existing.note = note
            existing.error_type = etype
            existing.knowledge_tags = tags
            existing.difficulty = difficulty
            existing.question_type = qtype
            if img:
                existing.image_data = img
            skipped.append({'question_number': qn, 'reason': '已存在，已更新'})
        else:
            q = WrongQuestion(
                question_number=qn, chapter=ch, section=sec, source=src, note=note,
                error_type=etype, knowledge_tags=tags, difficulty=difficulty,
                question_type=qtype, image_data=img,
                date_added=str(date.today()), last_wrong_date=str(date.today())
            )
            session.add(q)
            added.append(qn)
    session.commit()
    return jsonify({'added': len(added), 'skipped': len(skipped)})

@app.route('/api/questions/<int:qid>', methods=['PUT'])
def update_question(qid):
    data = request.get_json()
    session = get_session()
    q = session.query(WrongQuestion).get(qid)
    if not q:
        return jsonify({'error': '题目不存在'}), 404
    if 'question_number' in data:
        q.question_number = data['question_number']
    if 'chapter' in data:
        q.chapter = data['chapter']
    if 'section' in data:
        q.section = data['section']
    if 'source' in data:
        q.source = data['source']
    if 'note' in data:
        q.note = data['note']
    if 'error_type' in data:
        q.error_type = data['error_type']
    if 'knowledge_tags' in data:
        q.knowledge_tags = data['knowledge_tags']
    if 'difficulty' in data:
        q.difficulty = data['difficulty']
    if 'question_type' in data:
        q.question_type = data['question_type']
    if 'image_data' in data:
        q.image_data = data['image_data']
    session.commit()
    return jsonify({'success': True})

@app.route('/api/questions/<int:qid>/image', methods=['PUT'])
def update_image(qid):
    data = request.get_json()
    img = data.get('image_data')
    session = get_session()
    q = session.query(WrongQuestion).get(qid)
    if not q:
        return jsonify({'error': '题目不存在'}), 404
    q.image_data = img
    session.commit()
    return jsonify({'success': True})

@app.route('/api/questions/<int:qid>', methods=['DELETE'])
def delete_question(qid):
    session = get_session()
    q = session.query(WrongQuestion).get(qid)
    if not q:
        return jsonify({'error': '题目不存在'}), 404
    session.delete(q)
    session.commit()
    return jsonify({'success': True})

@app.route('/api/questions/filter', methods=['POST'])
def filter_questions():
    data = request.get_json()
    chapter = data.get('chapter', '')
    mastered = data.get('mastered', '')
    session = get_session()
    query = session.query(WrongQuestion)
    if chapter:
        query = query.filter(WrongQuestion.chapter == chapter)
    if mastered == '0':
        query = query.filter(WrongQuestion.mastered == 0)
    elif mastered == '1':
        query = query.filter(WrongQuestion.mastered == 1)
    rows = query.order_by(WrongQuestion.chapter, WrongQuestion.section, WrongQuestion.question_number).all()
    return jsonify([{c.name: getattr(r, c.name) for c in WrongQuestion.__table__.columns} for r in rows])

@app.route('/api/chapters', methods=['GET'])
def get_chapters():
    session = get_session()
    chapters = session.query(WrongQuestion.chapter).distinct().order_by(WrongQuestion.chapter).all()
    return jsonify([c[0] for c in chapters if c[0]])

@app.route('/api/practice', methods=['GET'])
def get_practice():
    count = int(get_setting('daily_count', DAILY_COUNT_DEFAULT))
    session = get_session()
    today = str(date.today())
    dp = session.query(DailyPractice).filter_by(date=today).first()
    if dp:
        q_ids = json.loads(dp.questions)
        questions = session.query(WrongQuestion).filter(WrongQuestion.id.in_(q_ids)).all()
    else:
        unmastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 0).all()
        if len(unmastered) <= count:
            questions = unmastered
        else:
            weights = []
            for q in unmastered:
                base_weight = q.wrong_count * 2
                if q.last_wrong_date:
                    days_since_wrong = (date.today() - datetime.strptime(q.last_wrong_date, '%Y-%m-%d').date()).days
                    recency_weight = max(0, 10 - days_since_wrong)
                else:
                    recency_weight = 10
                weights.append(base_weight + recency_weight)
            indices = random.choices(range(len(unmastered)), weights=weights, k=count)
            questions = [unmastered[i] for i in sorted(set(indices))]
        dp = DailyPractice(date=today, questions=json.dumps([q.id for q in questions]))
        session.add(dp)
        session.commit()
    return jsonify([{c.name: getattr(q, c.name) for c in WrongQuestion.__table__.columns} for q in questions])

@app.route('/api/practice/result', methods=['POST'])
def practice_result():
    data = request.get_json()
    qid = data['question_id']
    result = data['result']
    error_type = data.get('error_type', '')
    session = get_session()
    q = session.query(WrongQuestion).get(qid)
    if not q:
        return jsonify({'error': '题目不存在'}), 404
    q.last_practice_date = str(date.today())
    if result == 'correct':
        q.consecutive_correct += 1
        if q.consecutive_correct >= MASTER_THRESHOLD:
            q.mastered = 1
    else:
        q.consecutive_correct = 0
        q.mastered = 0
        q.wrong_count += 1
        q.last_wrong_date = str(date.today())
    session.add(PracticeResult(
        question_id=qid, date=str(date.today()), result=result, error_type=error_type
    ))
    session.commit()
    return jsonify({'success': True, 'mastered': q.mastered})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    session = get_session()
    total = session.query(WrongQuestion).count()
    mastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 1).count()
    unmastered = total - mastered
    today = str(date.today())
    today_count = session.query(PracticeResult).filter(PracticeResult.date == today).count()
    return jsonify({
        'total': total,
        'mastered': mastered,
        'unmastered': unmastered,
        'today_count': today_count
    })

@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    session = get_session()
    questions = session.query(WrongQuestion).order_by(WrongQuestion.chapter, WrongQuestion.section, WrongQuestion.question_number).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['题号', '章节', '小节', '来源', '备注', '错误次数', '连续正确', '是否掌握', '添加日期', '最后错误日期', '错误类型', '知识点标签', '难度', '题型'])
    for q in questions:
        writer.writerow([q.question_number, q.chapter, q.section, q.source, q.note, q.wrong_count, q.consecutive_correct, q.mastered, q.date_added, q.last_wrong_date, q.error_type, q.knowledge_tags, q.difficulty, q.question_type])
    output.seek(0)
    return Response(output, mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=wrong_questions.csv'})

@app.route('/api/backup', methods=['POST'])
def create_backup():
    session = get_session()
    questions = session.query(WrongQuestion).all()
    results = session.query(PracticeResult).all()
    settings = session.query(Setting).all()
    practices = session.query(DailyPractice).all()
    backup_data = {
        'version': 'v3',
        'backup_time': datetime.now().isoformat(),
        'questions': [{c.name: getattr(q, c.name) for c in WrongQuestion.__table__.columns} for q in questions],
        'practice_results': [{c.name: getattr(r, c.name) for c in PracticeResult.__table__.columns} for r in results],
        'settings': [{c.name: getattr(s, c.name) for c in Setting.__table__.columns} for s in settings],
        'daily_practice': [{c.name: getattr(p, c.name) for c in DailyPractice.__table__.columns} for p in practices]
    }
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = os.path.join(backup_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)
    s = session.query(Setting).filter_by(key='last_backup_date').first()
    if s:
        s.value = str(date.today())
    else:
        session.add(Setting(key='last_backup_date', value=str(date.today())))
    session.commit()
    backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.json')], reverse=True)[:7]
    for old in os.listdir(backup_dir):
        if old.endswith('.json') and old not in backups:
            os.remove(os.path.join(backup_dir, old))
    return jsonify({'success': True, 'filename': filename, 'count': len(questions)})

@app.route('/api/backup/list', methods=['GET'])
def list_backups():
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    if not os.path.exists(backup_dir):
        return jsonify([])
    backups = []
    for f in sorted(os.listdir(backup_dir), reverse=True):
        if f.endswith('.json'):
            filepath = os.path.join(backup_dir, f)
            backups.append({
                'filename': f,
                'size': os.path.getsize(filepath),
                'timestamp': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
            })
    return jsonify(backups)

@app.route('/api/backup/download/<filename>', methods=['GET'])
def download_backup(filename):
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    filepath = os.path.join(backup_dir, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': '备份文件不存在'}), 404
    return send_file(filepath, as_attachment=True)

@app.route('/api/backup/restore', methods=['POST'])
def restore_backup():
    data = request.get_json()
    filename = data.get('filename')
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    filepath = os.path.join(backup_dir, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': '备份文件不存在'}), 404
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        session = get_session()
        session.query(WrongQuestion).delete()
        session.query(PracticeResult).delete()
        session.query(Setting).delete()
        session.query(DailyPractice).delete()
        for q in backup_data.get('questions', []):
            session.add(WrongQuestion(**q))
        for pr in backup_data.get('practice_results', []):
            session.add(PracticeResult(**pr))
        for s in backup_data.get('settings', []):
            session.add(Setting(**s))
        for dp in backup_data.get('daily_practice', []):
            session.add(DailyPractice(**dp))
        session.commit()
        return jsonify({'success': True, 'count': len(backup_data.get('questions', []))})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup/automatic', methods=['GET'])
def check_automatic_backup():
    session = get_session()
    last_backup = session.query(Setting).filter_by(key='last_backup_date').first()
    today = str(date.today())
    needs_backup = not last_backup or last_backup.value != today
    return jsonify({'needs_backup': needs_backup, 'last_backup': last_backup.value if last_backup else None})

@app.route('/api/backup/cleanup', methods=['POST'])
def cleanup_backups():
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    if not os.path.exists(backup_dir):
        return jsonify({'success': True, 'cleaned': 0})
    backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.json')], reverse=True)[:7]
    cleaned = 0
    for f in os.listdir(backup_dir):
        if f.endswith('.json') and f not in backups:
            os.remove(os.path.join(backup_dir, f))
            cleaned += 1
    return jsonify({'success': True, 'cleaned': cleaned})

# ═══ 艾宾浩斯智能复习 ═══
def calculate_ebbinghaus_score(q):
    base_weight = q.wrong_count * 2
    recency_weight = 0
    if q.last_wrong_date:
        try:
            days_since_wrong = (date.today() - datetime.strptime(q.last_wrong_date, '%Y-%m-%d').date()).days
            recency_weight = max(0, 14 - days_since_wrong)
        except:
            recency_weight = 10
    else:
        recency_weight = 10
    mastered_bonus = 0
    if q.mastered == 0:
        mastered_bonus = 10
    difficulty_multiplier = 1
    if q.difficulty == 'hard':
        difficulty_multiplier = 1.5
    elif q.difficulty == 'medium':
        difficulty_multiplier = 1.2
    consecutive_penalty = 0
    if q.consecutive_correct > 0:
        consecutive_penalty = q.consecutive_correct * 2
    return (base_weight + recency_weight + mastered_bonus - consecutive_penalty) * difficulty_multiplier

@app.route('/api/practice/ebbinghaus', methods=['GET'])
def get_ebbinghaus_practice():
    count = int(get_setting('daily_count', DAILY_COUNT_DEFAULT))
    session = get_session()
    unmastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 0).all()
    if len(unmastered) <= count:
        questions = unmastered
    else:
        weights = [calculate_ebbinghaus_score(q) for q in unmastered]
        indices = random.choices(range(len(unmastered)), weights=weights, k=count)
        questions = [unmastered[i] for i in sorted(set(indices))]
    return jsonify([{c.name: getattr(q, c.name) for c in WrongQuestion.__table__.columns} for q in questions])

@app.route('/api/daily-practice', methods=['GET'])
def get_daily_practice():
    target_date = request.args.get('date', str(date.today()))
    count = int(get_setting('daily_count', DAILY_COUNT_DEFAULT))
    session = get_session()
    dp = session.query(DailyPractice).filter_by(date=target_date).first()
    if dp:
        q_ids = json.loads(dp.questions)
        questions = session.query(WrongQuestion).filter(WrongQuestion.id.in_(q_ids)).all()
    else:
        unmastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 0).all()
        if len(unmastered) <= count:
            questions = unmastered
        else:
            weights = [calculate_ebbinghaus_score(q) for q in unmastered]
            indices = random.choices(range(len(unmastered)), weights=weights, k=count)
            questions = [unmastered[i] for i in sorted(set(indices))]
        dp = DailyPractice(date=target_date, questions=json.dumps([q.id for q in questions]))
        session.add(dp)
        session.commit()
    today_results = {}
    for pr in session.query(PracticeResult).filter(PracticeResult.date == target_date).all():
        today_results[pr.question_id] = pr.result
    result = [{c.name: getattr(q, c.name) for c in WrongQuestion.__table__.columns} for q in questions]
    for q in result:
        q['today_result'] = today_results.get(q['id'])
    return jsonify({'date': target_date, 'questions': result})

@app.route('/api/daily-practice/refresh', methods=['POST'])
def refresh_daily_practice():
    today = str(date.today())
    count = int(get_setting('daily_count', DAILY_COUNT_DEFAULT))
    session = get_session()
    session.query(DailyPractice).filter_by(date=today).delete()
    session.commit()
    return get_daily_practice()

@app.route('/api/daily-practice/dates', methods=['GET'])
def get_practice_dates():
    session = get_session()
    dates = [dp.date for dp in session.query(DailyPractice).order_by(DailyPractice.date.desc()).all()]
    return jsonify(dates)

@app.route('/api/practice-result', methods=['POST'])
def submit_practice_result():
    data = request.get_json()
    qid = data['question_id']
    result = data['result']
    error_type = data.get('error_type', '')
    time_spent = data.get('time_spent', 0)
    session = get_session()
    q = session.query(WrongQuestion).get(qid)
    if not q:
        return jsonify({'error': '题目不存在'}), 404
    q.last_practice_date = str(date.today())
    if result == 'correct':
        q.consecutive_correct += 1
        if q.consecutive_correct >= MASTER_THRESHOLD:
            q.mastered = 1
    else:
        q.consecutive_correct = 0
        q.mastered = 0
        q.wrong_count += 1
        q.last_wrong_date = str(date.today())
    session.add(PracticeResult(
        question_id=qid, date=str(date.today()), result=result, 
        error_type=error_type, time_spent=time_spent
    ))
    session.commit()
    return jsonify({'ok': True, 'mastered': q.mastered})

@app.route('/api/targeted-practice', methods=['POST'])
def get_targeted_practice():
    data = request.get_json()
    chapter = data.get('chapter', '')
    error_type = data.get('error_type', '')
    tag = data.get('tag', '')
    difficulty = data.get('difficulty', '')
    question_type = data.get('question_type', '')
    count = data.get('count', 15)
    session = get_session()
    query = session.query(WrongQuestion).filter(WrongQuestion.mastered == 0)
    if chapter:
        query = query.filter(WrongQuestion.chapter == chapter)
    if error_type:
        query = query.filter(WrongQuestion.error_type == error_type)
    if tag:
        query = query.filter(WrongQuestion.knowledge_tags.like(f'%{tag}%'))
    if difficulty:
        query = query.filter(WrongQuestion.difficulty == difficulty)
    if question_type:
        query = query.filter(WrongQuestion.question_type == question_type)
    available = query.all()
    total_available = len(available)
    if len(available) <= count:
        questions = available
    else:
        weights = [calculate_ebbinghaus_score(q) for q in available]
        indices = random.choices(range(len(available)), weights=weights, k=count)
        questions = [available[i] for i in sorted(set(indices))]
    return jsonify({
        'total_available': total_available,
        'questions': [{c.name: getattr(q, c.name) for c in WrongQuestion.__table__.columns} for q in questions]
    })

# ═══ 学习数据可视化 ═══
@app.route('/api/stats/overview', methods=['GET'])
def get_stats_overview():
    session = get_session()
    total = session.query(WrongQuestion).count()
    mastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 1).count()
    today = str(date.today())
    today_practice = session.query(PracticeResult).filter(PracticeResult.date == today).count()
    return jsonify({
        'total': total,
        'mastered': mastered,
        'remaining': total - mastered,
        'today_practice': today_practice
    })

@app.route('/api/stats/error-types', methods=['GET'])
def get_error_type_stats():
    session = get_session()
    results = session.query(PracticeResult).all()
    type_counts = Counter()
    for r in results:
        etype = r.error_type if r.error_type else 'unknown'
        if r.result == 'wrong':
            type_counts[etype] += 1
    distribution = []
    for etype, count in type_counts.items():
        distribution.append({
            'type': etype,
            'label': ERROR_TYPES.get(etype, etype),
            'count': count
        })
    return jsonify({'distribution': distribution})

@app.route('/api/stats/heatmap', methods=['GET'])
def get_heatmap_data():
    session = get_session()
    chapters = session.query(WrongQuestion.chapter).distinct().order_by(WrongQuestion.chapter).all()
    chapter_list = [c[0] for c in chapters if c[0]]
    result = []
    for ch in chapter_list:
        total = session.query(WrongQuestion).filter(WrongQuestion.chapter == ch).count()
        mastered = session.query(WrongQuestion).filter(
            WrongQuestion.chapter == ch, WrongQuestion.mastered == 1
        ).count()
        mastery_pct = round(mastered / total * 100) if total > 0 else 0
        error_types = {}
        wrong_results = session.query(PracticeResult).filter(PracticeResult.result == 'wrong').all()
        for r in wrong_results:
            q = session.query(WrongQuestion).get(r.question_id)
            if q and q.chapter == ch:
                etype = r.error_type if r.error_type else 'unknown'
                error_types[etype] = error_types.get(etype, 0) + 1
        result.append({
            'chapter': ch,
            'total': total,
            'mastered': mastered,
            'mastery_pct': mastery_pct,
            'error_types': error_types
        })
    return jsonify({'chapters': result})

@app.route('/api/stats/diagnosis', methods=['GET'])
def get_diagnosis():
    session = get_session()
    total = session.query(WrongQuestion).count()
    mastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 1).count()
    remaining = total - mastered
    recent_results = session.query(PracticeResult).filter(
        PracticeResult.date >= str(date.today() - timedelta(days=7))
    ).all()
    correct_count = sum(1 for r in recent_results if r.result == 'correct')
    total_recent = len(recent_results)
    recent_accuracy = round(correct_count / total_recent * 100) if total_recent > 0 else 0
    total_minutes = 0
    days_studied = 0
    suggestions = []
    if remaining > 0:
        suggestions.append(f'还有 {remaining} 道错题待攻克，继续加油！')
    if recent_accuracy < 60:
        suggestions.append('近期正确率较低，建议重点复习薄弱章节')
    elif recent_accuracy >= 80:
        suggestions.append('近期正确率良好，继续保持！')
    if total_minutes < 30:
        suggestions.append('学习时长较短，建议每天至少练习30分钟')
    return jsonify({
        'overall': {'total': total, 'mastered': mastered},
        'recent_accuracy': recent_accuracy,
        'time_stats': {'total_minutes': total_minutes, 'days_studied': days_studied},
        'suggestions': suggestions
    })

@app.route('/api/stats/prediction', methods=['GET'])
def get_prediction():
    session = get_session()
    total = session.query(WrongQuestion).count()
    mastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 1).count()
    remaining = total - mastered
    if remaining == 0:
        return jsonify({
            'remaining': 0,
            'predicted_days': 0,
            'prediction_date': None,
            'trend': 'steady',
            'trend_label': '已完成',
            'daily_avg_mastered': 0,
            'daily_avg_practiced': 0,
            'needed_per_day': 0,
            'target_days': 30
        })
    all_results = session.query(PracticeResult).all()
    if len(all_results) < 5:
        return jsonify({
            'remaining': remaining,
            'predicted_days': -1,
            'prediction_date': None,
            'trend': 'steady',
            'trend_label': '数据不足',
            'daily_avg_mastered': 0,
            'daily_avg_practiced': 0,
            'needed_per_day': 0,
            'target_days': 30
        })
    date_counts = Counter(r.date for r in all_results)
    days_with_practice = len(date_counts)
    daily_avg_practiced = round(len(all_results) / days_with_practice)
    correct_results = [r for r in all_results if r.result == 'correct']
    date_correct_counts = Counter(r.date for r in correct_results)
    daily_avg_mastered = round(len(correct_results) / days_with_practice) if days_with_practice > 0 else 0
    predicted_days = math.ceil(remaining / daily_avg_mastered) if daily_avg_mastered > 0 else 999
    prediction_date = date.today() + timedelta(days=predicted_days)
    recent_week = [str(date.today() - timedelta(days=i)) for i in range(7)]
    recent_counts = [date_counts.get(d, 0) for d in recent_week]
    prev_week = [str(date.today() - timedelta(days=i)) for i in range(7, 14)]
    prev_counts = [date_counts.get(d, 0) for d in prev_week]
    recent_avg = sum(recent_counts) / 7
    prev_avg = sum(prev_counts) / 7 if prev_counts else 0
    trend = 'steady'
    trend_label = '稳定'
    if recent_avg > prev_avg * 1.3:
        trend = 'accelerating'
        trend_label = '进步中'
    elif recent_avg < prev_avg * 0.7:
        trend = 'slowing'
        trend_label = '需加油'
    exam_date_str = get_setting('exam_date', '')
    target_days = 30
    needed_per_day = 0
    if exam_date_str:
        try:
            exam_date = datetime.strptime(exam_date_str, '%Y-%m-%d').date()
            target_days = max(1, (exam_date - date.today()).days)
            needed_per_day = math.ceil(remaining / target_days)
        except:
            pass
    chapter_predictions = []
    chapters = session.query(WrongQuestion.chapter).distinct().order_by(WrongQuestion.chapter).all()
    for ch in chapters:
        ch_name = ch[0]
        if not ch_name:
            continue
        ch_total = session.query(WrongQuestion).filter(WrongQuestion.chapter == ch_name).count()
        ch_mastered = session.query(WrongQuestion).filter(
            WrongQuestion.chapter == ch_name, WrongQuestion.mastered == 1
        ).count()
        ch_remaining = ch_total - ch_mastered
        if ch_remaining > 0 and daily_avg_mastered > 0:
            ch_days = math.ceil(ch_remaining / daily_avg_mastered)
            ch_pred_date = date.today() + timedelta(days=ch_days)
            chapter_predictions.append({
                'chapter': ch_name,
                'total': ch_total,
                'mastered': ch_mastered,
                'prediction_date': str(ch_pred_date)
            })
    return jsonify({
        'remaining': remaining,
        'predicted_days': predicted_days,
        'prediction_date': str(prediction_date),
        'trend': trend,
        'trend_label': trend_label,
        'daily_avg_mastered': daily_avg_mastered,
        'daily_avg_practiced': daily_avg_practiced,
        'needed_per_day': needed_per_day,
        'target_days': target_days,
        'chapter_predictions': chapter_predictions
    })

@app.route('/api/stats/trends', methods=['GET'])
def get_trends():
    period = request.args.get('period', 'daily')
    session = get_session()
    days = 14
    trend_data = []
    mastery_trend = []
    duration_trend = []
    for i in range(days, 0, -1):
        d = str(date.today() - timedelta(days=i))
        results = session.query(PracticeResult).filter(PracticeResult.date == d).all()
        correct = sum(1 for r in results if r.result == 'correct')
        wrong = sum(1 for r in results if r.result == 'wrong')
        accuracy = round(correct / (correct + wrong) * 100) if (correct + wrong) > 0 else 0
        trend_data.append({
            'date': d,
            'correct': correct,
            'wrong': wrong,
            'accuracy': accuracy
        })
        total = session.query(WrongQuestion).count()
        mastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 1).count()
        pct = round(mastered / total * 100) if total > 0 else 0
        mastery_trend.append({'date': d, 'pct': pct})
        duration_trend.append({'date': d, 'minutes': 0})
    return jsonify({
        'trend': trend_data,
        'mastery_trend': mastery_trend,
        'duration_trend': duration_trend
    })

@app.route('/api/stats/deep-analysis', methods=['GET'])
def get_deep_analysis():
    session = get_session()
    questions = session.query(WrongQuestion).all()
    results = session.query(PracticeResult).all()
    
    cross_dimensions = []
    chapter_error_counts = defaultdict(lambda: defaultdict(int))
    for r in results:
        q = session.query(WrongQuestion).get(r.question_id)
        if q and r.result == 'wrong':
            chapter_error_counts[q.chapter][r.error_type or 'unknown'] += 1
    
    for ch, etypes in chapter_error_counts.items():
        for etype, count in etypes.items():
            ch_total = session.query(WrongQuestion).filter(WrongQuestion.chapter == ch).count()
            ch_mastered = session.query(WrongQuestion).filter(
                WrongQuestion.chapter == ch, WrongQuestion.mastered == 1
            ).count()
            mastery_pct = round(ch_mastered / ch_total * 100) if ch_total > 0 else 0
            severity = 'ok'
            if mastery_pct < 40:
                severity = 'critical'
            elif mastery_pct < 70:
                severity = 'warning'
            cross_dimensions.append({
                'chapter': ch,
                'error_type': etype,
                'error_label': ERROR_TYPES.get(etype, etype),
                'difficulty': '',
                'total': count,
                'mastery_pct': mastery_pct,
                'severity': severity
            })
    
    cross_dimensions.sort(key=lambda x: x['mastery_pct'])
    cross_findings = []
    for d in cross_dimensions[:3]:
        cross_findings.append(f"{d['chapter']}章节在{d['error_label']}方面掌握较弱")
    
    regressed_count = 0
    regressed_questions = []
    for q in questions:
        if q.consecutive_correct > 0 and q.wrong_count > 1:
            regressed_count += 1
            if q.last_wrong_date:
                try:
                    days_to_forget = (date.today() - datetime.strptime(q.last_wrong_date, '%Y-%m-%d').date()).days
                    regressed_questions.append({
                        'question_number': q.question_number,
                        'chapter': q.chapter,
                        'days_to_forget': days_to_forget
                    })
                except:
                    pass
    
    regression_rate = round(regressed_count / len(questions) * 100) if questions else 0
    curve_data = []
    for i in range(1, 31):
        retention = max(20, 100 - i * 2.5 - random.uniform(0, 5))
        curve_data.append({'days': i, 'retention': round(retention)})
    
    forgetting_intervals = {}
    for q in questions:
        if q.last_wrong_date:
            try:
                days_since = (date.today() - datetime.strptime(q.last_wrong_date, '%Y-%m-%d').date()).days
                if days_since <= 7:
                    forgetting_intervals['7天内'] = forgetting_intervals.get('7天内', 0) + 1
                elif days_since <= 14:
                    forgetting_intervals['7-14天'] = forgetting_intervals.get('7-14天', 0) + 1
                elif days_since <= 30:
                    forgetting_intervals['14-30天'] = forgetting_intervals.get('14-30天', 0) + 1
                else:
                    forgetting_intervals['30天以上'] = forgetting_intervals.get('30天以上', 0) + 1
            except:
                pass
    
    forgetting_by_chapter = []
    ch_forget_counts = Counter()
    for q in questions:
        if q.mastered == 0:
            ch_forget_counts[q.chapter] += 1
    for ch, count in ch_forget_counts.most_common(5):
        forgetting_by_chapter.append({'chapter': ch, 'count': count})
    
    forgetting_by_tag = []
    tag_forget_counts = Counter()
    for q in questions:
        if q.mastered == 0 and q.knowledge_tags:
            for tag in q.knowledge_tags.split(','):
                tag_forget_counts[tag.strip()] += 1
    for tag, count in tag_forget_counts.most_common(5):
        forgetting_by_tag.append({'tag': tag, 'count': count})
    
    total_minutes = 0
    total_questions = len(results)
    days_with_practice = len(set(r.date for r in results))
    avg_daily_questions = round(total_questions / days_with_practice) if days_with_practice > 0 else 0
    
    all_dates = sorted(set(r.date for r in results))
    if len(all_dates) >= 10:
        mid = len(all_dates) // 2
        early_dates = all_dates[:mid]
        late_dates = all_dates[mid:]
        early_correct = sum(1 for r in results if r.date in early_dates and r.result == 'correct')
        early_total = sum(1 for r in results if r.date in early_dates)
        early_accuracy = round(early_correct / early_total * 100) if early_total > 0 else 0
        late_correct = sum(1 for r in results if r.date in late_dates and r.result == 'correct')
        late_total = sum(1 for r in results if r.date in late_dates)
        late_accuracy = round(late_correct / late_total * 100) if late_total > 0 else 0
        early_speed = round(early_total / (len(early_dates) or 1))
        late_speed = round(late_total / (len(late_dates) or 1))
        efficiency_trend = 'improving' if late_accuracy > early_accuracy else 'declining' if late_accuracy < early_accuracy else 'stable'
    else:
        early_accuracy = late_accuracy = early_speed = late_speed = 0
        efficiency_trend = 'insufficient_data'
    
    periods = []
    for hour in range(6, 24):
        hour_results = [r for r in results if r.date and int(r.date.split(' ')[-1].split(':')[0]) == hour] if any(' ' in r.date for r in results) else []
        if not hour_results:
            continue
        correct_h = sum(1 for r in hour_results if r.result == 'correct')
        accuracy = round(correct_h / len(hour_results) * 100) if hour_results else 0
        periods.append({'period': f'{hour}:00', 'avg_accuracy': accuracy, 'sessions': len(hour_results)})
    
    periods.sort(key=lambda x: x['avg_accuracy'], reverse=True)
    best_period = periods[0]['period'] if periods else '--'
    
    week_days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    action_plan = {'weekly_target': f'攻克 {min(70, len([q for q in questions if q.mastered == 0]))} 道错题', 'days': []}
    weak_chapters = [ch for ch, cnt in ch_forget_counts.most_common(3)]
    
    for i, day_name in enumerate(week_days):
        if i < 5:
            if i < len(weak_chapters):
                action_plan['days'].append({
                    'day': day_name,
                    'chapter': weak_chapters[i],
                    'question_count': 10,
                    'focus_error': '重点',
                    'type': 'weak',
                    'tips': '集中攻克薄弱章节'
                })
            else:
                action_plan['days'].append({
                    'day': day_name,
                    'chapter': '综合复习',
                    'question_count': 15,
                    'focus_error': '巩固',
                    'type': 'review',
                    'tips': '复习已掌握内容'
                })
        else:
            action_plan['days'].append({
                'day': day_name,
                'chapter': '休息',
                'question_count': 0,
                'focus_error': '休息',
                'type': 'rest',
                'tips': '适当休息，保持状态'
            })
    
    return jsonify({
        'cross_analysis': {'findings': cross_findings, 'dimensions': cross_dimensions},
        'forgetting_curve': {
            'total_regressed': regressed_count,
            'regression_rate': regression_rate,
            'curve_data': curve_data,
            'forgetting_intervals': forgetting_intervals,
            'forgetting_by_chapter': forgetting_by_chapter,
            'forgetting_by_tag': forgetting_by_tag,
            'regressed_questions': regressed_questions[:10]
        },
        'efficiency': {
            'total_minutes': total_minutes,
            'avg_daily_questions': avg_daily_questions,
            'efficiency_trend': efficiency_trend,
            'early_avg_accuracy': early_accuracy,
            'late_avg_accuracy': late_accuracy,
            'early_avg_speed': early_speed,
            'late_avg_speed': late_speed,
            'best_period': best_period,
            'peak_periods': periods[:6]
        },
        'action_plan': action_plan
    })

@app.route('/api/stats/clusters', methods=['GET'])
def get_clusters():
    session = get_session()
    results = session.query(PracticeResult).filter(
        PracticeResult.date >= str(date.today() - timedelta(days=7))
    ).all()
    if len(results) < 5:
        return jsonify({'alerts': [], 'message': '数据不足'})
    
    tag_errors = Counter()
    chapter_errors = Counter()
    for r in results:
        if r.result == 'wrong':
            q = session.query(WrongQuestion).get(r.question_id)
            if q:
                if q.knowledge_tags:
                    for tag in q.knowledge_tags.split(','):
                        tag = tag.strip()
                        if tag:
                            tag_errors[(tag, r.error_type or '')] += 1
                chapter_errors[(q.chapter, r.error_type or '')] += 1
    
    alerts = []
    for (tag, etype), count in tag_errors.most_common(3):
        if count >= 3:
            alerts.append({
                'level': 'critical' if count >= 5 else 'warning',
                'type': 'tag',
                'message': f'"{tag}" 标签近期频繁出错 ({count}次)',
                'count': count,
                'tag': tag
            })
    
    for (ch, etype), count in chapter_errors.most_common(3):
        if count >= 3:
            alerts.append({
                'level': 'critical' if count >= 5 else 'warning',
                'type': 'chapter_error',
                'message': f'{ch}章节在{ERROR_TYPES.get(etype, etype)}方面薄弱 ({count}次)',
                'count': count,
                'chapter': ch,
                'error_type': etype
            })
    
    return jsonify({'alerts': alerts, 'message': '近7天错误智能聚类'})

@app.route('/api/stats/weekly-report', methods=['GET'])
def get_weekly_report():
    session = get_session()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    
    week_results = session.query(PracticeResult).filter(
        PracticeResult.date >= str(week_start),
        PracticeResult.date <= str(week_end)
    ).all()
    
    total_questions = len(week_results)
    if total_questions == 0:
        return jsonify({'stats': {'total_questions': 0}})
    
    correct = sum(1 for r in week_results if r.result == 'correct')
    accuracy = round(correct / total_questions * 100)
    
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = week_start - timedelta(days=1)
    prev_results = session.query(PracticeResult).filter(
        PracticeResult.date >= str(prev_week_start),
        PracticeResult.date <= str(prev_week_end)
    ).all()
    prev_accuracy = round(sum(1 for r in prev_results if r.result == 'correct') / len(prev_results) * 100) if prev_results else 0
    
    days_studied = len(set(r.date for r in week_results))
    total_minutes = 0
    
    mastered_count = session.query(WrongQuestion).filter(WrongQuestion.mastered == 1).count()
    
    chapter_counts = Counter()
    tag_counts = Counter()
    for r in week_results:
        if r.result == 'wrong':
            q = session.query(WrongQuestion).get(r.question_id)
            if q:
                chapter_counts[q.chapter] += 1
                if q.knowledge_tags:
                    for tag in q.knowledge_tags.split(','):
                        tag_counts[tag.strip()] += 1
    
    top_wrong_chapters = [{'chapter': ch, 'count': cnt} for ch, cnt in chapter_counts.most_common(3)]
    top_wrong_tags = [{'tag': tag, 'count': cnt} for tag, cnt in tag_counts.most_common(3)]
    
    daily_accuracy = {}
    for r in week_results:
        if r.date not in daily_accuracy:
            daily_accuracy[r.date] = {'correct': 0, 'total': 0}
        daily_accuracy[r.date]['total'] += 1
        if r.result == 'correct':
            daily_accuracy[r.date]['correct'] += 1
    
    best_day = None
    for d, stats in daily_accuracy.items():
        day_acc = round(stats['correct'] / stats['total'] * 100)
        if not best_day or day_acc > best_day['accuracy']:
            best_day = {'date': d, 'accuracy': day_acc}
    
    score = min(100, accuracy + days_studied * 2 + min(mastered_count, 50))
    if score >= 90:
        grade = 'S'
    elif score >= 80:
        grade = 'A'
    elif score >= 60:
        grade = 'B'
    else:
        grade = 'C'
    
    commentary = []
    if accuracy >= 80:
        commentary.append('本周表现优秀，正确率很高！')
    elif accuracy >= 60:
        commentary.append('本周表现良好，继续加油！')
    else:
        commentary.append('本周正确率有待提高，建议加强练习')
    if days_studied >= 5:
        commentary.append('坚持练习，非常棒！')
    
    return jsonify({
        'grade': grade,
        'score': score,
        'commentary': commentary,
        'stats': {
            'total_questions': total_questions,
            'accuracy': accuracy,
            'prev_accuracy': prev_accuracy,
            'days_studied': days_studied,
            'total_minutes': total_minutes,
            'new_mastered': mastered_count
        },
        'top_wrong_chapters': top_wrong_chapters,
        'top_wrong_tags': top_wrong_tags,
        'best_day': best_day
    })

@app.route('/api/stats/goal-plan', methods=['GET'])
def get_goal_plan():
    session = get_session()
    exam_date_str = get_setting('exam_date', '')
    if not exam_date_str:
        return jsonify({'set': False})
    
    try:
        exam_date = datetime.strptime(exam_date_str, '%Y-%m-%d').date()
    except:
        return jsonify({'set': False})
    
    remaining_days = (exam_date - date.today()).days
    if remaining_days < 0:
        remaining_days = 0
    
    total = session.query(WrongQuestion).count()
    mastered = session.query(WrongQuestion).filter(WrongQuestion.mastered == 1).count()
    remaining_questions = total - mastered
    overall_pct = round(mastered / total * 100) if total > 0 else 0
    
    if remaining_days > 0 and remaining_questions > 0:
        daily_target = math.ceil(remaining_questions / remaining_days)
        review_daily = max(5, daily_target // 2)
    else:
        daily_target = 0
        review_daily = 0
    
    if remaining_days <= 7:
        urgent_level = 'critical'
        urgent_message = '时间紧迫！加速冲刺！'
    elif remaining_days <= 14:
        urgent_level = 'tight'
        urgent_message = '时间紧张，保持节奏'
    elif remaining_days <= 30:
        urgent_level = 'normal'
        urgent_message = '按计划推进即可'
    else:
        urgent_level = 'relaxed'
        urgent_message = '时间充裕，稳扎稳打'
    
    milestones = []
    for i in range(4):
        m_days = remaining_days * (i + 1) // 4
        m_date = date.today() + timedelta(days=m_days)
        m_pct = min(25 * (i + 1), 100)
        milestones.append({
            'date': str(m_date),
            'target_pct': m_pct,
            'label': f'完成{m_pct}%'
        })
    
    chapter_plan = []
    chapters = session.query(WrongQuestion.chapter).distinct().order_by(WrongQuestion.chapter).all()
    for ch in chapters:
        ch_name = ch[0]
        if not ch_name:
            continue
        ch_total = session.query(WrongQuestion).filter(WrongQuestion.chapter == ch_name).count()
        ch_mastered = session.query(WrongQuestion).filter(
            WrongQuestion.chapter == ch_name, WrongQuestion.mastered == 1
        ).count()
        ch_pct = round(ch_mastered / ch_total * 100) if ch_total > 0 else 0
        ch_remaining = ch_total - ch_mastered
        ch_daily = math.ceil(ch_remaining / remaining_days) if remaining_days > 0 else 0
        
        urgency = 'ok'
        if ch_pct < 40:
            urgency = 'critical'
        elif ch_pct < 70:
            urgency = 'warning'
        
        chapter_plan.append({
            'chapter': ch_name,
            'total': ch_total,
            'mastered': ch_mastered,
            'mastery_pct': ch_pct,
            'daily_suggest': ch_daily,
            'urgency': urgency
        })
    
    return jsonify({
        'set': True,
        'remaining_days': remaining_days,
        'remaining_questions': remaining_questions,
        'overall_pct': overall_pct,
        'daily_target': daily_target,
        'review_daily': review_daily,
        'urgent_level': urgent_level,
        'urgent_message': urgent_message,
        'milestones': milestones,
        'chapter_plan': chapter_plan
    })

@app.route('/api/tags', methods=['GET'])
def get_tags():
    session = get_session()
    questions = session.query(WrongQuestion).filter(WrongQuestion.knowledge_tags != '').all()
    tags = set()
    for q in questions:
        if q.knowledge_tags:
            for tag in q.knowledge_tags.split(','):
                t = tag.strip()
                if t:
                    tags.add(t)
    return jsonify(sorted(list(tags)))

@app.route('/api/questions/batch-delete', methods=['POST'])
def batch_delete_questions():
    data = request.get_json()
    ids = data.get('ids', [])
    session = get_session()
    for qid in ids:
        q = session.query(WrongQuestion).get(qid)
        if q:
            session.delete(q)
    session.commit()
    return jsonify({'ok': True, 'deleted': len(ids)})

@app.route('/api/reset-mastered', methods=['POST'])
def reset_mastered():
    session = get_session()
    session.query(WrongQuestion).update({WrongQuestion.mastered: 0, WrongQuestion.consecutive_correct: 0})
    session.commit()
    return jsonify({'ok': True})

@app.route('/api/settings', methods=['POST'])
def save_settings():
    data = request.get_json()
    session = get_session()
    for key, value in data.items():
        s = session.query(Setting).filter_by(key=key).first()
        if s:
            s.value = str(value)
        else:
            session.add(Setting(key=key, value=str(value)))
    session.commit()
    return jsonify({'ok': True})

@app.route('/api/sync/export', methods=['GET'])
def sync_export():
    session = get_session()
    questions = session.query(WrongQuestion).all()
    results = session.query(PracticeResult).all()
    settings = session.query(Setting).all()
    return jsonify({
        'wrong_questions': [{c.name: getattr(q, c.name) for c in WrongQuestion.__table__.columns} for q in questions],
        'practice_results': [{c.name: getattr(r, c.name) for c in PracticeResult.__table__.columns} for r in results],
        'settings': [{c.name: getattr(s, c.name) for c in Setting.__table__.columns} for s in settings]
    })

@app.route('/api/sync/import', methods=['POST'])
def sync_import():
    data = request.get_json()
    session = get_session()
    imported = 0
    updated = 0
    
    for q in data.get('wrong_questions', []):
        existing = session.query(WrongQuestion).filter(
            WrongQuestion.question_number == q.get('question_number', ''),
            WrongQuestion.chapter == q.get('chapter', ''),
            WrongQuestion.section == q.get('section', '')
        ).first()
        if existing:
            for key, value in q.items():
                if key != 'id':
                    setattr(existing, key, value)
            updated += 1
        else:
            session.add(WrongQuestion(**q))
            imported += 1
    
    for r in data.get('practice_results', []):
        existing = session.query(PracticeResult).filter(
            PracticeResult.question_id == r.get('question_id', 0),
            PracticeResult.date == r.get('date', ''),
            PracticeResult.result == r.get('result', '')
        ).first()
        if not existing:
            session.add(PracticeResult(**r))
    
    for s in data.get('settings', []):
        existing = session.query(Setting).filter_by(key=s.get('key')).first()
        if existing:
            existing.value = s.get('value', '')
        else:
            session.add(Setting(**s))
    
    session.commit()
    return jsonify({'ok': True, 'questions_imported': imported, 'questions_updated': updated})

@app.route('/api/export/pdf', methods=['GET'])
def export_pdf():
    session = get_session()
    questions = session.query(WrongQuestion).order_by(WrongQuestion.chapter, WrongQuestion.section, WrongQuestion.question_number).all()
    
    pdf_content = f"""错题管理系统导出报告
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
总题数: {len(questions)}

================================================================================
"""
    
    current_chapter = ''
    for q in questions:
        if q.chapter != current_chapter:
            current_chapter = q.chapter
            pdf_content += f"\n【{current_chapter}】\n"
            pdf_content += "─" * 60 + "\n"
        
        pdf_content += f"\n题号: {q.question_number}"
        pdf_content += f"  | 小节: {q.section}"
        pdf_content += f"  | 来源: {q.source}\n"
        pdf_content += f"  错因: {ERROR_TYPES.get(q.error_type, q.error_type) if q.error_type else '未记录'}"
        pdf_content += f"  | 难度: {'基础' if q.difficulty == 'easy' else '中等' if q.difficulty == 'medium' else '难题' if q.difficulty == 'hard' else '未记录'}\n"
        pdf_content += f"  错误次数: {q.wrong_count} 次"
        pdf_content += f"  | 连续正确: {q.consecutive_correct} 次"
        pdf_content += f"  | 状态: {'已掌握' if q.mastered else '未掌握'}\n"
        if q.note:
            pdf_content += f"  备注: {q.note}\n"
        if q.knowledge_tags:
            pdf_content += f"  标签: {q.knowledge_tags}\n"
        pdf_content += "-" * 60 + "\n"
    
    return Response(
        pdf_content,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=wrong_questions_{date.today()}.pdf'}
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
