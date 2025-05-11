import os
import json
import threading
import schedule
import time
from datetime import datetime

import pandas as pd
import telebot
import sqlite3
from dotenv import load_dotenv

# ——————————————————————————————————————————————————————
# 1) Настройка и загрузка токена
# ——————————————————————————————————————————————————————
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise Exception("Не найден токен BOT_TOKEN. Убедитесь, что .env содержит BOT_TOKEN=<ваш токен>")
bot = telebot.TeleBot(BOT_TOKEN)

# ID администратора (для привилегированных команд), задайте в .env
ADMIN_ID = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID) if ADMIN_ID else None

# ——————————————————————————————————————————————————————
# 2) Загрузка расписания из Excel
#    Файл schedule.xlsx с колонками: Group, Day, Classes
# ——————————————————————————————————————————————————————
SCHEDULE_FILE = "schedule.xlsx"
try:
    schedule_df = pd.read_excel(SCHEDULE_FILE, engine="openpyxl")
    # Переименуем столбцы, если они на русском, в английские для удобства
    rename_map = {}
    if 'Группа' in schedule_df.columns:
        rename_map['Группа'] = 'Group'
    if 'Day' not in schedule_df.columns and 'День' in schedule_df.columns:
        rename_map['День'] = 'Day'
    if 'Classes' not in schedule_df.columns and 'Занятия' in schedule_df.columns:
        rename_map['Занятия'] = 'Classes'
    if rename_map:
        schedule_df.rename(columns=rename_map, inplace=True)
    # Проверяем наличие необходимых колонок
    expected_cols = {'Group', 'Day', 'Classes'}
    if not expected_cols.issubset(schedule_df.columns):
        missing = expected_cols - set(schedule_df.columns)
        raise KeyError(f"В файле {SCHEDULE_FILE} нет столбцов: {', '.join(missing)}")
    # Приводим типы данных к строковым (на случай числовых кодов групп)
    schedule_df['Group']   = schedule_df['Group'].astype(str)
    schedule_df['Day']     = schedule_df['Day'].astype(str)
    schedule_df['Classes'] = schedule_df['Classes'].fillna("").astype(str)
except FileNotFoundError:
    print(f"⚠️ Файл {SCHEDULE_FILE} не найден — расписание недоступно.")
    schedule_df = pd.DataFrame(columns=['Group','Day','Classes'])
except KeyError as e:
    print(f"❌ Ошибка структуры {SCHEDULE_FILE}: {e}")
    schedule_df = pd.DataFrame(columns=['Group','Day','Classes'])

# Функции для получения расписания
def get_week_schedule(group_name: str) -> dict:
    """Получить расписание на неделю по названию группы."""
    df = schedule_df[schedule_df['Group'].str.lower() == group_name.lower()]
    week = {}
    for _, row in df.iterrows():
        week[row['Day']] = row['Classes']
    return week

def get_today_schedule(group_name: str) -> str:
    """Получить расписание на сегодня по группе (возвращает строку занятий)."""
    days_map = {
        0: "Понедельник", 1: "Вторник", 2: "Среда",
        3: "Четверг",   4: "Пятница", 5: "Суббота", 6: "Воскресенье"
    }
    today = days_map[datetime.now().weekday()]
    df = schedule_df[
        (schedule_df['Group'].str.lower() == group_name.lower()) &
        (schedule_df['Day'] == today)
    ]
    return df.iloc[0]['Classes'] if not df.empty else ""

def filter_by_subgroup(text: str, subgroup: int) -> str:
    """Отфильтровать текст расписания по подгруппе (если указана 1 или 2)."""
    if not subgroup or not text:
        return text
    lines_out = []
    for line in text.splitlines():
        if "(1 подгр" in line or "(2 подгр" in line:
            # Оставляем только строки, относящиеся к выбранной подгруппе
            if f"{subgroup} подгр" in line:
                lines_out.append(line)
        else:
            # Строки без указания подгруппы показываем для всех
            lines_out.append(line)
    return "\n".join(lines_out).strip()

# ——————————————————————————————————————————————————————
# 3) Инициализация базы данных (SQLite вместо JSON)
# ——————————————————————————————————————————————————————
DB_FILE = "bot_data.sqlite"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()
# Создаем таблицы, если еще не существуют
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

# Миграция данных из JSON-файлов (если существовали старые данные)
USER_DATA_FILE = "user_data.json"
REQUESTS_FILE = "requests.json"
if os.path.exists(USER_DATA_FILE):
    try:
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            old_users = json.load(f)
        # Вставляем пользователей из JSON в таблицу users
        for uid_str, info in old_users.items():
            uid = int(uid_str)
            # Вставляем, если записи нет (игнорируем, если пользователь уже есть)
            cur.execute("INSERT OR IGNORE INTO users (user_id, group_name, subgroup, notify, reminders) VALUES (?, ?, ?, ?, ?)",
                        (uid, info.get('group'), info.get('subgroup'), 1 if info.get('notify') else 0, 1 if info.get('reminders') else 0))
        conn.commit()
        print(f"Импортировано пользователей из {USER_DATA_FILE}: {len(old_users)}")
    except Exception as e:
        print(f"Ошибка импорта {USER_DATA_FILE}: {e}")

