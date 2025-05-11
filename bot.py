import os
import json
import threading
import schedule
import time
from datetime import datetime

import pandas as pd
import telebot
from dotenv import load_dotenv

import db  # наш модуль с базой данных

# ——————————————————————————————————————————————————————
# 1) Настройка и загрузка токена
# ——————————————————————————————————————————————————————
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise Exception("Не найден токен BOT_TOKEN. Убедитесь, что .env содержит BOT_TOKEN=<ваш токен>")
bot = telebot.TeleBot(BOT_TOKEN)

# ID администратора (для привилегированных команд), задается в .env
ADMIN_ID = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID) if ADMIN_ID else None

# Игнорируем все стикеры
@bot.message_handler(content_types=['sticker'])
def handle_sticker(m):
    return

# ——————————————————————————————————————————————————————
# 2) Загрузка расписания из Excel
# ——————————————————————————————————————————————————————
SCHEDULE_FILE = "schedule.xlsx"
try:
    schedule_df = pd.read_excel(SCHEDULE_FILE, engine="openpyxl", sheet_name="Schedule")
    # Убираем пробелы вокруг имён столбцов
    schedule_df.columns = schedule_df.columns.str.strip()
    # Проверяем, что теперь есть все нужные
    expected = {'Group','Day','Time','Subgroup','Class'}
    missing = expected - set(schedule_df.columns)
    if missing:
        raise KeyError(f"В файле {SCHEDULE_FILE} нет столбцов: {', '.join(missing)}")
    # Для удобства: пустые Subgroup → NaN → оставить как есть
    schedule_df['Subgroup'] = schedule_df['Subgroup'].fillna(0).astype(int)
except FileNotFoundError:
    print(f"⚠️ Файл {SCHEDULE_FILE} не найден — расписание недоступно.")
    schedule_df = pd.DataFrame(columns=['Group','Day','Time','Subgroup','Class'])
except KeyError as e:
    print(f"❌ Ошибка структуры {SCHEDULE_FILE}: {e}")
    schedule_df = pd.DataFrame(columns=['Group','Day','Time','Subgroup','Class'])

# Функции для получения расписания
def get_today_schedule(group_name: str, subgroup: int) -> str:
    days_map = {
        0: "Понедельник",1: "Вторник",2: "Среда",
        3: "Четверг",4: "Пятница",5: "Суббота",6: "Воскресенье"
    }
    today = days_map[datetime.now().weekday()]
    df = schedule_df[
        (schedule_df['Group'].str.lower() == group_name.lower()) &
        (schedule_df['Day'] == today)
    ]
    # Фильтруем по подгруппе: показываем общие (Subgroup==0) и нужную подгруппу
    df = df[(df['Subgroup'] == 0) | (df['Subgroup'] == subgroup)]
    # Сортируем по времени
    df = df.sort_values('Time')
    # Формируем строки: "08:30-10:00  Математический анализ"
    lines = [f"{row.Time}  {row.Class}" for _, row in df.iterrows()]
    return "\n".join(lines)

def get_week_schedule(group_name: str, subgroup: int) -> dict:
    result = {}
    for day in ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота"]:
        df = schedule_df[
            (schedule_df['Group'].str.lower() == group_name.lower()) &
            (schedule_df['Day'] == day)
        ]
        df = df[(df['Subgroup'] == 0) | (df['Subgroup'] == subgroup)]
        df = df.sort_values('Time')
        lines = [f"{row.Time}  {row.Class}" for _, row in df.iterrows()]
        result[day] = "\n".join(lines) if lines else ""
    return result

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
# 3) Инициализация базы данных (вынесено в db.py)
# ——————————————————————————————————————————————————————
# База данных автоматически создается и заполняется примерными данными при первом запуске (см. db.py)

