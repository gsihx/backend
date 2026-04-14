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
            # Замени на свой адрес в Amvera
            requests.get("https://ege-api2-gsihx.amvera.io/tasks")
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
    conn = get_db_connection()
    # Используем RealDictCursor, чтобы обращаться к полям по именам
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # --- СИЛОВОЙ ФИКС СТРУКТУРЫ ---
    # Добавляем колонку is_admin, если её вдруг нет в базе
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;")
    conn.commit()
    # ------------------------------

    cur.execute("SELECT * FROM users WHERE username = %s", (data.get('username'),))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if user and check_password_hash(user['password_hash'], data.get('password')):
        # Теперь user['is_admin'] точно существует (как минимум со значением по умолчанию False)
        is_admin_value = bool(user.get('is_admin', False))

        token = jwt.encode({
            'user_id': user['id'],
            'username': user['username'],
            'is_admin': is_admin_value,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm="HS256")

        return jsonify({
            'token': token,
            'is_admin': is_admin_value,
            'username': user['username']
        })

    return jsonify({'error': 'Неверный вход'}), 401


@app.route('/tasks', methods=['GET'])
def get_tasks():
    subject = request.args.get('subject', 'Все')
    variant = request.args.get('variant', 'Все')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # --- СИЛОВОЙ ФИКС БАЗЫ (ЗАДАЧИ) ---
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS image_url VARCHAR(255);")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS explanation TEXT;")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS variant_number INTEGER DEFAULT 1;")
    conn.commit()
    # ----------------------------------

    query = "SELECT id, subject, variant_number, task_number, content, correct_answer, image_url FROM tasks WHERE 1=1"
    params = []
    if subject != 'Все':
        query += " AND subject = %s"
        params.append(subject)
    if variant != 'Все':
        query += " AND variant_number = %s"
        params.append(variant)
    query += " ORDER BY task_number ASC"

    cur.execute(query, tuple(params))
    tasks = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(tasks)