if os.path.exists(REQUESTS_FILE):
    try:
        with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
            old_reqs = json.load(f)
        count = 0
        for uid_str, req_list in old_reqs.items():
            uid = int(uid_str)
            for req in req_list:
                cur.execute("INSERT INTO requests (user_id, type, name, group_name, details, status) VALUES (?, ?, ?, ?, ?, ?)",
                            (uid, req.get('type'), req.get('name'), req.get('group'), req.get('details'), req.get('status')))
                count += 1
        conn.commit()
        print(f"Импортировано заявок из {REQUESTS_FILE}: {count}")
    except Exception as e:
        print(f"Ошибка импорта {REQUESTS_FILE}: {e}")

# Первичное заполнение FAQ и ресурсов, если имеются файлы или нужны стандартные данные
# Загрузка FAQ из faq.json (если файл существует и таблица пуста)
if os.path.exists("faq.json"):
    try:
        with open("faq.json", "r", encoding="utf-8") as f:
            faq_data = json.load(f)
        if isinstance(faq_data, dict) and 'faq' in faq_data:
            faq_items = faq_data['faq']
        elif isinstance(faq_data, list):
            faq_items = faq_data
        else:
            faq_items = []
        for item in faq_items:
            q = item.get('q') or item.get('question')
            a = item.get('a') or item.get('answer')
            if q and a:
                cur.execute("INSERT INTO faq (question, answer) VALUES (?, ?)", (q, a))
        conn.commit()
    except Exception as e:
        print(f"Ошибка импорта faq.json: {e}")

# Заполнение таблицы resources стандартными ссылками (если таблица пока пуста)
cur.execute("SELECT COUNT(*) FROM resources")
if cur.fetchone()[0] == 0:
    resources_defaults = [
        ("📚 Электронная библиотека", "https://library.mgppu.ru"),
        ("🌐 Сайт МГППУ", "https://mgppu.ru"),
        ("🎓 Личный кабинет студента", "https://lk.mgppu.ru"),
        ("💻 ЭИОС (электронная среда)", "https://eios.mgppu.ru")
    ]
    for name, url in resources_defaults:
        cur.execute("INSERT INTO resources (name, url) VALUES (?, ?)", (name, url))
    conn.commit()

# Вспомогательная функция: гарантировать наличие пользователя в БД
def ensure_user(user):
    """Убедиться, что пользователь есть в базе (если нет, добавить с начальными данными)."""
    uid = user.id
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    username = user.username or ""
    # Вставляем пользователя, если его еще нет
    cur.execute("INSERT OR IGNORE INTO users (user_id, first_name, last_name, username, notify, reminders) VALUES (?, ?, ?, ?, 0, 0)",
                (uid, first_name, last_name, username))
    # Обновляем имя/username при каждом обращении (на случай изменения)
    cur.execute("UPDATE users SET first_name=?, last_name=?, username=? WHERE user_id=?",
                (first_name, last_name, username, uid))
    conn.commit()

# Словарь для отображения кодов заявок в человекочитаемые названия
REQUEST_LABELS = {
    "spravka": "Справка",
    "otsrochka": "Отсрочка",
    "hvost": "Пересдача"
}

# ——————————————————————————————————————————————————————
# 4) Обработчики команд пользователя и меню
# ——————————————————————————————————————————————————————
@bot.message_handler(commands=['start'])
def cmd_start(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)  # добавляем пользователя в базу (если новый)
    # Формируем приветственное сообщение с перечнем возможностей
    text = (f"Привет, *{user.first_name or 'студент'}*! Я бот-помощник МГППУ.\n\n"
            "*Команды:*\n"
            "/setgroup <группа> — указать вашу учебную группу\n"
            "/setsub <1|2> — указать вашу подгруппу (если есть)\n"
            "/schedule — расписание на сегодня\n"
            "/week — расписание на неделю\n"
            "/notify — вкл/выкл ежедневные уведомления расписания\n"
            "/reminders — вкл/выкл напоминания о дедлайнах и мотивация\n"
            "/faq — часто задаваемые вопросы\n"
            "/resources — полезные ссылки\n"
            "/spravka — заявка на справку\n"
            "/otsrochka — заявление на отсрочку\n"
            "/hvost — заявка на пересдачу\n"
            "/status — статус ваших заявок\n"
            "/news — последние новости и объявления")
    # Подсказка о вопросах
    text += ("\nТакже вы можете просто написать мне свой вопрос, и он будет передан администрации.")
    # Добавляем раздел для администратора, если текущий пользователь админ
    if ADMIN_ID and uid == ADMIN_ID:
        text += ("\n\n*Администратор:* "
                 "/anons — разослать объявление всем пользователям\n"
                 "/addnews — добавить новость/объявление\n"
                 "/addfaq — добавить FAQ\n"
                 "/delfaq <id> — удалить FAQ по ID\n"
                 "/addresource — добавить ссылку\n"
                 "/delresource <id> — удалить ссылку\n"
                 "/questions — непрочитанные вопросы пользователей\n"
                 "/answer <id> — ответить на вопрос\n"
                 "/stats — статистика использования")
    # Отправляем приветствие и основное меню
    bot.send_message(uid, text, parse_mode="Markdown")
    # Создаем и отправляем клавиатуру с основными кнопками меню
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("📅 Расписание (сегодня)", "📅 Расписание (неделя)")
    keyboard.row("📰 Новости", "❓ FAQ", "📖 Ресурсы")
    keyboard.row("📝 Подать заявку", "📋 Мои заявки")
    keyboard.row("💬 Задать вопрос", "👤 Мой профиль")
    bot.send_message(uid, "Выберите действие на клавиатуре ниже:", reply_markup=keyboard)