# Словарь для отображения кодов заявок в понятные названия
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
    db.ensure_user(user)  # добавить пользователя в базу (если новый)
    text = (f"Привет, *{user.first_name or 'студент'}*! Я бот-помощник МГППУ.\n\n"
            "*Команды:*\n"
            "/setgroup <группа> — указать вашу учебную группу\n"
            "/setsub <1|2> — указать вашу подгруппу (если есть)\n"
            "/schedule — расписание на сегодня\n"
            "/week — расписание на неделю\n"
            "/notify — вкл/выкл ежедневные уведомления расписания\n"
            "/reminders — вкл/выкл напоминания о дедлайнах и мотивации\n"
            "/faq — часто задаваемые вопросы\n"
            "/resources — полезные ссылки\n"
            "/spravka — заявка на справку\n"
            "/otsrochka — заявление на отсрочку\n"
            "/hvost — заявка на пересдачу\n"
            "/status — статус ваших заявок\n"
            "/news — последние новости и объявления")
    text += ("\nТакже вы можете просто написать мне свой вопрос, и он будет передан администрации.")
    if ADMIN_ID and uid == ADMIN_ID:
        text += ("\n\n*Администратор:* "
                 "/anons — разослать объявление всем пользователям\n"
                 "/addnews — добавить новость/объявление\n"
                 "/addfaq — добавить FAQ\n"
                 "/delnews <id> — удалить новость по ID\n"
                 "/delfaq <id> — удалить FAQ по ID\n"
                 "/addresource — добавить ресурс\n"
                 "/delresource <id> — удалить ресурс\n"
                 "/list — посмотреть все категории\n"
                 "/questions — непрочитанные вопросы пользователей\n"
                 "/answer <id> — ответить на вопрос\n"
                 "/stats — статистика использования")
    bot.send_message(uid, text, parse_mode="Markdown")
    # Клавиатура с основными действиями
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
    db.ensure_user(user)
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(m, "Используйте: /setgroup <код_группы>, например /setgroup ПИ-21")
    grp = parts[1].strip()
    db.update_user_group(uid, grp)
    bot.reply_to(m, f"Группа установлена: *{grp}*", parse_mode="Markdown")

