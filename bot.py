import telebot
from telebot import types
import sqlite3
import pandas as pd
from datetime import datetime, date
from telegram_bot_calendar import DetailedTelegramCalendar
import os
import logging

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
HR_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = telebot.TeleBot(TOKEN)

if os.path.exists("/data"):
    DB_PATH = "/data/hr_system.db"
else:
    DB_PATH = "hr_system.db"

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

    cursor.execute(
        "SELECT name FROM employees WHERE telegram_id=?",
        (tid,)
    )

    res = cursor.fetchone()
    conn.close()

    return res[0] if res else None


def ensure_session(chat_id):
    if chat_id not in user_temp_data:
        name = get_user_name(chat_id)
        if name:
            user_temp_data[chat_id] = {"name": name}
        else:
            user_temp_data[chat_id] = {}

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

    markup.add(
        "📝 تقديم طلب إجازة",
        "📜 سجل إجازاتي"
    )

    bot.send_message(
        message.chat.id,
        f"مرحباً {name}",
        reply_markup=markup
    )

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

    bot.send_message(
        message.chat.id,
        "لوحة الإدارة",
        reply_markup=markup
    )

# =====================
# CALLBACK (FIXED)
# =====================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):

    chat_id = call.message.chat.id
    ensure_session(chat_id)

    # ===== FIX CALENDAR =====
    result, key, step = DetailedTelegramCalendar(
        min_date=date.today()
    ).process(call.data)

    if not result and key:
        bot.edit_message_text(
            f"اختر {step}",
            chat_id,
            call.message.message_id,
            reply_markup=key
        )
        return

    elif result:
        user_temp_data[chat_id]["date"] = str(result)

        bot.edit_message_text(
            f"تم اختيار التاريخ: {result}",
            chat_id,
            call.message.message_id
        )

        msg = bot.send_message(chat_id, "اكتب سبب الإجازة")
        bot.register_next_step_handler(msg, save_reason)
        return

    # ===== باقي الأزرار =====
    if call.data == "add_emp":

        msg = bot.send_message(chat_id, "أرسل اسم الموظف")
        bot.register_next_step_handler(msg, ask_emp_id)

    elif call.data == "manage_emp":

        show_employees(chat_id)

    elif call.data.startswith("del_emp_"):

        delete_employee(call)

    elif call.data.startswith("edit_emp_"):

        ask_new_name(call)

    elif call.data == "pending":

        show_pending()

    elif call.data == "all_leaves":

        show_all_leaves(chat_id)

    elif call.data == "export":

        export_excel()

    elif call.data.startswith("emp_history_"):

        show_employee_history(call)

    elif call.data.startswith("type_"):

        user_temp_data[chat_id]["leave_type"] = call.data.split("_")[1]
        show_duration(call.message)

    elif call.data.startswith("dur_"):

        user_temp_data[chat_id]["duration"] = call.data.split("_")[1]

        calendar, step = DetailedTelegramCalendar(
            min_date=date.today()
        ).build()

        bot.edit_message_text(
            "اختر التاريخ",
            chat_id,
            call.message.message_id,
            reply_markup=calendar
        )

# =====================
# SAVE REQUEST
# =====================
def save_reason(message):

    chat_id = message.chat.id
    user_temp_data[chat_id]["reason"] = message.text

    data = user_temp_data[chat_id]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO leaves(
        emp_name,
        emp_id,
        type,
        duration,
        date,
        time,
        reason,
        status,
        timestamp
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("name"),
        chat_id,
        data.get("leave_type"),
        data.get("duration"),
        data.get("date"),
        datetime.now().strftime("%H:%M"),
        data.get("reason"),
        "انتظار",
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    conn.commit()
    conn.close()

    bot.send_message(chat_id, "✅ تم إرسال الطلب")

    bot.send_message(
        HR_ADMIN_ID,
        f"طلب جديد:\n{data.get('name')} | {data.get('leave_type')} | {data.get('date')}"
    )

# =====================
# OTHER FUNCTIONS
# =====================
def show_employees(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id,name FROM employees")
    rows = cursor.fetchall()
    conn.close()

    for r in rows:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("📜 الإجازات", callback_data=f"emp_history_{r[0]}"),
            types.InlineKeyboardButton("✏ تعديل", callback_data=f"edit_emp_{r[0]}"),
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

def ask_new_name(call):
    emp_id = call.data.split("_")[2]
    msg = bot.send_message(call.message.chat.id, "أرسل الاسم الجديد")
    bot.register_next_step_handler(msg, update_employee_name, emp_id)

def update_employee_name(message, emp_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE employees SET name=? WHERE id=?", (message.text, emp_id))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "تم التعديل")

def show_employee_history(call):
    emp_id = call.data.split("_")[2]
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT emp_name,type,date,status FROM leaves WHERE emp_id=?", (emp_id,))
    rows = cursor.fetchall()
    conn.close()

    text = "\n".join([f"{r[0]} | {r[1]} | {r[2]} | {r[3]}" for r in rows])
    bot.send_message(call.message.chat.id, text if text else "لا يوجد")

def show_all_leaves(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT emp_name,type,date,status FROM leaves")
    rows = cursor.fetchall()
    conn.close()

    text = "\n".join([f"{r[0]} | {r[1]} | {r[2]} | {r[3]}" for r in rows])
    bot.send_message(chat_id, text if text else "لا يوجد")

def show_pending():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT emp_name,type,date FROM leaves WHERE status='انتظار'")
    rows = cursor.fetchall()
    conn.close()

    for r in rows:
        bot.send_message(HR_ADMIN_ID, f"{r[0]} | {r[1]} | {r[2]}")

def export_excel():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM leaves", conn)
    conn.close()

    path = "report.xlsx"
    df.to_excel(path, index=False)

    with open(path, "rb") as f:
        bot.send_document(HR_ADMIN_ID, f)

@bot.message_handler(func=lambda m: m.text == "📝 تقديم طلب إجازة")
def leave_request(message):
    show_leave_types(message)

def show_leave_types(message):
    markup = types.InlineKeyboardMarkup()
    types_list = ["إدارية", "مرضية", "غير مدفوعة"]
    markup.add(*[types.InlineKeyboardButton(t, callback_data=f"type_{t}") for t in types_list])
    bot.send_message(message.chat.id, "اختر النوع", reply_markup=markup)

def show_duration(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("ساعية", callback_data="dur_ساعية"),
        types.InlineKeyboardButton("يومية", callback_data="dur_يومية")
    )
    bot.edit_message_text("المدة", message.chat.id, message.message_id, reply_markup=markup)

# =====================
# RUN
# =====================
if __name__ == "__main__":
    init_db()
    print("البوت يعمل")
    bot.infinity_polling(skip_pending=True)