@bot.message_handler(commands=['setgroup'])
def cmd_setgroup(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(m, "Используйте: /setgroup <код_группы>, например /setgroup ПИ-21")
    grp = parts[1].strip()
    # Обновляем группу пользователя в БД
    cur.execute("UPDATE users SET group_name=?, subgroup=NULL WHERE user_id=?", (grp, uid))
    conn.commit()
    bot.reply_to(m, f"Группа установлена: *{grp}*", parse_mode="Markdown")

@bot.message_handler(commands=['setsub'])
def cmd_setsub(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or parts[1] not in ("1", "2"):
        return bot.reply_to(m, "Используйте: /setsub 1 или /setsub 2")
    sub = int(parts[1])
    cur.execute("UPDATE users SET subgroup=? WHERE user_id=?", (sub, uid))
    conn.commit()
    bot.reply_to(m, f"Подгруппа установлена: *{sub}*", parse_mode="Markdown")
    
@bot.message_handler(commands=['schedule'])
def cmd_schedule(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    # Получаем группу и подгруппу из БД
    cur.execute("SELECT group_name, subgroup FROM users WHERE user_id = ?", (uid,))
    row = cur.fetchone()
    grp, sub = row if row else (None, None)

    if not grp:
        return bot.reply_to(m, "Сначала укажите группу — /setgroup <код_группы>.")
    if sub is None:
        return bot.reply_to(m, "Сначала укажите подгруппу — /setsub 1 или /setsub 2.")

    # Берём полное расписание на сегодня и фильтруем подгруппу
    txt = get_today_schedule(grp)
    if not txt:
        return bot.send_message(uid, f"На сегодня для группы *{grp}* занятий нет.", parse_mode="Markdown")
    filtered = filter_by_subgroup(txt, sub)

    bot.send_message(
        uid,
        f"*Расписание на сегодня ({grp}, подгруппа {sub}):*\n{filtered}",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['week'])
def cmd_week(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    # Получаем группу и подгруппу из БД
    cur.execute("SELECT group_name, subgroup FROM users WHERE user_id = ?", (uid,))
    row = cur.fetchone()
    grp, sub = row if row else (None, None)

    if not grp:
        return bot.reply_to(m, "Сначала укажите группу — /setgroup <код_группы>.")
    if sub is None:
        return bot.reply_to(m, "Сначала укажите подгруппу — /setsub 1 или /setsub 2.")

    week = get_week_schedule(grp)
    if not week:
        return bot.send_message(uid, "Расписание на неделю не найдено.", parse_mode="Markdown")

    lines = [f"*Расписание на неделю ({grp}, подгруппа {sub}):*"]
    order = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота"]
    for d in order:
        cls = week.get(d, "")
        if cls:
            cls = filter_by_subgroup(cls, sub)
        else:
            cls = "_(нет занятий)_"
        lines.append(f"\n*{d}:*\n{cls}")

    bot.send_message(uid, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=['notify'])
def cmd_notify(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    # Переключаем флаг уведомлений
    cur.execute("SELECT notify FROM users WHERE user_id=?", (uid,))
    current = cur.fetchone()
    new_state = 1
    if current:
        new_state = 0 if current[0] else 1
    cur.execute("UPDATE users SET notify=? WHERE user_id=?", (new_state, uid))
    conn.commit()
    state_text = "включены" if new_state else "отключены"
    bot.reply_to(m, f"Ежедневные уведомления расписания {state_text}.", parse_mode="Markdown")

@bot.message_handler(commands=['reminders'])
def cmd_reminders(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    cur.execute("SELECT reminders FROM users WHERE user_id=?", (uid,))
    current = cur.fetchone()
    new_state = 1
    if current:
        new_state = 0 if current[0] else 1
    cur.execute("UPDATE users SET reminders=? WHERE user_id=?", (new_state, uid))
    conn.commit()
    state_text = "включены" if new_state else "отключены"
    bot.reply_to(m, f"Учебные напоминания {state_text}.", parse_mode="Markdown")

@bot.message_handler(commands=['faq'])
def cmd_faq(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    # Получаем все FAQ из базы
    cur.execute("SELECT question, answer FROM faq")
    faq_list = cur.fetchall()
    if not faq_list:
        return bot.send_message(uid, "FAQ недоступен или пока пуст.")
    text = "*Часто задаваемые вопросы:*"
    for i, (q, a) in enumerate(faq_list, start=1):
        text += f"\n\n*{i}. {q}*\n_{a}_"
    bot.send_message(uid, text, parse_mode="Markdown")

@bot.message_handler(commands=['resources'])
def cmd_resources(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    # Получаем все ресурсы из базы
    cur.execute("SELECT name, url FROM resources")
    res_list = cur.fetchall()
    if not res_list:
        return bot.send_message(uid, "Список ресурсов пуст.")
    text = "*Полезные ресурсы:*"
    for name, url in res_list:
        text += f"\n{name}: {url}"
    bot.send_message(uid, text, parse_mode="Markdown")

# ——————————————————————————————————————————————————————
# 5) Функции подачи заявок (справка, отсрочка, пересдача)
# ——————————————————————————————————————————————————————
# Временное хранилище данных в процессе заполнения заявки
temp_request = {}

@bot.message_handler(commands=['spravka'])
def cmd_spravka(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    bot.send_message(uid, "Оформление справки.\n1⃣ Введите *ФИО*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, spravka_name_step)

def spravka_name_step(m):
    uid = m.chat.id
    temp_request[uid] = {"type": "spravka", "name": m.text.strip()}
    bot.send_message(uid, "2⃣ Укажите *группу*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, spravka_group_step)

def spravka_group_step(m):
    uid = m.chat.id
    if uid in temp_request:
        temp_request[uid]["group"] = m.text.strip()
    else:
        temp_request[uid] = {"group": m.text.strip()}
    bot.send_message(uid, "3⃣ Укажите *тип справки*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, spravka_type_step)

def spravka_type_step(m):
    uid = m.chat.id
    if uid not in temp_request:
        temp_request[uid] = {}
    temp_request[uid]["details"] = m.text.strip()
    req = temp_request.pop(uid)
    # Сохраняем заявку в базу данных
    cur.execute("INSERT INTO requests (user_id, type, name, group_name, details, status) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, "spravka", req.get("name"), req.get("group"), req.get("details"), "Принята"))
    conn.commit()
    bot.send_message(uid,
                     f"✅ Заявка на справку принята!\n"
                     f"ФИО: {req['name']}\nГруппа: {req['group']}\nТип справки: {req['details']}\n\n"
                     "Статус заявки можно посмотреть командой /status.",
                     parse_mode="Markdown")

@bot.message_handler(commands=['otsrochka'])
def cmd_otsrochka(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    bot.send_message(uid, "Оформление заявления на отсрочку.\n1⃣ Введите *ФИО*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, ots_name_step)

def ots_name_step(m):
    uid = m.chat.id
    temp_request[uid] = {"type": "otsrochka", "name": m.text.strip()}
    bot.send_message(uid, "2⃣ Укажите *группу*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, ots_group_step)

def ots_group_step(m):
    uid = m.chat.id
    if uid in temp_request:
        temp_request[uid]["group"] = m.text.strip()
    else:
        temp_request[uid] = {"group": m.text.strip()}
    bot.send_message(uid, "3⃣ Укажите *причину отсрочки*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, ots_reason_step)

def ots_reason_step(m):
    uid = m.chat.id
    if uid not in temp_request:
        temp_request[uid] = {}
    temp_request[uid]["details"] = m.text.strip()
    req = temp_request.pop(uid)
    cur.execute("INSERT INTO requests (user_id, type, name, group_name, details, status) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, "otsrochka", req.get("name"), req.get("group"), req.get("details"), "Принята"))
    conn.commit()
    bot.send_message(uid,
                     f"✅ Заявление на отсрочку принято!\n"
                     f"ФИО: {req['name']}\nГруппа: {req['group']}\nПричина: {req['details']}\n\n"
                     "Статус заявки можно проверить командой /status.",
                     parse_mode="Markdown")

@bot.message_handler(commands=['hvost'])
def cmd_hvost(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    bot.send_message(uid, "Оформление заявки на пересдачу.\n1⃣ Введите *ФИО*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, hvost_name_step)

def hvost_name_step(m):
    uid = m.chat.id
    temp_request[uid] = {"type": "hvost", "name": m.text.strip()}
    bot.send_message(uid, "2⃣ Укажите *группу*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, hvost_group_step)

def hvost_group_step(m):
    uid = m.chat.id
    if uid in temp_request:
        temp_request[uid]["group"] = m.text.strip()
    else:
        temp_request[uid] = {"group": m.text.strip()}
    bot.send_message(uid, "3⃣ Укажите *дисциплину для пересдачи*:", parse_mode="Markdown")
    bot.register_next_step_handler(m, hvost_subject_step)

def hvost_subject_step(m):
    uid = m.chat.id
    if uid not in temp_request:
        temp_request[uid] = {}
    temp_request[uid]["details"] = m.text.strip()
    req = temp_request.pop(uid)
    cur.execute("INSERT INTO requests (user_id, type, name, group_name, details, status) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, "hvost", req.get("name"), req.get("group"), req.get("details"), "Принята"))
    conn.commit()
    bot.send_message(uid,
                     f"✅ Заявка на пересдачу принята!\n"
                     f"ФИО: {req['name']}\nГруппа: {req['group']}\nДисциплина: {req['details']}\n\n"
                     "Статус заявки можно проверить командой /status.",
                     parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def cmd_status(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    cur.execute("SELECT type, details, status FROM requests WHERE user_id=?", (uid,))
    requests_list = cur.fetchall()
    if not requests_list:
        return bot.reply_to(m, "У вас нет отправленных заявок.")
    text = "*Статус ваших заявок:*\n"
    for req_type, details, status in requests_list:
        label = REQUEST_LABELS.get(req_type, req_type)
        text += f"– {label} ({details}): {status}\n"
    bot.send_message(uid, text, parse_mode="Markdown")

# ——————————————————————————————————————————————————————
# 6) Администраторские команды (новости, рассылка, ответы)
# ——————————————————————————————————————————————————————
@bot.message_handler(commands=['news'])
def cmd_news(m):
    uid = m.chat.id
    user = m.from_user
    ensure_user(user)
    # Показываем список новостей/объявлений
    cur.execute("SELECT content, datetime(created_at, 'localtime') FROM news ORDER BY created_at DESC")
    news_list = cur.fetchall()
    if not news_list:
        return bot.send_message(uid, "Новостей пока нет.")
    text = "*Новости и объявления:*"
    for content, dt in news_list:
        # Форматируем дату для отображения (YYYY-MM-DD HH:MM:SS -> DD.MM.YYYY)
        try:
            date_obj = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
            date_str = date_obj.strftime("%d.%m.%Y")
        except:
            date_str = dt.split(" ")[0]
        text += f"\n[{date_str}] {content}"
    bot.send_message(uid, text, parse_mode="Markdown")

@bot.message_handler(commands=['addnews'])
def cmd_addnews(m):
    # Только админ
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        # Если текст новости не указан в команде, запрашиваем следующим сообщением
        msg = bot.reply_to(m, "Введите текст новости/объявления:")
        bot.register_next_step_handler(msg, addnews_step)
    else:
        content = parts[1].strip()
        if content:
            # Добавляем новость и отправляем ее пользователям
            add_news_and_broadcast(content)
        else:
            bot.reply_to(m, "Текст новости не должен быть пустым.")

def addnews_step(m):
    # Обработка следующего шага ввода текста новости (для admin)
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    content = m.text.strip()
    if not content:
        return bot.reply_to(m, "Текст новости не должен быть пустым.")
    add_news_and_broadcast(content)

def add_news_and_broadcast(content: str):
    """Добавить новость в базу и разослать всем пользователям."""
    # Сохранить новость в БД
    cur.execute("INSERT INTO news (content) VALUES (?)", (content,))
    conn.commit()
    # Рассылка новости всем пользователям
    cur.execute("SELECT user_id FROM users")
    all_users = cur.fetchall()
    for (user_id,) in all_users:
        try:
            bot.send_message(user_id, f"📢 *Новое объявление:* {content}", parse_mode="Markdown")
        except Exception as e:
            # Игнорируем ошибки отправки (например, пользователь остановил бота)
            continue

@bot.message_handler(commands=['anons', 'broadcast'])
def cmd_anons(m):
    # Команда рассылки произвольного объявления всем пользователям (только админ)
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        # Нет текста – попросим в следующем сообщении
        msg = bot.reply_to(m, "Введите текст объявления для рассылки всем пользователям:")
        bot.register_next_step_handler(msg, anons_step)
    else:
        announcement = parts[1].strip()
        if announcement:
            broadcast_message(announcement)
        else:
            bot.reply_to(m, "Текст объявления не должен быть пустым.")

def anons_step(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    announcement = m.text.strip()
    if not announcement:
        return bot.reply_to(m, "Текст объявления не должен быть пустым.")
    broadcast_message(announcement)

def broadcast_message(text: str):
    """Разослать всем пользователям заданный текст."""
    cur.execute("SELECT user_id FROM users")
    all_users = cur.fetchall()
    count = 0
    for (user_id,) in all_users:
        try:
            bot.send_message(user_id, text)
            count += 1
        except:
            continue
    # Отправляем администратору итог
    if ADMIN_ID:
        bot.send_message(ADMIN_ID, f"Отправлено объявление {count} пользователям.")

@bot.message_handler(commands=['addfaq'])
def cmd_addfaq(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    # Запрашиваем у администратора новый вопрос для FAQ
    msg = bot.reply_to(m, "Введите новый вопрос (FAQ):")
    bot.register_next_step_handler(msg, addfaq_question_step)

def addfaq_question_step(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    question_text = m.text.strip()
    if not question_text:
        return bot.reply_to(m, "Вопрос не должен быть пустым.")
    # Сохраняем временно вопрос и спрашиваем ответ
    temp_request[m.chat.id] = {"faq_q": question_text}
    msg = bot.reply_to(m, "Введите ответ на этот вопрос:")
    bot.register_next_step_handler(msg, addfaq_answer_step)

def addfaq_answer_step(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    answer_text = m.text.strip()
    if not answer_text:
        return bot.reply_to(m, "Ответ не должен быть пустым.")
    data = temp_request.get(m.chat.id)
    if not data or "faq_q" not in data:
        return bot.reply_to(m, "Ошибка: не найден временный вопрос.")
    question_text = data["faq_q"]
    # Удаляем временный сохраненный вопрос
    temp_request.pop(m.chat.id, None)
    # Добавляем в базу
    cur.execute("INSERT INTO faq (question, answer) VALUES (?, ?)", (question_text, answer_text))
    conn.commit()
    bot.send_message(m.chat.id, f"✅ FAQ добавлен: {question_text} – {answer_text}")

@bot.message_handler(commands=['delfaq'])
def cmd_delfaq(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        return bot.reply_to(m, "Используйте: /delfaq <ID>")
    faq_id = int(parts[1])
    cur.execute("DELETE FROM faq WHERE id=?", (faq_id,))
    conn.commit()
    if cur.rowcount:
        bot.reply_to(m, f"FAQ с ID {faq_id} удален.")
    else:
        bot.reply_to(m, f"FAQ с ID {faq_id} не найден.")

@bot.message_handler(commands=['addresource'])
def cmd_addresource(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    msg = bot.reply_to(m, "Введите название ресурса:")
    bot.register_next_step_handler(msg, addres_name_step)

def addres_name_step(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    name = m.text.strip()
    if not name:
        return bot.reply_to(m, "Название не должно быть пустым.")
    temp_request[m.chat.id] = {"res_name": name}
    msg = bot.reply_to(m, "Введите URL ресурса:")
    bot.register_next_step_handler(msg, addres_url_step)

def addres_url_step(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    url = m.text.strip()
    if not url:
        return bot.reply_to(m, "URL не должен быть пустым.")
    data = temp_request.get(m.chat.id)
    if not data or "res_name" not in data:
        return bot.reply_to(m, "Ошибка: не найден временно сохраненный ресурс.")
    name = data["res_name"]
    temp_request.pop(m.chat.id, None)
    cur.execute("INSERT INTO resources (name, url) VALUES (?, ?)", (name, url))
    conn.commit()
    bot.send_message(m.chat.id, f"✅ Ресурс добавлен: {name} – {url}")

@bot.message_handler(commands=['delresource'])
def cmd_delresource(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        return bot.reply_to(m, "Используйте: /delresource <ID>")
    res_id = int(parts[1])
    cur.execute("DELETE FROM resources WHERE id=?", (res_id,))
    conn.commit()
    if cur.rowcount:
        bot.reply_to(m, f"Ресурс с ID {res_id} удален.")
    else:
        bot.reply_to(m, f"Ресурс с ID {res_id} не найден.")

@bot.message_handler(commands=['questions'])
def cmd_questions(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    # Получаем все вопросы без ответа
    cur.execute("SELECT q.id, u.first_name, q.question, datetime(q.asked_at, 'localtime') "
                "FROM questions q LEFT JOIN users u ON q.user_id = u.user_id "
                "WHERE q.answered = 0")
    questions_list = cur.fetchall()
    if not questions_list:
        return bot.send_message(m.chat.id, "Нет новых вопросов от пользователей.")
    text = "*Вопросы от пользователей:*"
    for qid, first_name, question, asked_dt in questions_list:
        name = first_name or "Пользователь"
        try:
            dt_obj = datetime.strptime(asked_dt, "%Y-%m-%d %H:%M:%S")
            dt_str = dt_obj.strftime("%d.%m.%Y %H:%M")
        except:
            dt_str = asked_dt
        text += f"\nID{qid} от {name} ({dt_str}): {question}"
    bot.send_message(m.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['answer'])
def cmd_answer(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=2)
    if len(parts) < 2:
        return bot.reply_to(m, "Используйте: /answer <ID> <текст ответа>")
    # parts[1] должен быть ID вопроса
    if not parts[1].isdigit():
        return bot.reply_to(m, "Неверный формат ID.")
    qid = int(parts[1])
    # Если ответ сразу указан в команде (parts[2]), то берем его
    if len(parts) >= 3:
        answer_text = parts[2].strip()
        if not answer_text:
            return bot.reply_to(m, "Текст ответа не должен быть пустым.")
        send_answer_to_user(qid, answer_text)
    else:
        # Иначе попросим отправить ответ отдельным сообщением
        temp_request[m.chat.id] = {"answer_qid": qid}
        msg = bot.reply_to(m, f"Введите ответ на вопрос ID{qid}:")
        bot.register_next_step_handler(msg, answer_text_step)

def answer_text_step(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    answer_text = m.text.strip()
    if not answer_text:
        return bot.reply_to(m, "Ответ не должен быть пустым.")
    data = temp_request.get(m.chat.id)
    if not data or "answer_qid" not in data:
        return bot.reply_to(m, "Ошибка: не выбран вопрос для ответа.")
    qid = data["answer_qid"]
    temp_request.pop(m.chat.id, None)
    send_answer_to_user(qid, answer_text)

def send_answer_to_user(qid: int, answer_text: str):
    """Отправить ответ на вопрос с заданным ID пользователю и отметить как отвеченный."""
    # Ищем вопрос по ID
    cur.execute("SELECT user_id, question FROM questions WHERE id=? AND answered=0", (qid,))
    row = cur.fetchone()
    if not row:
        return bot.send_message(ADMIN_ID, f"Вопрос ID{qid} не найден или уже закрыт.")
    user_id, question_text = row
    # Отмечаем как отвеченный в базе
    cur.execute("UPDATE questions SET answered=1, answer=?, answered_at=datetime('now') WHERE id=?", (answer_text, qid))
    conn.commit()
    # Отправляем ответ пользователю
    try:
        bot.send_message(user_id, f"✉️ Ответ на ваш вопрос \"{question_text}\":\n{answer_text}")
        bot.send_message(ADMIN_ID, f"Ответ пользователю {user_id} отправлен.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"Не удалось доставить ответ пользователю {user_id}. Возможно, он остановил бота.")

@bot.message_handler(commands=['stats'])
def cmd_stats(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    # Количество пользователей
    cur.execute("SELECT COUNT(*) FROM users")
    users_count = cur.fetchone()[0]
    # Количество заявок (и по типам)
    cur.execute("SELECT COUNT(*) FROM requests")
    requests_total = cur.fetchone()[0]
    cur.execute("SELECT type, COUNT(*) FROM requests GROUP BY type")
    req_by_type = {t: c for t, c in cur.fetchall()}
    spr_count = req_by_type.get("spravka", 0)
    ots_count = req_by_type.get("otsrochka", 0)
    hvost_count = req_by_type.get("hvost", 0)
    # Количество вопросов (всего и неотвеченных)
    cur.execute("SELECT COUNT(*), SUM(CASE WHEN answered=0 THEN 1 ELSE 0 END) FROM questions")
    q_total, q_open = cur.fetchone()
    q_total = q_total or 0
    q_open = q_open or 0
    # Количество новостей
    cur.execute("SELECT COUNT(*) FROM news")
    news_count = cur.fetchone()[0]
    # Количество FAQ и ресурсов
    cur.execute("SELECT COUNT(*) FROM faq")
    faq_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM resources")
    res_count = cur.fetchone()[0]
    # Формируем текст отчета
    text = "*Статистика использования:*\n"
    text += f"Пользователей: {users_count}\n"
    text += f"Отправлено заявок: {requests_total} "
    text += f"(Справок: {spr_count}, Отсрочек: {ots_count}, Пересдач: {hvost_count})\n"
    text += f"Вопросов получено: {q_total} (из них без ответа: {q_open})\n"
    text += f"Новостей опубликовано: {news_count}\n"
    text += f"FAQ записей: {faq_count}, ресурсов: {res_count}"
    bot.send_message(m.chat.id, text, parse_mode="Markdown")

# ——————————————————————————————————————————————————————
# 7) Обработчики кнопок меню (ReplyKeyboard)
# ——————————————————————————————————————————————————————
@bot.message_handler(func=lambda m: m.text == "📅 Расписание (сегодня)")
def menu_today(m):
    # Прямой вызов функционала /schedule
    cmd_schedule(m)

@bot.message_handler(func=lambda m: m.text == "📅 Расписание (неделя)")
def menu_week(m):
    cmd_week(m)

@bot.message_handler(func=lambda m: m.text == "📰 Новости")
def menu_news(m):
    cmd_news(m)

@bot.message_handler(func=lambda m: m.text == "❓ FAQ")
def menu_faq(m):
    cmd_faq(m)

@bot.message_handler(func=lambda m: m.text == "📖 Ресурсы")
def menu_resources(m):
    cmd_resources(m)

@bot.message_handler(func=lambda m: m.text == "📝 Подать заявку")
def menu_request(m):
    # При нажатии "Подать заявку" предлагаем выбрать тип заявки через Inline-кнопки
    uid = m.chat.id
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("Справка", callback_data="req_spravka"))
    kb.add(telebot.types.InlineKeyboardButton("Отсрочка", callback_data="req_otsrochka"))
    kb.add(telebot.types.InlineKeyboardButton("Пересдача", callback_data="req_hvost"))
    bot.send_message(uid, "Выберите тип заявки:", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "📋 Мои заявки")
def menu_status(m):
    cmd_status(m)

@bot.message_handler(func=lambda m: m.text == "💬 Задать вопрос")
def menu_question(m):
    uid = m.chat.id
    bot.send_message(uid, "Напишите свой вопрос в ответном сообщении, и он будет сохранен для последующего ответа.")

@bot.message_handler(func=lambda m: m.text == "👤 Мой профиль")
def menu_profile(m):
    uid = m.chat.id
    cur.execute("SELECT group_name, subgroup, notify, reminders FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        return bot.send_message(uid, "Данные профиля не найдены.")
    grp, sub, notify_flag, rem_flag = row
    grp = grp or "<не указана>"
    sub = sub if sub else "<нет>"
    notify_text = "включены" if notify_flag else "отключены"
    rem_text = "включены" if rem_flag else "отключены"
    text = ("*Ваш профиль:*\n"
            f"Группа: {grp}\n"
            f"Подгруппа: {sub}\n"
            f"Уведомления расписания: {notify_text}\n"
            f"Учебные напоминания: {rem_text}")
    bot.send_message(uid, text, parse_mode="Markdown")

# Обработчик inline-кнопок для выбора типа заявки
@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("req_"))
def callback_request_type(call):
    uid = call.message.chat.id
    if call.data == "req_spravka":
        # Начинаем процесс заявки на справку
        bot.delete_message(uid, call.message.message_id)  # удаляем сообщение с выбором
        bot.answer_callback_query(call.id, "Выбрано: Справка")
        bot.send_message(uid, "Оформление справки.\n1⃣ Введите *ФИО*:", parse_mode="Markdown")
        # Используем существующее сообщение для привязки следующего шага
        dummy = call.message  # используем call.message как ссылку на чат
        bot.register_next_step_handler(dummy, spravka_name_step)
    elif call.data == "req_otsrochka":
        bot.delete_message(uid, call.message.message_id)
        bot.answer_callback_query(call.id, "Выбрано: Отсрочка")
        bot.send_message(uid, "Оформление заявления на отсрочку.\n1⃣ Введите *ФИО*:", parse_mode="Markdown")
        dummy = call.message
        bot.register_next_step_handler(dummy, ots_name_step)
    elif call.data == "req_hvost":
        bot.delete_message(uid, call.message.message_id)
        bot.answer_callback_query(call.id, "Выбрано: Пересдача")
        bot.send_message(uid, "Оформление заявки на пересдачу.\n1⃣ Введите *ФИО*:", parse_mode="Markdown")
        dummy = call.message
        bot.register_next_step_handler(dummy, hvost_name_step)

# ——————————————————————————————————————————————————————
# 8) Логирование вопросов пользователей
# ——————————————————————————————————————————————————————
@bot.message_handler(func=lambda message: True, content_types=['text'])
def catch_all_text(m):
    # Перехватываем любые текстовые сообщения, не обработанные командами выше
    if m.chat.type != "private":
        return  # игнорируем сообщения в группах/каналах
    # Пропускаем сообщения администратора (чтобы не логировать его ответы или команды)
    if ADMIN_ID and m.chat.id == ADMIN_ID:
        return
    # Если это команда, но она не распознана, уведомим (не записываем как вопрос)
    if m.text.startswith('/'):
        return bot.send_message(m.chat.id, "Неизвестная команда. Используйте /start для списка команд.")
    # Сохраняем вопрос пользователя в базу данных
    question_text = m.text.strip()
    if not question_text:
        return
    cur.execute("INSERT INTO questions (user_id, question) VALUES (?, ?)", (m.chat.id, question_text))
    conn.commit()
    bot.reply_to(m, "✅ Ваш вопрос отправлен. Мы ответим на него в ближайшее время.")

# ——————————————————————————————————————————————————————
# 9) Ежедневные рассылки (расписание и напоминания)
# ——————————————————————————————————————————————————————
def send_daily_schedule():
    """Ежедневная отправка расписания на сегодня (08:00) всем, кто включил notify."""
    # Получаем всех пользователей с notify=1
    cur.execute("SELECT user_id, group_name, subgroup FROM users WHERE notify=1 AND group_name IS NOT NULL")
    users_list = cur.fetchall()
    for user_id, group_name, sub in users_list:
        classes_today = get_today_schedule(group_name)
        if not classes_today:
            continue
        classes_today = filter_by_subgroup(classes_today, sub)
        try:
            bot.send_message(user_id, f"*Расписание на сегодня ({group_name}):*\n{classes_today}", parse_mode="Markdown")
        except:
            continue

def send_daily_reminders():
    """Ежедневная отправка дедлайнов/мотивации (09:00) всем, кто включил reminders."""
    try:
        data = json.load(open("reminders.json", "r", encoding="utf-8"))
    except FileNotFoundError:
        return
    today_str = datetime.now().strftime("%Y-%m-%d")
    # Собираем события на сегодня
    reminders_list = []
    # дедлайны
    for ev in data.get('deadlines', []):
        if ev.get('date') == today_str:
            reminders_list.append(f"– {ev['message']}")
    # мотивация
    wd = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][datetime.now().weekday()]
    mot_list = data.get('motivation', {}).get(wd) or data.get('motivation', {}).get('Any', [])
    mot_message = ""
    if mot_list:
        import random
        mot_message = random.choice(mot_list)
    # Формируем текст напоминания
    text = ""
    if reminders_list:
        text += "📌 *Напоминания:*\n" + "\n".join(reminders_list)
    if mot_message:
        text += ("\n\n" if text else "") + f"💡 *Мотивация:* {mot_message}"
    if not text:
        return
    # Отправляем всем с reminders=1
    cur.execute("SELECT user_id FROM users WHERE reminders=1")
    users_to_remind = cur.fetchall()
    for (user_id,) in users_to_remind:
        try:
            bot.send_message(user_id, text, parse_mode="Markdown")
        except:
            continue

# Планируем ежедневные задачи (используем библиотеку schedule)
schedule.every().day.at("08:00").do(send_daily_schedule)
schedule.every().day.at("09:00").do(send_daily_reminders)

# Запуск потока для регулярного выполнения задач schedule
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(60)

threading.Thread(target=run_scheduler, daemon=True).start()

# ——————————————————————————————————————————————————————
# 10) Запуск бота
# ——————————————————————————————————————————————————————
print("Бот запущен...")
bot.polling(none_stop=True)