@bot.message_handler(commands=['setsub'])
def cmd_setsub(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or parts[1] not in ("1", "2"):
        return bot.reply_to(m, "Используйте: /setsub 1 или /setsub 2")
    sub = int(parts[1])
    db.update_user_subgroup(uid, sub)
    bot.reply_to(m, f"Подгруппа установлена: *{sub}*", parse_mode="Markdown")

@bot.message_handler(commands=['schedule'])
def cmd_schedule(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
    grp, sub = db.get_user_group_sub(uid) or (None, None)
    if not grp:
        return bot.reply_to(m, "Сначала укажите группу — /setgroup <код_группы>.")
    if sub is None:
        return bot.reply_to(m, "Сначала укажите подгруппу — /setsub 1 или 2.")
    # Получаем расписание на сегодня с учётом времени и подгруппы
    classes_today = get_today_schedule(grp, sub)
    if not classes_today:
        return bot.send_message(uid, f"У вас нет занятий сегодня ({grp}, подгруппа {sub}).", parse_mode="Markdown")
    bot.send_message(
        uid,
        f"*Расписание на сегодня ({grp}, подгруппа {sub}):*\n{classes_today}",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['week'])
def cmd_week(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
    grp, sub = db.get_user_group_sub(uid) or (None, None)
    if not grp:
        return bot.reply_to(m, "Сначала укажите группу — /setgroup <код_группы>.")
    if sub is None:
        return bot.reply_to(m, "Сначала укажите подгруппу — /setsub 1 или 2.")
    # Получаем расписание на всю неделю
    week = get_week_schedule(grp, sub)
    # Проверим, есть ли хоть одно занятие
    if not any(week.values()):
        return bot.send_message(uid, "Расписание на неделю не найдено.", parse_mode="Markdown")
    lines = [f"*Расписание на неделю ({grp}, подгруппа {sub}):*"]
    order = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота"]
    for day in order:
        cls = week.get(day) or "_(нет занятий)_"
        lines.append(f"\n*{day}:*\n{cls}")
    bot.send_message(uid, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=['notify'])
def cmd_notify(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
    new_state = db.toggle_notify(uid)
    state_text = "включены" if new_state else "отключены"
    bot.reply_to(m, f"Ежедневные уведомления расписания {state_text}.", parse_mode="Markdown")

@bot.message_handler(commands=['reminders'])
def cmd_reminders(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
    new_state = db.toggle_reminders(uid)
    state_text = "включены" if new_state else "отключены"
    bot.reply_to(m, f"Учебные напоминания {state_text}.", parse_mode="Markdown")

@bot.message_handler(commands=['faq'])
def cmd_faq(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
    faq_list = db.get_all_faq()
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
    db.ensure_user(user)
    res_list = db.get_all_resources()
    if not res_list:
        return bot.send_message(uid, "Список ресурсов пуст.")
    text = "*Полезные ресурсы:*"
    for name, url in res_list:
        text += f"\n{name}: {url}"
    bot.send_message(uid, text, parse_mode="Markdown")

# ——————————————————————————————————————————————————————
# 5) Функции подачи заявок (справка, отсрочка, пересдача)
# ——————————————————————————————————————————————————————
temp_request = {}

@bot.message_handler(commands=['spravka'])
def cmd_spravka(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
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
    db.insert_request(uid, "spravka", req.get("name"), req.get("group"), req.get("details"), "Принята")
    bot.send_message(uid,
                     f"✅ Заявка на справку принята!\n"
                     f"ФИО: {req['name']}\nГруппа: {req['group']}\nТип справки: {req['details']}\n\n"
                     "Статус заявки можно посмотреть командой /status.",
                     parse_mode="Markdown")

@bot.message_handler(commands=['otsrochka'])
def cmd_otsrochka(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
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
    db.insert_request(uid, "otsrochka", req.get("name"), req.get("group"), req.get("details"), "Принята")
    bot.send_message(uid,
                     f"✅ Заявление на отсрочку принято!\n"
                     f"ФИО: {req['name']}\nГруппа: {req['group']}\nПричина: {req['details']}\n\n"
                     "Статус заявки можно проверить командой /status.",
                     parse_mode="Markdown")

@bot.message_handler(commands=['hvost'])
def cmd_hvost(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
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
    db.insert_request(uid, "hvost", req.get("name"), req.get("group"), req.get("details"), "Принята")
    bot.send_message(uid,
                     f"✅ Заявка на пересдачу принята!\n"
                     f"ФИО: {req['name']}\nГруппа: {req['group']}\nДисциплина: {req['details']}\n\n"
                     "Статус заявки можно проверить командой /status.",
                     parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def cmd_status(m):
    uid = m.chat.id
    user = m.from_user
    db.ensure_user(user)
    requests_list = db.get_requests_by_user(uid)
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
    db.ensure_user(user)
    news_list = db.get_all_news()
    if not news_list:
        return bot.send_message(uid, "Новостей пока нет.")
    text = "*Новости и объявления:*"
    for content, dt in news_list:
        try:
            date_obj = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
            date_str = date_obj.strftime("%d.%m.%Y")
        except:
            date_str = dt.split(" ")[0]
        text += f"\n[{date_str}] {content}"
    bot.send_message(uid, text, parse_mode="Markdown")

@bot.message_handler(commands=['addnews'])
def cmd_addnews(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        msg = bot.reply_to(m, "Введите текст новости/объявления:")
        bot.register_next_step_handler(msg, addnews_step)
    else:
        content = parts[1].strip()
        if content:
            broadcast_news(content)
        else:
            bot.reply_to(m, "Текст новости не должен быть пустым.")

def addnews_step(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    content = m.text.strip()
    if not content:
        return bot.reply_to(m, "Текст новости не должен быть пустым.")
    broadcast_news(content)

def broadcast_news(content: str):
    """Добавить новость и разослать всем пользователям."""
    db.add_news(content)
    all_users = db.get_all_user_ids()
    for user_id in all_users:
        try:
            bot.send_message(user_id, f"📢 *Новое объявление:* {content}", parse_mode="Markdown")
        except Exception as e:
            continue
            
@bot.message_handler(commands=['delnews'])
def cmd_delnews(m):
    # Доступно только администратору
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        return bot.reply_to(m, "Использование: /delnews <ID>")
    news_id = int(parts[1])
    success = db.delete_news(news_id)
    if success:
        bot.reply_to(m, f"Новость с ID {news_id} удалена.")
    else:
        bot.reply_to(m, f"Новость с ID {news_id} не найдена.")

@bot.message_handler(commands=['anons', 'broadcast'])
def cmd_anons(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
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
    all_users = db.get_all_user_ids()
    count = 0
    for user_id in all_users:
        try:
            bot.send_message(user_id, text)
            count += 1
        except:
            continue
    if ADMIN_ID:
        bot.send_message(ADMIN_ID, f"Отправлено объявление {count} пользователям.")

@bot.message_handler(commands=['addfaq'])
def cmd_addfaq(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    msg = bot.reply_to(m, "Введите новый вопрос (FAQ):")
    bot.register_next_step_handler(msg, addfaq_question_step)

def addfaq_question_step(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    question_text = m.text.strip()
    if not question_text:
        return bot.reply_to(m, "Вопрос не должен быть пустым.")
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
    temp_request.pop(m.chat.id, None)
    db.add_faq(question_text, answer_text)
    bot.send_message(m.chat.id, f"✅ FAQ добавлен: {question_text} – {answer_text}")

@bot.message_handler(commands=['delfaq'])
def cmd_delfaq(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        return bot.reply_to(m, "Используйте: /delfaq <ID>")
    faq_id = int(parts[1])
    success = db.delete_faq(faq_id)
    if success:
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
    db.add_resource(name, url)
    bot.send_message(m.chat.id, f"✅ Ресурс добавлен: {name} – {url}")

@bot.message_handler(commands=['delresource'])
def cmd_delresource(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        return bot.reply_to(m, "Используйте: /delresource <ID>")
    res_id = int(parts[1])
    success = db.delete_resource(res_id)
    if success:
        bot.reply_to(m, f"Ресурс с ID {res_id} удален.")
    else:
        bot.reply_to(m, f"Ресурс с ID {res_id} не найден.")

@bot.message_handler(commands=['questions'])
def cmd_questions(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    questions_list = db.get_unanswered_questions()
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
    if not parts[1].isdigit():
        return bot.reply_to(m, "Неверный формат ID.")
    qid = int(parts[1])
    if len(parts) >= 3:
        answer_text = parts[2].strip()
        if not answer_text:
            return bot.reply_to(m, "Текст ответа не должен быть пустым.")
        send_answer_to_user(qid, answer_text)
    else:
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
    """Отправить ответ пользователю и отметить вопрос как отвеченный."""
    info = db.answer_question(qid, answer_text)
    if not info:
        return bot.send_message(ADMIN_ID, f"Вопрос ID{qid} не найден или уже закрыт.")
    user_id, question_text = info
    try:
        bot.send_message(user_id, f"✉️ Ответ на ваш вопрос \"{question_text}\":\n{answer_text}")
        bot.send_message(ADMIN_ID, f"Ответ пользователю {user_id} отправлен.")
    except:
        bot.send_message(ADMIN_ID, f"Не удалось доставить ответ пользователю {user_id}. Возможно, он остановил бота.")

@bot.message_handler(commands=['stats'])
def cmd_stats(m):
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return
    stats = db.get_stats()
    text = "*Статистика использования:*\n"
    text += f"Пользователей: {stats['users']}\n"
    text += f"Отправлено заявок: {stats['requests_total']} (Справок: {stats['spravka']}, Отсрочек: {stats['otsrochka']}, Пересдач: {stats['hvost']})\n"
    text += f"Вопросов получено: {stats['questions_total']} (из них без ответа: {stats['questions_unanswered']})\n"
    text += f"Новостей опубликовано: {stats['news']}\n"
    text += f"FAQ записей: {stats['faq']}, ресурсов: {stats['resources']}"
    bot.send_message(m.chat.id, text, parse_mode="Markdown")

# ——————————————————————————————————————————————————————
# 7) Обработчики кнопок меню (ReplyKeyboard)
# ——————————————————————————————————————————————————————
@bot.message_handler(func=lambda m: m.text == "📅 Расписание (сегодня)")
def menu_today(m):
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
    bot.send_message(uid, "Напишите свой вопрос в ответном сообщении, и он будет сохранен для последующего ответа")

@bot.message_handler(commands=['list'])
def cmd_list(m):
    # Доступно только администратору
    if not ADMIN_ID or m.chat.id != ADMIN_ID:
        return

    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return bot.reply_to(m, "Использование: /list <faq|resources|news|questions>")

    category = parts[1].strip().lower()
    text = ""

    if category == "faq":
        rows = db.cur.execute("SELECT id, question, answer FROM faq").fetchall()
        if not rows:
            text = "FAQ пока пуст."
        else:
            text = "*FAQ (ID | Вопрос — Ответ):*"
            for rid, q, a in rows:
                text += f"\n{rid} | {q} — {a}"

    elif category == "resources":
        rows = db.cur.execute("SELECT id, name, url FROM resources").fetchall()
        if not rows:
            text = "Список ресурсов пуст."
        else:
            text = "*Ресурсы (ID | Название — URL):*"
            for rid, name, url in rows:
                text += f"\n{rid} | {name} — {url}"

    elif category == "news":
        rows = db.cur.execute(
            "SELECT id, content, datetime(created_at, 'localtime') "
            "FROM news ORDER BY created_at DESC"
        ).fetchall()
        if not rows:
            text = "Новостей пока нет."
        else:
            text = "*Новости (ID | Дата — Текст):*"
            for rid, content, dt in rows:
                try:
                    date_str = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
                except:
                    date_str = dt.split(" ")[0]
                text += f"\n{rid} | [{date_str}] {content}"

    elif category == "questions":
        rows = db.cur.execute("SELECT id, user_id, question, answered FROM questions").fetchall()
        if not rows:
            text = "Вопросов нет."
        else:
            text = "*Вопросы (ID | Пользователь — Отвечен?):*"
            for rid, uid, q, answered in rows:
                status = "✅" if answered else "❌"
                text += f"\n{rid} | {uid} — {status} «{q}»"

    else:
        return bot.reply_to(m, "Неподдерживаемая категория. Используйте faq, resources, news или questions.")

    bot.send_message(m.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👤 Мой профиль")
def menu_profile(m):
    uid = m.chat.id
    profile = db.get_user_profile(uid)
    if not profile:
        return bot.send_message(uid, "Данные профиля не найдены.")
    grp, sub, notify_flag, rem_flag = profile
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
        bot.delete_message(uid, call.message.message_id)
        bot.answer_callback_query(call.id, "Выбрано: Справка")
        bot.send_message(uid, "Оформление справки.\n1⃣ Введите *ФИО*:", parse_mode="Markdown")
        dummy = call.message
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
    if m.chat.type != "private":
        return
    if ADMIN_ID and m.chat.id == ADMIN_ID:
        return
    if m.text.startswith('/'):
        return bot.send_message(m.chat.id, "Неизвестная команда. Используйте /start для списка команд.")
    question_text = m.text.strip()
    if not question_text:
        return
    db.add_question(m.chat.id, question_text)
    bot.reply_to(m, "✅ Ваш вопрос отправлен. Мы ответим на него в ближайшее время.")

# ——————————————————————————————————————————————————————
# 9) Ежедневные рассылки (расписание и напоминания)
# ——————————————————————————————————————————————————————
def send_daily_schedule():
    """Ежедневная отправка расписания на сегодня (08:00) всем, кто включил notify."""
    users_list = db.get_users_for_notify()
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
    reminders_list = []
    for ev in data.get('deadlines', []):
        if ev.get('date') == today_str:
            reminders_list.append(f"– {ev['message']}")
    wd = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][datetime.now().weekday()]
    mot_list = data.get('motivation', {}).get(wd) or data.get('motivation', {}).get('Any', [])
    mot_message = ""
    if mot_list:
        import random
        mot_message = random.choice(mot_list)
    text = ""
    if reminders_list:
        text += "📌 *Напоминания:*\n" + "\n".join(reminders_list)
    if mot_message:
        text += ("\n\n" if text else "") + f"💡 *Мотивация:* {mot_message}"
    if not text:
        return
    users_to_remind = db.get_users_for_reminders()
    for user_id in users_to_remind:
        try:
            bot.send_message(user_id, text, parse_mode="Markdown")
        except:
            continue
            
# Планируем ежедневные задачи
schedule.every().day.at("08:00").do(send_daily_schedule)
schedule.every().day.at("09:00").do(send_daily_reminders)

# Запуск отдельного потока для выполнения задач schedule
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
