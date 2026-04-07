import json
import psycopg2

def get_db_connection():
    return psycopg2.connect(
        host='localhost',
        database='ege_platform',
        user='postgres',
        password='jobs22812',
        client_encoding='UTF8'
    )

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

if __name__ == '__main__':
    load_tasks()
