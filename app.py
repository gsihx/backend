import os
import jwt
import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from functools import wraps
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import threading
import time
import requests



app = Flask(__name__)
# Разрешаем запросы с фронтенда Vite
# Измените строку настройки CORS на эту:
CORS(app, resources={r"/*": {
    "origins": "*",
    "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

# Секретный ключ для JWT (ИСПРАВЛЕНО: config — это словарь)
app.config['SECRET_KEY'] = 'super-secret-key-6d8f9a2b1c4e7f3g5h1j9k0l-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://localhost/ege_db')

DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    DATABASE_URL = "postgresql://postgres:password@localhost:5432/ege_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
Base.metadata.create_all(bind=engine)

UPLOAD_FOLDER = 'static/uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- ПОДКЛЮЧЕНИЕ К POSTGRESQL ---
def get_db_connection():
    conn = psycopg2.connect(
        host='localhost',
        database='ege_platform',
        user='postgres',
        password='jobs22812',
        client_encoding='UTF8'
    )
    return conn

def keep_alive():
    while True:
        try:
            # Пингуем собственный эндпоинт или делаем запрос в БД
            # Замени на URL своего бэкенда на Railway
            requests.get("https://backend-production-bf52.up.railway.app/tasks")
            print("Ping successful: Database is awake!")
        except Exception as e:
            print(f"Ping failed: {e}")

        # Спим 10 минут (600 секунд)
        time.sleep(300)

# --- ДЕКОРАТОРЫ ЗАЩИТЫ ---

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

        if not token:
            return jsonify({'message': 'Токен отсутствует!'}), 401

        try:
            # ИСПРАВЛЕНО: ключ и алгоритм
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user_id = data['user_id']
        except Exception as e:
            return jsonify({'message': 'Токен недействителен или просрочен!'}), 401

        return f(current_user_id, *args, **kwargs)

    return decorated

threading.Thread(target=keep_alive, daemon=True).start()

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

        if not token:
            return jsonify({'message': 'Токен отсутствует!'}), 401

        try:
            # ИСПРАВЛЕНО: ключ и алгоритм
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            if not data.get('is_admin'):
                return jsonify({'message': 'Доступ запрещен: требуются права администратора!'}), 403
            current_user_id = data['user_id']
        except:
            return jsonify({'message': 'Токен недействителен!'}), 401

        return f(current_user_id, *args, **kwargs)

    return decorated


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def update_user_achievements(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Добавляем [0], чтобы получить само число, а не кортеж
        cur.execute("SELECT COUNT(*) FROM solved_tasks WHERE user_id = %s", (user_id,))
        total_solved = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM exam_results WHERE user_id = %s", (user_id,))
        exams_done = cur.fetchone()[0]

        cur.execute("SELECT id, requirement_type, requirement_value FROM achievements")
        all_achievements = cur.fetchall()

        for ach in all_achievements:
            ach_id, req_type, req_val = ach
            should_award = False

            if req_type == 'total_solved' and total_solved >= req_val:
                should_award = True
            elif req_type == 'exams_completed' and exams_done >= req_val:
                should_award = True

            if should_award:
                cur.execute("""
                    INSERT INTO user_achievements (user_id, achievement_id) 
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                """, (user_id, ach_id))
        conn.commit()
    except Exception as e:
        print(f"Ошибка при обновлении достижений: {e}")
    finally:
        cur.close()
        conn.close()


# --- МАРШРУТЫ ДЛЯ ЗАДАНИЙ ---

@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        res = make_response()
        res.headers.add("Access-Control-Allow-Origin", "*")
        res.headers.add("Access-Control-Allow-Headers", "*")
        res.headers.add("Access-Control-Allow-Methods", "*")
        return res




# ИСПРАВЛЕНО: Изменен метод на GET, чтобы не конфликтовать с добавлением
@app.route('/tasks', methods=['GET'])
def get_tasks():
    subject = request.args.get('subject', 'Все')
    variant = request.args.get('variant', 'Все')

    # ПРОВЕРЬ: Все ли эти колонки есть в твоей таблице PostgreSQL?
    # Если ты не добавлял image_url или variant_number в БД через pgAdmin, сервер выдаст 500
    query = "SELECT id, subject, variant_number, task_number, content, correct_answer, image_url FROM tasks WHERE 1=1"
    params = []

    if subject != 'Все':
        query += " AND subject = %s"
        params.append(subject)
    if variant != 'Все':
        query += " AND variant_number = %s"
        params.append(variant)

    # ORDER BY всегда в самом конце!
    query += " ORDER BY task_number ASC"

    try:
        conn = get_db_connection()
        # Использование RealDictCursor важно для того, чтобы Vue понимал ключи (id, content и т.д.)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, tuple(params))
        tasks = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(tasks)  # Возвращаем список задач
    except Exception as e:
        print(f"Критическая ошибка БД: {e}")  # Это появится в консоли PyCharm
        return jsonify({"error": str(e)}), 500
    pass


@app.route('/api/admin/tasks', methods=['POST'])
@admin_required
def add_Task(current_user_id):
    try:
        # Получаем текстовые данные из form-data
        subject = request.form.get('subject')
        task_number = request.form.get('task_number')
        variant_number = request.form.get('variant_number', 1)  # Добавляем получение варианта
        content = request.form.get('content')
        correct_answer = request.form.get('correct_answer')

        image_url = None

        # Проверяем, есть ли файл в запросе
        if 'image' in request.files:
            file = request.files['image']
            if file.filename != '':
                filename = secure_filename(file.filename)
                # Добавляем уникальный префикс, чтобы имена не дублировались
                filename = f"{task_number}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image_url = f"/static/uploads/{filename}"

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
                INSERT INTO tasks (subject, task_number, variant_number, content, correct_answer, image_url)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (subject, task_number, variant_number, content, correct_answer, image_url))
        conn.commit()

        return jsonify({"message": "Задача создана"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/tasks/<int:task_id>', methods=['PUT'])
@admin_required
def admin_update_task(current_user_id, task_id):
    try:
        # Используем .form.get, так как Axios отправляет FormData
        subject = request.form.get('subject')
        task_number = request.form.get('task_number')
        variant_number = request.form.get('variant_number')
        content = request.form.get('content')
        correct_answer = request.form.get('correct_answer')

        # Отладочный принт в консоль PyCharm, чтобы увидеть, что пришло
        print(f"DEBUG: {subject}, {task_number}, {variant_number}")

        if not subject or not content:
            return jsonify({"error": "Поля 'Предмет' и 'Текст' не могут быть пустыми"}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        if 'image' in request.files:
            file = request.files['image']
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_url = f"/static/uploads/{filename}"

            cur.execute("""
                UPDATE tasks 
                SET subject=%s, task_number=%s, variant_number=%s, content=%s, correct_answer=%s, image_url=%s
                WHERE id=%s
            """, (subject, task_number, variant_number, content, correct_answer, image_url, task_id))
        else:
            cur.execute("""
                UPDATE tasks 
                SET subject=%s, task_number=%s, variant_number=%s, content=%s, correct_answer=%s
                WHERE id=%s
            """, (subject, task_number, variant_number, content, correct_answer, task_id))

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "Успешно обновлено"}), 200
    except Exception as e:
        print(f"Ошибка обновления: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/user_exam_history', methods=['GET'])
@token_required
def get_exam_history(current_user_id):
    try:
        conn = get_db_connection()
        # Используем RealDictCursor, чтобы данные приходили в виде словаря (json)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Получаем последние 5 результатов экзаменов для текущего пользователя
        cur.execute("""
            SELECT subject, score, total_tasks, completed_at 
            FROM exam_results 
            WHERE user_id = %s 
            ORDER BY completed_at DESC LIMIT 5
        """, (current_user_id,))

        history = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify({'history': history}), 200
    except Exception as e:
        print(f"Ошибка в get_exam_history: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/tasks', methods=['POST'])
@admin_required
def add_task(current_user_id):
    data = request.json
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tasks (subject, task_number, content, correct_answer, variant_number, explanation)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """, (data.get('subject'), data.get('number'), data.get('text'),
              data.get('correct_answer'), data.get('variant_number', 1), data.get('explanation', '')))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "Задание добавлено!", "id": new_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/tasks/<int:task_id>', methods=['PUT'])  # ИСПРАВЛЕНО: Обычно используется PUT для обновления
@admin_required
def update_task(current_user_id, task_id):
    data = request.json
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE tasks 
            SET subject = %s, task_number = %s, content = %s, correct_answer = %s, variant_number = %s, explanation = %s
            WHERE id = %s
        """, (data['subject'], data['task_number'], data['content'], data['correct_answer'],
              data.get('variant_number', 1), data.get('explanation', ''), task_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "Задание обновлено"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
@admin_required
def delete_task(current_user_id, task_id):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1. Сначала удаляем связанные записи в solved_tasks,
        # иначе база не даст удалить саму задачу
        cur.execute("DELETE FROM solved_tasks WHERE task_id = %s", (task_id,))

        # 2. Теперь удаляем саму задачу
        cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "Задание и связанные данные удалены"}), 200

    except Exception as e:
        if conn:
            conn.rollback()  # Откатываем изменения, если произошла ошибка
            conn.close()
        print(f"ОШИБКА УДАЛЕНИЯ: {e}")  # Это появится в терминале PyCharm
        return jsonify({"error": str(e)}), 500


# --- ОСТАЛЬНЫЕ МАРШРУТЫ ---

@app.route('/api/generate_exam', methods=['GET'])  # ИСПРАВЛЕНО: GET для получения
def generate_exam():
    subject = request.args.get('subject', 'Математика')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT DISTINCT ON (task_number) id, subject, variant_number, task_number, content 
        FROM tasks WHERE subject = %s ORDER BY task_number, RANDOM()
    """
    cur.execute(query, (subject,))
    exam_tasks = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({'tasks': exam_tasks})


@app.route('/api/check_answer', methods=['POST'])
@token_required
def check_answer(current_user_id):
    data = request.json
    task_id = data.get('task_id')
    user_answer = str(data.get('user_answer', '')).strip().lower()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT correct_answer FROM tasks WHERE id = %s", (task_id,))
    task = cur.fetchone()
    if not task:
        conn.close()
        return jsonify({'error': 'Задача не найдена'}), 404

    is_correct = (user_answer == str(task['correct_answer']).strip().lower())
    if is_correct:
        cur.execute("INSERT INTO solved_tasks (user_id, task_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (current_user_id, task_id))
        conn.commit()
    cur.close()
    conn.close()
    return jsonify({'correct': is_correct})


@app.route('/api/user_achievements', methods=['GET']) # Убедись, что тут GET
@token_required
def get_achievements(current_user_id):
    update_user_achievements(current_user_id)
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT a.id, a.name, a.description, a.icon, (ua.earned_at IS NOT NULL) as earned
        FROM achievements a
        LEFT JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = %s
        ORDER BY a.id ASC
    """, (current_user_id,))
    achievements = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({'achievements': achievements})


@app.route('/user_solved_tasks', methods=['GET'])
@token_required
def get_user_solved_tasks(current_user_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Выбираем все ID задач, которые решил этот пользователь
        cur.execute('SELECT task_id FROM solved_tasks WHERE user_id = %s', (current_user_id,))

        # Превращаем список кортежей [(1,), (5,), (10,)] в простой список [1, 5, 10]
        rows = cur.fetchall()
        solved_ids = [row[0] for row in rows]

        cur.close()
        conn.close()

        return jsonify({'solved_task_ids': solved_ids}), 200
    except Exception as e:
        print(f"Ошибка в get_user_solved_tasks: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/save_exam_result', methods=['POST'])
@token_required
def save_exam_result(current_user_id):
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO exam_results (user_id, subject, score, total_tasks) VALUES (%s, %s, %s, %s)",
                (current_user_id, data['subject'], data['score'], data['total']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'message': 'Результат сохранен'}), 201


@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    if cur.fetchone():
        conn.close()
        return jsonify({'message': 'Пользователь уже существует'}), 409
    hashed_pw = generate_password_hash(password)
    cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, hashed_pw))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'message': 'Регистрация успешна'}), 201


@app.route('/api/login', methods=['POST']) # Убедись, что тут НЕТ слеша в конце
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, username, password_hash, is_admin FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    conn.close()

    if user and check_password_hash(user['password_hash'], password):
        token = jwt.encode({
            'user_id': user['id'],
            'username': user['username'],
            'is_admin': bool(user.get('is_admin')),
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm="HS256")

        return jsonify({
            'token': token,
            'is_admin': bool(user.get('is_admin')),
            'username': user['username']
        }), 200

    return jsonify({'error': 'Неверный логин или пароль'}), 401


if __name__ == '__main__':
    # host='0.0.0.0' заставляет Flask слушать внешние запросы
    app.run(debug=True, host='0.0.0.0', port=5000)