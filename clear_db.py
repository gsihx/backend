import psycopg2


def clear_tasks():
    try:
        conn = psycopg2.connect(
            host='localhost',
            database='ege_platform',
            user='postgres',
            password='jobs22812',
            client_encoding='UTF8'
        )
        cursor = conn.cursor()

        # Эта команда полностью удаляет все задачи и сбрасывает счетчик ID
        cursor.execute('TRUNCATE TABLE tasks CASCADE;')
        conn.commit()
        conn.close()
        print("✅ База данных успешно очищена от старых задач!")
    except Exception as e:
        print(f"❌ Ошибка: {e}")


if __name__ == '__main__':
    clear_tasks()
