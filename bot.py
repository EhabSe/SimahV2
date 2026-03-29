import telebot
from telebot import types
import sqlite3
import pandas as pd
from datetime import datetime, date
from telegram_bot_calendar import DetailedTelegramCalendar
import os
import logging

# =====================
# CONFIG
# =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN is missing")

try:
    HR_ADMIN_ID = int(os.getenv("ADMIN_ID"))
except:
    raise ValueError("ADMIN_ID must be a valid integer")

bot = telebot.TeleBot(TOKEN)

DB_PATH = "/data/hr_system.db" if os.path.exists("/data") else "hr_system.db"

user_temp_data = {}

# =====================
# DATABASE
# =====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS employees(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        telegram_id INTEGER UNIQUE
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS leaves(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_name TEXT,
        emp_id INTEGER,
        type TEXT,
        duration TEXT,
        date TEXT,
        time TEXT,
        reason TEXT,
        status TEXT,
        timestamp TEXT
    )
    """)

    conn.commit()
    conn.close()

# =====================
# HELPERS
# =====================
def get_user_name(tid):
    if tid == HR_ADMIN_ID:
        return "المدير"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM employees WHERE telegram_id=?", (tid,))
    res = cursor.fetchone()
    conn.close()

    return res[0] if res else None


def ensure_session(chat_id):
    if chat_id not in user_temp_data:
        name = get_user_name(chat_id)
        if name:
            user_temp_data[chat_id] = {"name": name}


# =====================
# START
# =====================
@bot.message_handler(commands=["start"])
def start(message):
    name = get_user_name(message.chat.id)

    if not name:
        bot.send_message(message.chat.id, "غير مسجل")
        return

    ensure_session(message.chat.id)

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📝 تقديم طلب إجازة", "📜 سجل إجازاتي")

    bot.send_message(message.chat.id, f"مرحباً {name}", reply_markup=markup)


# =====================
# ADMIN PANEL
# =====================
@bot.message_handler(commands=["admin"])
def admin_panel(message):
    if message.chat.id != HR_ADMIN_ID:
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("➕ إضافة موظف", callback_data="add_emp"),
        types.InlineKeyboardButton("👥 إدارة الموظفين", callback_data="manage_emp"),
        types.InlineKeyboardButton("⏳ الطلبات المعلقة", callback_data="pending"),
        types.InlineKeyboardButton("📜 سجل الإجازات", callback_data="all_leaves"),
        types.InlineKeyboardButton("📊 تصدير Excel", callback_data="export")
    )

    bot.send_message(message.chat.id, "لوحة الإدارة", reply_markup=markup)


# =====================
# APPROVE / REJECT (FIXED)
# =====================
@bot.callback_query_handler(func=lambda call: call.data.startswith("approve_") or call.data.startswith("reject_"))
def handle_approval(call):
    try:
        action, leave_id, emp_id = call.data.split("_")

        status = "مقبول" if action == "approve" else "مرفوض"

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE leaves SET status=? WHERE id=?",
            (status, leave_id)
        )

        conn.commit()
        conn.close()

        bot.send_message(call.message.chat.id, f"تم {status}")
        bot.send_message(int(emp_id), f"تم {status} طلبك")

    except Exception:
        logging.exception("Approval Error")
        bot.send_message(call.message.chat.id, "حدث خطأ")


# =====================
# CALLBACK HANDLER
# =====================
@bot.callback_query_handler(func=lambda call: not DetailedTelegramCalendar.func()(call)
                             and not call.data.startswith("approve_")
                             and not call.data.startswith("reject_"))
def callback_handler(call):
    chat_id = call.message.chat.id
    ensure_session(chat_id)
    user_temp_data.setdefault(chat_id, {})

    try:
        if call.data == "add_emp":
            msg = bot.send_message(chat_id, "أرسل اسم الموظف")
            bot.register_next_step_handler(msg, ask_emp_id)

        elif call.data == "manage_emp":
            show_employees(chat_id)

        elif call.data.startswith("del_emp_"):
            delete_employee(call)

        elif call.data == "pending":
            show_pending()

        elif call.data == "all_leaves":
            show_all_leaves(chat_id)

        elif call.data == "export":
            export_excel()

        elif call.data.startswith("type_"):
            user_temp_data[chat_id]["leave_type"] = call.data.split("_", 1)[1]
            show_duration(call.message)

        elif call.data.startswith("dur_"):
            duration = call.data.split("_", 1)[1]
            user_temp_data[chat_id]["duration"] = duration
            logging.info(f"{chat_id} selected duration: {duration}")

            calendar, _ = DetailedTelegramCalendar(min_date=date.today()).build()

            try:
                bot.edit_message_text(
                    "اختر التاريخ",
                    chat_id,
                    call.message.message_id,
                    reply_markup=calendar
                )
            except:
                bot.send_message(chat_id, "اختر التاريخ", reply_markup=calendar)

    except Exception:
        logging.exception("Callback Error")
        bot.send_message(chat_id, "حدث خطأ، حاول مرة أخرى")


# =====================
# CALENDAR HANDLER
# =====================
@bot.callback_query_handler(func=DetailedTelegramCalendar.func())
def calendar_handler(call):
    chat_id = call.message.chat.id

    try:
        result, key, step = DetailedTelegramCalendar(min_date=date.today()).process(call.data)

        if not result and key:
            bot.edit_message_text(
                f"اختر {step}",
                chat_id,
                call.message.message_id,
                reply_markup=key
            )

        elif result:
            ensure_session(chat_id)
            user_temp_data.setdefault(chat_id, {})
            user_temp_data[chat_id]["date"] = result.strftime("%Y-%m-%d")

            # --- NEW LOGIC: Check if hourly vacation ---
            duration = user_temp_data[chat_id].get("duration")
            
            if duration == "ساعية":
                # Ask for the time range
                msg = bot.send_message(chat_id, "اكتب وقت الإجازة (مثلاً: 10 ص إلى 2 م)")
                bot.register_next_step_handler(msg, ask_leave_time)
            else:
                # If it's a daily vacation, go straight to the reason
                msg = bot.send_message(chat_id, "اكتب السبب")
                bot.register_next_step_handler(msg, save_leave_request)

    except Exception:
        logging.exception("Calendar Error")
        bot.send_message(chat_id, "حدث خطأ في اختيار التاريخ")


# =====================
# TIME HANDLER (NEW)
# =====================
def ask_leave_time(message):
    chat_id = message.chat.id
    time_range = message.text

    # Update the duration to include the hand-typed time range
    if chat_id in user_temp_data:
        user_temp_data[chat_id]["duration"] = f"ساعية ({time_range})"

    # Now ask for the reason, just like normal
    msg = bot.send_message(chat_id, "اكتب السبب")
    bot.register_next_step_handler(msg, save_leave_request)


# =====================
# SAVE REQUEST
# =====================
def save_leave_request(message):
    chat_id = message.chat.id
    reason = message.text

    data = user_temp_data.get(chat_id, {})

    if not data.get("date"):
        bot.send_message(chat_id, "حدث خطأ، أعد المحاولة")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO leaves(emp_name,emp_id,type,duration,date,time,reason,status,timestamp)
    VALUES(?,?,?,?,?,?,?,?,?)
    """, (
        data.get("name"),
        chat_id,
        data.get("leave_type"),
        data.get("duration"),
        data.get("date"),
        datetime.now().strftime("%H:%M"),
        reason,
        "انتظار",
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    conn.commit()
    conn.close()

    user_temp_data.pop(chat_id, None)

    bot.send_message(chat_id, "تم إرسال الطلب ✅")

    bot.send_message(
        HR_ADMIN_ID,
        f"""طلب جديد:
👤 الموظف: {data.get('name')}
📌 النوع: {data.get('leave_type')}
⏱ المدة: {data.get('duration')}
📅 التاريخ: {data.get('date')}
📝 السبب: {reason}
"""
    )


# =====================
# EMPLOYEE MANAGEMENT
# =====================
def ask_emp_id(message):
    name = message.text
    msg = bot.send_message(message.chat.id, "أرسل Telegram ID")
    bot.register_next_step_handler(msg, save_employee, name)


def save_employee(message, name):
    try:
        telegram_id = int(message.text)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO employees(name, telegram_id) VALUES (?, ?)",
            (name, telegram_id)
        )

        conn.commit()
        conn.close()

        bot.send_message(message.chat.id, "تم إضافة الموظف ✅")

    except Exception:
        logging.exception("Add Employee Error")
        bot.send_message(message.chat.id, "خطأ في الإدخال")