@app.route('/check_answer', methods=['POST', 'OPTIONS'])
@token_required
def check_answer(current_user_id):
    if request.method == 'OPTIONS':
        return '', 200

    data = request.json
    task_id = data.get('task_id')
    user_answer = str(data.get('answer')).strip().lower()

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # --- СИЛОВОЙ ФИКС: Создаем таблицы для сохранения прогресса ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS solved_tasks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            solved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, task_id)
        );
        CREATE TABLE IF NOT EXISTS exam_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            subject VARCHAR(50),
            score INTEGER,
            total_tasks INTEGER,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    # -----------------------------------------------------------

    cur.execute("SELECT correct_answer FROM tasks WHERE id = %s", (task_id,))
    task = cur.fetchone()

    if not task:
        cur.close();
        conn.close()
        return jsonify({"error": "Задача не найдена"}), 404

    is_correct = str(task['correct_answer']).strip().lower() == user_answer

    if is_correct:
        # Сохраняем прогресс только если ответ верный
        cur.execute("INSERT INTO solved_tasks (user_id, task_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (current_user_id, task_id))
        conn.commit()

    cur.close()
    conn.close()

    return jsonify({
        "is_correct": is_correct,
        "correct_answer": task['correct_answer']
    })

@app.route('/user_achievements', methods=['GET'])
def get_achievements():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Создаем таблицы, если их нет
        cur.execute("""
            CREATE TABLE IF NOT EXISTS achievements (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                icon TEXT,
                requirement_type VARCHAR(50),
                requirement_value INTEGER
            );
            CREATE TABLE IF NOT EXISTS user_achievements (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                achievement_id INTEGER,
                earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()

        # 2. НАПОЛНЯЕМ БАЗУ, если она пустая
        cur.execute("SELECT COUNT(*) FROM achievements")
        if cur.fetchone()['count'] == 0:
            cur.execute("""
                INSERT INTO achievements (name, description, icon, requirement_type, requirement_value) VALUES 
                ('Первый шаг', 'Решена первая задача', '🎯', 'solved_tasks', 1),
                ('Стахановец', 'Решено 10 задач суммарно', '⚒️', 'solved_tasks', 10),
                ('Крепкий орешек', 'Решено 50 задач', '🧠', 'solved_tasks', 50),
                ('Первый КИМ', 'Завершен первый случайный экзамен', '📄', 'exams_completed', 1),
                ('Марафонец', 'Завершено 5 полных экзаменов', '🏃', 'exams_completed', 5);
            """)
            conn.commit()
            print("Достижения успешно загружены в базу!")

        # 3. Отдаем достижения на фронтенд
        cur.execute("""SELECT a.*, (ua.id IS NOT NULL) as earned FROM achievements a 
                       LEFT JOIN user_achievements ua ON a.id = ua.achievement_id""")
        data = cur.fetchall()

        cur.close()
        conn.close()

        return jsonify({"achievements": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

@app.route('/user_exam_history', methods=['GET', 'OPTIONS'])
def get_user_exam_history():
    # Отвечаем браузеру, что всё ок (для CORS)
    if request.method == 'OPTIONS':
        return '', 200

    # Возвращаем пустую историю, чтобы фронтенд не выдавал ошибку
    return jsonify({
        "history": []
    }), 200

# --- МАРШРУТЫ ДЛЯ АДМИН-ПАНЕЛИ (VUE) ---

@app.route('/api/admin/tasks', methods=['POST'])
@admin_required
def add_task(current_user_id):
    data = request.form
    image_file = request.files.get('image')
    image_url = None

    if image_file:
        filename = secure_filename(image_file.filename)
        # Добавляем метку времени, чтобы картинки с одинаковыми именами не перезаписывали друг друга
        unique_name = str(int(time.time())) + "_" + filename
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        image_file.save(filepath)
        image_url = f"/{filepath}"

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tasks (subject, task_number, variant_number, content, correct_answer, image_url)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (data.get('subject'), data.get('task_number'), data.get('variant_number', 1),
          data.get('content'), data.get('correct_answer'), image_url))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({'message': 'Задача успешно создана'}), 201

@app.route('/api/admin/tasks/<int:task_id>', methods=['PUT'])
@admin_required
def update_task(current_user_id, task_id):
    data = request.form
    image_file = request.files.get('image')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Получаем старую картинку, если новую не загрузили
    cur.execute("SELECT image_url FROM tasks WHERE id = %s", (task_id,))
    task = cur.fetchone()
    image_url = task['image_url'] if task else None

    if image_file:
        filename = secure_filename(image_file.filename)
        unique_name = str(int(time.time())) + "_" + filename
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        image_file.save(filepath)
        image_url = f"/{filepath}"

    cur.execute("""
        UPDATE tasks
        SET subject = %s, task_number = %s, variant_number = %s,
            content = %s, correct_answer = %s, image_url = %s
        WHERE id = %s
    """, (data.get('subject'), data.get('task_number'), data.get('variant_number', 1),
          data.get('content'), data.get('correct_answer'), image_url, task_id))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({'message': 'Задача обновлена'}), 200

@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
@admin_required
def delete_task(current_user_id, task_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({'message': 'Задача удалена'}), 200

@app.route('/make_me_admin')
def make_admin():
    conn = get_db_connection()
    cur = conn.cursor()
    # Выполняем SQL-запрос напрямую из Питона
    cur.execute("UPDATE users SET is_admin = TRUE WHERE username = 'gsihx231213';")
    conn.commit()
    cur.close()
    conn.close()
    return "Права администратора успешно выданы! Вернитесь на сайт и перезайдите в аккаунт."


@app.route('/reset_and_seed_tasks')
def reset_and_seed_tasks():
    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Полностью очищаем таблицу задач и сбрасываем нумерацию ID
    cur.execute("TRUNCATE TABLE tasks RESTART IDENTITY CASCADE;")

    # 2. Подготавливаем первый вариант (по 2 задания на каждый предмет)
    sample_tasks = [
        # Информатика (Вариант 1)
        ("Информатика", 1, 1,
         "На рисунке справа схема дорог Н-ского района изображена в виде графа... Найдите длину дороги из пункта А в пункт В.",
         "15", None),
        ("Информатика", 4, 1,
         "По каналу связи передаются сообщения, содержащие только 4 буквы: А, Б, В, Г. Для передачи используется код Фано...",
         "101", None),

        # Математика (Вариант 1)
        ("Математика", 1, 1, "В треугольнике ABC угол C равен 90°, AC = 6, BC = 8. Найдите гипотенузу AB.", "10", None),
        ("Математика", 5, 1, "Найдите корень уравнения: 2^(3-x) = 16", "-1", None),

        # Русский язык (Вариант 1)
        ("Русский язык", 4, 1,
         "Укажите варианты ответов, в которых верно выделена буква, обозначающая ударный гласный звук: 1) тОрты 2) звонИт 3) кУхонный...",
         "13", None),
        ("Русский язык", 9, 1,
         "Укажите варианты ответов, в которых во всех словах одного ряда пропущена безударная чередующаяся гласная корня...",
         "14", None)
    ]

    # 3. Загружаем их в базу
    for task in sample_tasks:
        cur.execute("""
            INSERT INTO tasks (subject, task_number, variant_number, content, correct_answer, image_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, task)

    conn.commit()
    cur.close()
    conn.close()
    return "База задач успешно очищена! Вариант №1 загружен (6 тестовых задач)."

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get("PORT", 80))
    )