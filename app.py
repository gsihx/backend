import os
import jwt
import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from functools import wraps
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import threading
import time
import requests

app = Flask(__name__)

# --- 1. НАСТРОЙКИ И КОРС ---
CORS(app, resources={r"/*": {"origins": "*"}})
app.config['SECRET_KEY'] = 'super-secret-key-6d8f9a2b1c4e7f3g5h1j9k0l-2026'
UPLOAD_FOLDER = 'static/uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Чистим DATABASE_URL от невидимых символов (0xc2 и прочее)
raw_url = os.getenv('DATABASE_URL')
clean_url = raw_url.replace('\xa0', '').strip().encode('utf-8', 'ignore').decode('utf-8')
app.config['SQLALCHEMY_DATABASE_URI'] = clean_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- 2. ИНИЦИАЛИЗАЦИЯ SQLALCHEMY И МОДЕЛИ (ДЛЯ АДМИНКИ) ---
db = SQLAlchemy(app)
with app.app_context():
    db.create_all()
    print("Database tables created successfully!")

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(100))
    task_number = db.Column(db.Integer)
    variant_number = db.Column(db.Integer, default=1)
    content = db.Column(db.Text)
    correct_answer = db.Column(db.String(255))
    image_url = db.Column(db.String(255))
    explanation = db.Column(db.Text)

class Achievement(db.Model):
    __tablename__ = 'achievements'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    icon = db.Column(db.Text)
    requirement_type = db.Column(db.String(50))
    requirement_value = db.Column(db.Integer)

class UserAchievement(db.Model):
    __tablename__ = 'user_achievements'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    achievement_id = db.Column(db.Integer)
    earned_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# --- 3. АДМИН-ПАНЕЛЬ (ТЕПЕРЬ БЕЗ КРАСНЫХ ЛИНИЙ) ---
admin = Admin(app, name='ЕГЭ Панель', url='/admin')
admin.add_view(ModelView(Task, db.session, name='Задачи'))
admin.add_view(ModelView(Achievement, db.session, name='Достижения'))
admin.add_view(ModelView(User, db.session, name='Пользователи'))

# --- 4. ПОДКЛЮЧЕНИЕ ЧЕРЕЗ PSYCOPG2 ---
def get_db_connection():
    url = os.getenv('DATABASE_URL')
    if url:
        # Очищаем от невидимых символов
        url = url.replace('\xa0', '').strip().encode('utf-8', 'ignore').decode('utf-8')
        return psycopg2.connect(url)

def init_db():
    with app.app_context():
        db.create_all()
        print("База данных готова и таблицы созданы!")

def keep_alive():
    while True:
        try:
            requests.get("https://backend-production-bf52.up.railway.app/tasks")
            print("Ping: Database is awake!")
        except: pass
        time.sleep(100)

threading.Thread(target=keep_alive, daemon=True).start()

# --- 5. ДЕКОРАТОРЫ ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token: return jsonify({'message': 'Токен отсутствует!'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user_id = data['user_id']
        except: return jsonify({'message': 'Токен недействителен!'}), 401
        return f(current_user_id, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            if not data.get('is_admin'): return jsonify({'message': 'Нужны права админа!'}), 403
            current_user_id = data['user_id']
        except: return jsonify({'message': 'Ошибка авторизации!'}), 401
        return f(current_user_id, *args, **kwargs)
    return decorated

# --- 6. ТВОИ ОРИГИНАЛЬНЫЕ МАРШРУТЫ ---

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()

    # --- СИЛОВОЙ ФИКС БАЗЫ ---
    # Если колонки password_hash нет, этот запрос её создаст прямо на лету
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);")
    conn.commit()
    # -------------------------
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);")
    # 2. Снимаем ограничение NOT NULL со старой колонки password, чтобы она не мешала
    cur.execute("ALTER TABLE users ALTER COLUMN password DROP NOT NULL;")
    conn.commit()

    cur.execute("SELECT id FROM users WHERE username = %s", (data.get('username'),))
    if cur.fetchone():
        cur.close();
        conn.close()
        return jsonify({'message': 'Пользователь уже существует'}), 409

    cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (data.get('username'), generate_password_hash(data.get('password'))))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'message': 'Регистрация успешна'}), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE username = %s", (data.get('username'),))
    user = cur.fetchone(); conn.close()
    if user and check_password_hash(user['password_hash'], data.get('password')):
        token = jwt.encode({
            'user_id': user['id'], 'username': user['username'],
            'is_admin': bool(user['is_admin']),
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'token': token, 'is_admin': bool(user['is_admin']), 'username': user['username']})
    return jsonify({'error': 'Неверный вход'}), 401

@app.route('/tasks', methods=['GET'])
def get_tasks():
    subject = request.args.get('subject', 'Все')
    variant = request.args.get('variant', 'Все')
    query = "SELECT id, subject, variant_number, task_number, content, correct_answer, image_url FROM tasks WHERE 1=1"
    params = []
    if subject != 'Все':
        query += " AND subject = %s"; params.append(subject)
    if variant != 'Все':
        query += " AND variant_number = %s"; params.append(variant)
    query += " ORDER BY task_number ASC"
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, tuple(params))
    tasks = cur.fetchall(); cur.close(); conn.close()
    return jsonify(tasks)

@app.route('/check_answer', methods=['POST'])
@token_required
def check_answer(current_user_id):
    data = request.json
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT correct_answer FROM tasks WHERE id = %s", (data.get('task_id'),))
    task = cur.fetchone()
    is_correct = str(data.get('user_answer', '')).strip().lower() == str(task['correct_answer']).strip().lower()
    if is_correct:
        cur.execute("INSERT INTO solved_tasks (user_id, task_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (current_user_id, data.get('task_id')))
        conn.commit()
    cur.close(); conn.close()
    return jsonify({'correct': is_correct})

@app.route('/user_achievements', methods=['GET'])
def get_achievements():
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""SELECT a.*, (ua.id IS NOT NULL) as earned FROM achievements a 
                       LEFT JOIN user_achievements ua ON a.id = ua.achievement_id""")
        data = cur.fetchall(); cur.close(); conn.close()
        return jsonify(data)
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/save_exam_result', methods=['POST'])
@token_required
def save_res(current_user_id):
    data = request.json
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO exam_results (user_id, subject, score, total_tasks) VALUES (%s, %s, %s, %s)",
                (current_user_id, data['subject'], data['score'], data['total']))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'status': 'ok'}), 201


@app.route('/user_solved_tasks', methods=['GET', 'OPTIONS'])
def get_user_solved_tasks():
    # Если браузер проверяет CORS (preflight запрос)
    if request.method == 'OPTIONS':
        return '', 200

    # Заглушка для статистики в Личном кабинете
    return jsonify({
        "solved_count": 0,
        "average_score": 0,
        "achievements": [],
        "recent_exams": []
    }), 200


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get("PORT", 80))
    )