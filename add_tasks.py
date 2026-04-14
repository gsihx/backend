import json
import psycopg2
import os

def get_db_connection():
    # Важно: берем URL из конфига Amvera
    return psycopg2.connect(os.getenv('DATABASE_URL'))

def load_tasks():
    try:
        with open('tasks_data.json', 'r', encoding='utf-8') as file:
            tasks = json.load(file)
    except FileNotFoundError:
        print("Файл tasks_data.json не найден!")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    count_added = 0
    count_skipped = 0

    for task in tasks:
        # ПРОВЕРКА: Есть ли уже такое задание в базе?
        cursor.execute(
            '''
            SELECT id FROM tasks 
            WHERE subject = %s AND variant_number = %s AND task_number = %s
            ''',
            (task['subject'], task['variant_number'], task['task_number'])
        )

        if cursor.fetchone() is None:
            # Если задания нет, добавляем его
            cursor.execute(
                '''
                INSERT INTO tasks (content, subject, correct_answer, task_number, variant_number) 
                VALUES (%s, %s, %s, %s, %s)
                ''',
                (task['content'], task['subject'], task['correct_answer'], task['task_number'], task['variant_number'])
            )
            count_added += 1
        else:
            # Если задание уже есть, пропускаем
            count_skipped += 1

    conn.commit()
    conn.close()

    print(f"Успешно добавлено новых задач: {count_added}")
    print(f"Пропущено (уже были в базе): {count_skipped}")


def create_tables():
    conn = get_db_connection()
    cursor = conn.cursor()

    # ВАЖНО: Удаляем таблицу, чтобы пересоздать её с нужной колонкой 'content'
    cursor.execute("DROP TABLE IF EXISTS tasks CASCADE;")

    # Создаем заново с правильными именами
    cursor.execute('''
        CREATE TABLE tasks (
            id SERIAL PRIMARY KEY,
            content TEXT NOT NULL,
            subject VARCHAR(100) NOT NULL,
            correct_answer TEXT NOT NULL,
            task_number INTEGER NOT NULL,
            variant_number INTEGER NOT NULL
        );
    ''')

    # Таблицу юзеров можно оставить с IF NOT EXISTS, она вроде ок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL
        );
    ''')

    conn.commit()
    conn.close()
    print("Таблицы ПЕРЕСОЗДАНЫ с правильными колонками!")

if __name__ == '__main__':
    create_tables()
    load_tasks()
