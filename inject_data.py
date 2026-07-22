"""将数据库备份注入离线 HTML"""
import json

# 1. 读备份数据
with open('错题库备份_2026-06-05.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

q_json = json.dumps(data['wrong_questions'], ensure_ascii=False)
r_json = json.dumps(data.get('practice_results', []), ensure_ascii=False)
d_json = json.dumps(data.get('daily_practice', []), ensure_ascii=False)

# 2. 读 HTML
with open('错题管理_离线版.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 3. 构造种子代码
seed_block = '''
// === 预置数据（从服务器导出） ===
const SEED_QUESTIONS=%s;
const SEED_RESULTS=%s;
const SEED_DAILY=%s;

async function seedData(){
    const sq=tx("wrong_questions","readwrite");
    const existing=await prom(sq.getAll());
    if(existing.length===0){
        for(const q of SEED_QUESTIONS)await prom(sq.put(q));
        const prs=tx("practice_results","readwrite");
        for(const r of SEED_RESULTS)await prom(prs.put(r));
        const dp=tx("daily_practice","readwrite");
        for(const d of SEED_DAILY)await prom(dp.put(d));
    }
}

''' % (q_json, r_json, d_json)

# 4. 找到注入点（init 函数之前）
marker = '(async function init(){'
pos = html.index(marker)
html = html[:pos] + seed_block + html[pos:]

# 5. 在 init 中调用 seedData
html = html.replace(
    'await openDB();\nawait refreshStats();',
    'await openDB();\nawait seedData();\nawait refreshStats();'
)

# 6. 写回
with open('错题管理_离线版.html', 'w', encoding='utf-8') as f:
    f.write(html)

print(f'完成！文件大小: {len(html)} 字符, 数据: {len(data["wrong_questions"])} 题')