def show_employees(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT id,name FROM employees")
    rows = cursor.fetchall()
    conn.close()

    for r in rows:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("❌ حذف", callback_data=f"del_emp_{r[0]}")
        )
        bot.send_message(chat_id, f"الموظف: {r[1]}", reply_markup=markup)


def delete_employee(call):
    emp_id = call.data.split("_")[2]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM employees WHERE id=?", (emp_id,))
    conn.commit()
    conn.close()

    bot.send_message(call.message.chat.id, "تم حذف الموظف")


# =====================
# LEAVES
# =====================
def show_pending():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id,emp_name,type,date,emp_id,reason,duration 
    FROM leaves WHERE status='انتظار'
    """)
    rows = cursor.fetchall()
    conn.close()

    for r in rows:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{r[0]}_{r[4]}"),
            types.InlineKeyboardButton("❌ رفض", callback_data=f"reject_{r[0]}_{r[4]}")
        )

        bot.send_message(
            HR_ADMIN_ID,
            f"""👤 {r[1]}
📌 {r[2]}
⏱ {r[6]}
📅 {r[3]}
📝 {r[5]}""",
            reply_markup=markup
        )


def show_all_leaves(chat_id):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM leaves ORDER BY id DESC", conn)
    conn.close()

    text = "\n".join([f"{r.emp_name} | {r.type} | {r.date} | {r.status}" for r in df.itertuples()])
    bot.send_message(chat_id, text if text else "لا يوجد سجل")


def export_excel():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM leaves", conn)
    conn.close()

    path = "report.xlsx"
    df.to_excel(path, index=False)

    with open(path, "rb") as f:
        bot.send_document(HR_ADMIN_ID, f)


# =====================
# USER FLOW (FIXED SESSION RESET)
# =====================
@bot.message_handler(func=lambda m: m.text == "📝 تقديم طلب إجازة")
def leave_request(message):
    chat_id = message.chat.id

    user_temp_data[chat_id] = {
        "name": get_user_name(chat_id)
    }

    markup = types.InlineKeyboardMarkup()
    types_list = ["إدارية", "مرضية", "غير مدفوعة", "زواج", "حج"]

    markup.add(*[
        types.InlineKeyboardButton(t, callback_data=f"type_{t}")
        for t in types_list
    ])

    bot.send_message(chat_id, "اختر النوع", reply_markup=markup)


def show_duration(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("ساعية", callback_data="dur_ساعية"),
        types.InlineKeyboardButton("يومية", callback_data="dur_يومية")
    )

    bot.edit_message_text("اختر المدة", message.chat.id, message.message_id, reply_markup=markup)


# =====================
# RUN (RAILWAY SAFE)
# =====================
if __name__ == "__main__":
    init_db()
    print("Bot is running...")

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception:
            logging.exception("Bot crashed, restarting...")
