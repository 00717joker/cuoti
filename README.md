# 错题管理智能体

一个功能完整的考研数学错题管理系统，支持拍照录入、智能抽题、学习分析、目标倒推等功能。

## ✨ 功能特性

### 错题管理
- 📸 **拍照录入** - 手机拍照自动保存图片，支持OCR文字识别
- 🏷️ **知识点标签** - 随输随选，自动补全
- 📊 **难度/题型/错因** - 多维度分类
- 📂 **章节体系** - U1-U18 章节，支持1000A/B、660、880等来源

### 智能练习
- 🎯 **加权抽题** - 错误次数×新鲜度×陈旧度综合算法
- 🔄 **每日练习** - 自定义题量，支持重新抽题
- 📝 **专项突破** - 按章节/错因/标签/难度筛选
- ⏱️ **计时器** - 记录每次练习时长

### 学习分析
- 📈 **学习曲线预测** - 预计完成日期、章节攻克时间
- 🔍 **智能诊断** - 薄弱章节、错因分布、长期未练提醒
- 🧠 **深度分析** - 交叉诊断、遗忘曲线、效率画像、周计划
- 🎯 **目标倒推** - 设置考试日期，自动反推每日目标
- 📊 **学习周报** - S/A/B/C 四维评分
- ⚠️ **错误聚类告警** - 连续出错自动提醒

### 数据同步
- 💾 **JSON导入导出** - 完整数据备份
- 📱 **手机↔电脑同步** - 云端部署，多设备数据共享

## 🚀 快速开始

### 方式一：本地运行

```bash
# 安装依赖
pip install flask

# 启动
python app.py
```

然后访问 http://127.0.0.1:5000

### 方式二：云端部署（推荐）

#### 方案 A：PythonAnywhere（免费，数据持久）

1. 注册 https://www.pythonanywhere.com
2. 上传 `app.py`、`requirements.txt`、`templates/` 到 `mysite` 目录
3. 配置 WSGI：
   ```python
   import sys
   sys.path.insert(0, '/home/你的用户名/mysite')
   from app import app as application
   ```
4. 点击 Reload，访问 `https://你的用户名.pythonanywhere.com`

#### 方案 B：Render（一键部署）

1. Fork 本仓库到你的 GitHub
2. 打开 https://render.com
3. 点击 New → Web Service → Connect your repo
4. Render 会自动读取 `render.yaml` 配置，一键部署

部署完成后，你的网站地址为 `https://你的项目名.onrender.com`

## 📁 项目结构

```
cuoti/
├── app.py              # Flask 后端
├── requirements.txt    # Python 依赖
├── render.yaml         # Render 部署配置
├── .gitignore          # Git 忽略文件
├── templates/
│   └── index.html      # 前端页面
└── wrong_questions.db  # SQLite 数据库（自动创建）
```

## 🔧 技术栈

- **后端**: Flask 3.1.1
- **前端**: 原生 HTML/CSS/JavaScript
- **数据库**: SQLite
- **部署**: PythonAnywhere / Render / 本地运行

## 📱 使用说明

### 添加错题
1. 进入「添加」标签页
2. 填写题号、选择章节/小节
3. 点击图片区域拍照或上传
4. 添加知识点标签
5. 点击「添加」

### 每日练习
1. 进入「练习」标签页
2. 系统自动按权重抽题
3. 做完后标记「已掌握」或「再做错」
4. 选择错因类型

### 查看统计
1. 进入「统计」标签页
2. 查看薄弱章节、学习曲线、效率分析
3. 设置考试日期查看目标倒推计划

## ⚠️ 注意事项

- Render 免费版15分钟无访问会休眠，首次打开需等待30秒
- PythonAnywhere 免费版每月有流量限制，个人使用足够
- SQLite 数据库文件请定期备份

## 📄 License

MIT
