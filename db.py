import sqlite3
import os
import json
from datetime import datetime

# Database file name
DB_FILE = "bot_data.sqlite"

# Connect to SQLite database (create if not exists)
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()

# Create tables if they do not exist
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    username TEXT,
    group_name TEXT,
    subgroup INTEGER,
    notify INTEGER DEFAULT 0,
    reminders INTEGER DEFAULT 0
);
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    name TEXT,
    group_name TEXT,
    details TEXT,
    status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    question TEXT,
    asked_at TEXT DEFAULT CURRENT_TIMESTAMP,
    answered INTEGER DEFAULT 0,
    answer TEXT,
    answered_at TEXT
);
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS faq (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT,
    answer TEXT
);
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    url TEXT
);
""")
conn.commit()

# Автоматическое заполнение базы данными при первом запуске, если она пуста
cur.execute("SELECT COUNT(*) FROM users")
users_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM requests")
requests_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM questions")
questions_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM news")
news_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM faq")
faq_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM resources")
res_count = cur.fetchone()[0]
if users_count == 0 and requests_count == 0 and questions_count == 0 and news_count == 0 and faq_count == 0 and res_count == 0:
    # Добавляем 5 примерных пользователей (с разными группами)
    sample_users = [
        (1, "Иван", "Иванов", "ivanov", "ПИ-21", 1, 0, 0),
        (2, "Петр", "Петров", "petrov", "ПИ-22", 2, 0, 0),
        (3, "Николай", "Николаев", "nick", "ИК-19", 1, 0, 0),
        (4, "Сергей", "Сергеев", "sergey", "БИ-20", 2, 0, 0),
        (5, "Алексей", "Алексеев", "alex", "ФИ-18", 1, 0, 0)
    ]
    for user in sample_users:
        cur.execute("INSERT OR IGNORE INTO users (user_id, first_name, last_name, username, group_name, subgroup, notify, reminders) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", user)
    # Добавляем по 3 заявки каждого типа (spravka, otsrochka, hvost)
    sample_requests = [
        # spravka
        (1, "spravka", "Иван Иванов", "ПИ-21", "для стипендии", "Принята"),
        (2, "spravka", "Петр Петров", "ПИ-22", "для военкомата", "Принята"),
        (3, "spravka", "Николай Николаев", "ИК-19", "для общежития", "Принята"),
        # otsrochka
        (2, "otsrochka", "Петр Петров", "ПИ-22", "болезнь", "Принята"),
        (4, "otsrochka", "Сергей Сергеев", "БИ-20", "семейные обстоятельства", "Принята"),
        (5, "otsrochka", "Алексей Алексеев", "ФИ-18", "участие в конференции", "Принята"),
        # hvost (пересдача)
        (1, "hvost", "Иван Иванов", "ПИ-21", "Математика", "Принята"),
        (3, "hvost", "Николай Николаев", "ИК-19", "История", "Принята"),
        (5, "hvost", "Алексей Алексеев", "ФИ-18", "Информатика", "Принята")
    ]
    for req in sample_requests:
        cur.execute("INSERT INTO requests (user_id, type, name, group_name, details, status) VALUES (?, ?, ?, ?, ?, ?)", req)
    # Добавляем 3 примера FAQ (вопрос + ответ)
    sample_faq = [
        ("Как подать заявку на справку?", "Используйте команду /spravka и следуйте инструкциям."),
        ("Как включить напоминания о дедлайнах?", "Отправьте команду /reminders для включения или отключения напоминаний."),
        ("Что делать, если я пропустил экзамен по болезни?", "Вы можете подать заявку на пересдачу экзамена командой /hvost.")
    ]
    for q, a in sample_faq:
        cur.execute("INSERT INTO faq (question, answer) VALUES (?, ?)", (q, a))
    # Добавляем 3 ресурса (название + URL)
    sample_resources = [
        ("📚 Электронная библиотека", "https://library.mgppu.ru"),
        ("🌐 Сайт МГППУ", "https://mgppu.ru"),
        ("🎓 Личный кабинет студента", "https://lk.mgppu.ru")
    ]
    for name, url in sample_resources:
        cur.execute("INSERT INTO resources (name, url) VALUES (?, ?)", (name, url))
    # Добавляем 3 новости/объявления
    sample_news = [
        "Начало сессии перенесено на 10 июня.",
        "Прием заявок на стипендию открыт.",
        "Опубликовано новое расписание занятий."
    ]
    for content in sample_news:
        cur.execute("INSERT INTO news (content) VALUES (?)", (content,))
    # Добавляем 3 вопроса от пользователей (один из них сразу с ответом администратора)
    sample_questions = [
        (1, "Когда начнется экзаменационная сессия?"),
        (2, "Где можно посмотреть расписание занятий?"),
        (3, "Как восстановить пароль от электронной почты?")
    ]
    answered_qid = None
    for user_id, question_text in sample_questions:
        cur.execute("INSERT INTO questions (user_id, question) VALUES (?, ?)", (user_id, question_text))
        if answered_qid is None:
            answered_qid = cur.lastrowid
    # Отмечаем один вопрос (первый) как отвеченный администратором
    if answered_qid:
        cur.execute("UPDATE questions SET answered=1, answer=?, answered_at=? WHERE id=?", 
                    ("Экзаменационная сессия начнется в следующем месяце.", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), answered_qid))
    conn.commit()

# Функции для работы с данными (пользователи, заявки, вопросы, новости, FAQ, ресурсы)
def ensure_user(user):
    """Убедиться, что пользователь есть в базе (если нет, добавить его)."""
    uid = user.id
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    username = user.username or ""
    # Insert user if not exists
    cur.execute("INSERT OR IGNORE INTO users (user_id, first_name, last_name, username, notify, reminders) VALUES (?, ?, ?, ?, 0, 0)",
                (uid, first_name, last_name, username))
    # Update name/username on each call (in case they changed)
    cur.execute("UPDATE users SET first_name=?, last_name=?, username=? WHERE user_id=?",
                (first_name, last_name, username, uid))
    conn.commit()

def update_user_group(user_id, group_name):
    """Обновить учебную группу пользователя и сбросить подгруппу (None)."""
    cur.execute("UPDATE users SET group_name=?, subgroup=NULL WHERE user_id=?", (group_name, user_id))
    conn.commit()

def update_user_subgroup(user_id, subgroup):
    """Обновить подгруппу пользователя."""
    cur.execute("UPDATE users SET subgroup=? WHERE user_id=?", (subgroup, user_id))
    conn.commit()

def toggle_notify(user_id):
    """Переключить флаг уведомлений расписания для пользователя. Возвращает новое состояние (1 или 0)."""
    cur.execute("SELECT notify FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    new_state = 1
    if row:
        current = row[0] or 0
        new_state = 0 if current == 1 else 1
    cur.execute("UPDATE users SET notify=? WHERE user_id=?", (new_state, user_id))
    conn.commit()
    return new_state

def toggle_reminders(user_id):
    """Переключить флаг учебных напоминаний для пользователя. Возвращает новое состояние (1 или 0)."""
    cur.execute("SELECT reminders FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    new_state = 1
    if row:
        current = row[0] or 0
        new_state = 0 if current == 1 else 1
    cur.execute("UPDATE users SET reminders=? WHERE user_id=?", (new_state, user_id))
    conn.commit()
    return new_state

def get_user_group_sub(user_id):
    """Получить группу и подгруппу пользователя (возвращает tuple)."""
    cur.execute("SELECT group_name, subgroup FROM users WHERE user_id=?", (user_id,))
    return cur.fetchone()

def get_user_profile(user_id):
    """Получить информацию профиля пользователя: группа, подгруппа, notify, reminders."""
    cur.execute("SELECT group_name, subgroup, notify, reminders FROM users WHERE user_id=?", (user_id,))
    return cur.fetchone()

def add_question(user_id, text):
    """Сохранить вопрос пользователя (неотвеченный) в базе. Возвращает ID вопроса."""
    cur.execute("INSERT INTO questions (user_id, question) VALUES (?, ?)", (user_id, text))
    conn.commit()
    return cur.lastrowid

def get_unanswered_questions():
    """Получить список всех вопросов пользователей без ответа (с именами пользователей)."""
    cur.execute(
        "SELECT q.id, u.first_name, q.question, datetime(q.asked_at, 'localtime') "
        "FROM questions q LEFT JOIN users u ON q.user_id = u.user_id "
        "WHERE q.answered = 0"
    )
    return cur.fetchall()

def answer_question(qid, answer_text):
    """Отметить вопрос как отвеченный и сохранить ответ. Возвращает (user_id, question) или None, если не найден."""
    cur.execute("SELECT user_id, question FROM questions WHERE id=? AND answered=0", (qid,))
    row = cur.fetchone()
    if not row:
        return None
    user_id, question_text = row
    cur.execute("UPDATE questions SET answered=1, answer=?, answered_at=datetime('now') WHERE id=?", (answer_text, qid))
    conn.commit()
    return (user_id, question_text)

def get_all_faq():
    """Получить все записи FAQ списком (question, answer)."""
    cur.execute("SELECT question, answer FROM faq")
    return cur.fetchall()

def add_faq(question_text, answer_text):
    """Добавить новую запись в FAQ."""
    cur.execute("INSERT INTO faq (question, answer) VALUES (?, ?)", (question_text, answer_text))
    conn.commit()

def delete_faq(faq_id):
    """Удалить запись FAQ по ID. Возвращает True, если удалено успешно."""
    cur.execute("DELETE FROM faq WHERE id=?", (faq_id,))
    deleted = cur.rowcount
    conn.commit()
    return deleted > 0

def get_all_resources():
    """Получить все ресурсы (список tuple (name, url))."""
    cur.execute("SELECT name, url FROM resources")
    return cur.fetchall()

def add_resource(name, url):
    """Добавить новый ресурс (ссылку)."""
    cur.execute("INSERT INTO resources (name, url) VALUES (?, ?)", (name, url))
    conn.commit()

def delete_resource(res_id):
    """Удалить ресурс по ID. Возвращает True, если удалён ресурс."""
    cur.execute("DELETE FROM resources WHERE id=?", (res_id,))
    deleted = cur.rowcount
    conn.commit()
    return deleted > 0

def insert_request(user_id, req_type, name, group_name, details, status="Принята"):
    """Добавить новую заявку (spravka, otsrochka, hvost) в базу данных."""
    cur.execute("INSERT INTO requests (user_id, type, name, group_name, details, status) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, req_type, name, group_name, details, status))
    conn.commit()
    return cur.lastrowid

def get_requests_by_user(user_id):
    """Получить все заявки пользователя в виде списка tuple (type, details, status)."""
    cur.execute("SELECT type, details, status FROM requests WHERE user_id=?", (user_id,))
    return cur.fetchall()

def get_all_news():
    """Получить все новости/объявления списком (content, created_at)."""
    cur.execute("SELECT content, created_at FROM news ORDER BY created_at DESC")
    return cur.fetchall()

def add_news(content):
    """Добавить новую новость/объявление в базу данных."""
    cur.execute("INSERT INTO news (content) VALUES (?)", (content,))
    conn.commit()

def get_all_user_ids():
    """Получить список всех user_id пользователей."""
    cur.execute("SELECT user_id FROM users")
    result = cur.fetchall()
    return [row[0] for row in result]

def get_users_for_notify():
    """Получить список (user_id, group_name, subgroup) всех пользователей с notify=1 (включены уведомления)."""
    cur.execute("SELECT user_id, group_name, subgroup FROM users WHERE notify=1 AND group_name IS NOT NULL")
    return cur.fetchall()

def get_users_for_reminders():
    """Получить список user_id всех пользователей с reminders=1 (включены напоминания)."""
    cur.execute("SELECT user_id FROM users WHERE reminders=1")
    result = cur.fetchall()
    return [row[0] for row in result]

def get_stats():
    """Получить статистику использования (словарь с ключами users, requests_total, spravka, otsrochka, hvost, questions_total, questions_unanswered, news, faq, resources)."""
    stats = {}
    # Count users
    cur.execute("SELECT COUNT(*) FROM users")
    stats["users"] = cur.fetchone()[0] or 0
    # Count requests total and by type
    cur.execute("SELECT COUNT(*) FROM requests")
    stats["requests_total"] = cur.fetchone()[0] or 0
    cur.execute("SELECT type, COUNT(*) FROM requests GROUP BY type")
    type_counts = {t: c for t, c in cur.fetchall()}
    stats["spravka"] = type_counts.get("spravka", 0)
    stats["otsrochka"] = type_counts.get("otsrochka", 0)
    stats["hvost"] = type_counts.get("hvost", 0)
    # Questions total and unanswered
    cur.execute("SELECT COUNT(*), SUM(CASE WHEN answered=0 THEN 1 ELSE 0 END) FROM questions")
    q_total, q_unanswered = cur.fetchone()
    stats["questions_total"] = q_total or 0
    stats["questions_unanswered"] = q_unanswered or 0
    # News count
    cur.execute("SELECT COUNT(*) FROM news")
    stats["news"] = cur.fetchone()[0] or 0
    # FAQ count
    cur.execute("SELECT COUNT(*) FROM faq")
    stats["faq"] = cur.fetchone()[0] or 0
    # Resources count
    cur.execute("SELECT COUNT(*) FROM resources")
    stats["resources"] = cur.fetchone()[0] or 0
    return stats
def delete_news(news_id: int) -> bool:
    """
    Удалить новость по ID. 
    Возвращает True, если запись была удалена, иначе False.
    """
    cur.execute("DELETE FROM news WHERE id = ?", (news_id,))
    deleted = cur.rowcount
    conn.commit()
    return deleted > 0
