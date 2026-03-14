import telebot
from telebot import types
import sqlite3
import pandas as pd
from datetime import datetime, date
from telegram_bot_calendar import DetailedTelegramCalendar
import os

# --- 1. الإعدادات ---
TOKEN = os.getenv('BOT_TOKEN')
HR_ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

bot = telebot.TeleBot(TOKEN)

# --- 2. مسار قاعدة البيانات ---
if os.path.exists('/data'):
    DB_PATH = '/data/hr_system.db'
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, 'hr_system.db')

LSTEP_AR = {'y': 'السنة', 'm': 'الشهر', 'd': 'اليوم'}
user_temp_data = {}

# --- 3. الداتابيز ---
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS employees(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        telegram_id INTEGER UNIQUE
    )
    ''')

    cursor.execute('''
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
    ''')

    conn.commit()
    conn.close()

# --- 4. استرجاع اسم الموظف ---
def get_user_name(telegram_id):

    if telegram_id == HR_ADMIN_ID:
        return "المدير إيهاب"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM employees WHERE telegram_id=?", (telegram_id,))
    res = cursor.fetchone()

    conn.close()

    return res[0] if res else None

# --- 5. /start ---
@bot.message_handler(commands=['start'])
def start(message):

    name = get_user_name(message.from_user.id)

    if name:

        user_temp_data[message.chat.id] = {
            "name": name,
            "user_id": message.chat.id
        }

        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("📝 تقديم طلب إجازة", "📜 سجل إجازاتي")

        bot.send_message(
            message.chat.id,
            f"مرحباً بك {name} في نظام طلب الإجازات.",
            reply_markup=markup
        )

    else:
        bot.send_message(
            message.chat.id,
            "🚫 غير مسجل في النظام. يرجى مراجعة الإدارة."
        )

# --- 6. لوحة المدير ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):

    if message.chat.id != HR_ADMIN_ID:
        return

    markup = types.InlineKeyboardMarkup(row_width=1)

    markup.add(
        types.InlineKeyboardButton("➕ إضافة موظف", callback_data="admin_add_emp"),
        types.InlineKeyboardButton("⏳ الطلبات المعلقة", callback_data="admin_pending"),
        types.InlineKeyboardButton("📊 تصدير Excel", callback_data="admin_export_excel")
    )

    bot.send_message(
        message.chat.id,
        "🛠 لوحة تحكم المدير",
        reply_markup=markup
    )

# --- 7. CALLBACK ---
@bot.callback_query_handler(func=lambda call: not call.data.startswith("cbcal_"))
def callback_handler(call):

    chat_id = call.message.chat.id

    if chat_id not in user_temp_data:
        name = get_user_name(chat_id)
        if name:
            user_temp_data[chat_id] = {"name": name}

    # اضافة موظف
    if call.data == "admin_add_emp":

        msg = bot.send_message(chat_id, "أرسل اسم الموظف:")
        bot.register_next_step_handler(msg, ask_employee_id)

    # نوع الاجازة
    elif call.data.startswith("type_"):

        user_temp_data[chat_id]["leave_type"] = call.data.split("_")[1]
        show_duration_type(call.message)

    # مدة الاجازة
    elif call.data.startswith("dur_"):

        user_temp_data[chat_id]["duration_type"] = call.data.split("_")[1]

        calendar, step = DetailedTelegramCalendar(
            calendar_id=1,
            min_date=date.today()
        ).build()

        bot.edit_message_text(
            f"اختر {LSTEP_AR.get(step, step)}:",
            chat_id,
            call.message.message_id,
            reply_markup=calendar
        )

    elif call.data == "admin_pending":
        show_pending_leaves(call.message)

    elif call.data == "admin_export_excel":
        export_to_excel()

    elif call.data.startswith("hr_"):
        process_hr_decision(call)

    elif call.data == "filter_all":
        display_user_history(call.message)

# --- 8. إضافة موظف ---
def ask_employee_id(message):

    chat_id = message.chat.id

    if chat_id != HR_ADMIN_ID:
        return

    name = message.text.strip()

    user_temp_data[chat_id]["new_emp_name"] = name

    msg = bot.send_message(chat_id, "أرسل Telegram ID:")
    bot.register_next_step_handler(msg, save_employee)

def save_employee(message):

    chat_id = message.chat.id

    name = user_temp_data.get(chat_id, {}).get("new_emp_name")

    try:
        tid = int(message.text.strip())
    except:
        bot.send_message(chat_id, "الآيدي يجب أن يكون رقم.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO employees (name, telegram_id) VALUES (?,?)",
            (name, tid)
        )

        conn.commit()

        bot.send_message(chat_id, f"تم إضافة الموظف {name}")

    except:
        bot.send_message(chat_id, "هذا الموظف مسجل مسبقاً.")

    conn.close()

# --- 9. اختيار نوع الاجازة ---
def show_leave_types(message):

    markup = types.InlineKeyboardMarkup(row_width=2)

    types_list = ["إدارية", "مرضية", "غير مدفوعة", "زواج", "حج"]

    btns = [
        types.InlineKeyboardButton(t, callback_data=f"type_{t}")
        for t in types_list
    ]

    markup.add(*btns)

    bot.send_message(
        message.chat.id,
        "اختر نوع الإجازة:",
        reply_markup=markup
    )

# --- 10. اختيار المدة ---
def show_duration_type(message):

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton("ساعية", callback_data="dur_ساعية"),
        types.InlineKeyboardButton("يومية", callback_data="dur_يومية")
    )

    bot.edit_message_text(
        "نوع الإجازة:",
        message.chat.id,
        message.message_id,
        reply_markup=markup
    )

# --- 11. التقويم ---
@bot.callback_query_handler(func=DetailedTelegramCalendar.func(calendar_id=1))
def cal(c):

    chat_id = c.message.chat.id

    result, key, step = DetailedTelegramCalendar(
        calendar_id=1,
        min_date=date.today()
    ).process(c.data)

    if not result and key:

        bot.edit_message_text(
            f"اختر {LSTEP_AR.get(step, step)}",
            chat_id,
            c.message.message_id,
            reply_markup=key
        )

    elif result:

        user_temp_data[chat_id]["date"] = result

        if user_temp_data[chat_id].get("duration_type") == "ساعية":

            msg = bot.send_message(chat_id, "اكتب وقت الإجازة:")
            bot.register_next_step_handler(msg, ask_reason_after_time)

        else:

            msg = bot.send_message(chat_id, "اكتب سبب الإجازة:")
            bot.register_next_step_handler(msg, finalize_request)

# --- 12. طلب الوقت ---
def ask_reason_after_time(message):

    chat_id = message.chat.id

    user_temp_data[chat_id]["time"] = message.text

    msg = bot.send_message(chat_id, "اكتب سبب الإجازة:")
    bot.register_next_step_handler(msg, finalize_request)

# --- 13. حفظ الطلب ---
def finalize_request(message):

    chat_id = message.chat.id
    data = user_temp_data.get(chat_id)

    reason = message.text

    time_val = data.get("time", "-")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """INSERT INTO leaves
        (emp_name,emp_id,type,duration,date,time,reason,status,timestamp)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            data["name"],
            chat_id,
            data["leave_type"],
            data["duration_type"],
            str(data["date"]),
            time_val,
            reason,
            "انتظار",
            datetime.now().strftime("%Y-%m-%d %H:%M")
        )
    )

    lid = cursor.lastrowid

    conn.commit()
    conn.close()

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton("✅ موافقة", callback_data=f"hr_approve_{chat_id}_{lid}"),
        types.InlineKeyboardButton("❌ رفض", callback_data=f"hr_reject_{chat_id}_{lid}")
    )

    bot.send_message(
        HR_ADMIN_ID,
        f"""
طلب إجازة جديد

الموظف: {data['name']}
النوع: {data['leave_type']}
التاريخ: {data['date']}
الوقت: {time_val}

السبب:
{reason}
""",
        reply_markup=markup
    )

    bot.send_message(chat_id, "تم إرسال الطلب.")

# --- 14. الطلبات المعلقة ---
def show_pending_leaves(message):

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id,emp_name,type,date,emp_id,reason FROM leaves WHERE status='انتظار'"
    )

    rows = cursor.fetchall()

    conn.close()

    if not rows:
        bot.send_message(HR_ADMIN_ID, "لا توجد طلبات.")
        return

    for r in rows:

        m = types.InlineKeyboardMarkup()

        m.add(
            types.InlineKeyboardButton("✅ موافقة", callback_data=f"hr_approve_{r[4]}_{r[0]}"),
            types.InlineKeyboardButton("❌ رفض", callback_data=f"hr_reject_{r[4]}_{r[0]}")
        )

        bot.send_message(
            HR_ADMIN_ID,
            f"""
الموظف: {r[1]}
التاريخ: {r[3]}
النوع: {r[2]}

السبب:
{r[5]}
""",
            reply_markup=m
        )

# --- 15. قرار المدير ---
def process_hr_decision(call):

    p = call.data.split("_")

    act = p[1]
    eid = int(p[2])
    lid = p[3]

    status = "مقبولة" if act == "approve" else "مرفوضة"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE leaves SET status=? WHERE id=?",
        (status, lid)
    )

    conn.commit()
    conn.close()

    bot.send_message(eid, f"تم {status} طلب الإجازة.")

# --- 16. Excel ---
def export_to_excel():

    conn = sqlite3.connect(DB_PATH)

    df = pd.read_sql_query("SELECT * FROM leaves", conn)

    conn.close()

    path = "report.xlsx"

    df.to_excel(path, index=False)

    with open(path, "rb") as f:
        bot.send_document(HR_ADMIN_ID, f)

# --- 17. طلب إجازة ---
@bot.message_handler(func=lambda message: message.text == "📝 تقديم طلب إجازة")
def trigger_leave(message):
    show_leave_types(message)

# --- 18. سجل الإجازات ---
@bot.message_handler(func=lambda message: message.text == "📜 سجل إجازاتي")
def trigger_history(message):

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "عرض السجل",
            callback_data="filter_all"
        )
    )

    bot.send_message(
        message.chat.id,
        "سجل إجازاتك:",
        reply_markup=markup
    )

def display_user_history(message):

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT type,date,status FROM leaves WHERE emp_id=? ORDER BY id DESC",
        (message.chat.id,)
    )

    rows = cursor.fetchall()

    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "لا يوجد سجل.")
        return

    text = "سجل الطلبات:\n"

    for r in rows:
        text += f"{r[1]} | {r[0]} | {r[2]}\n"

    bot.send_message(message.chat.id, text)

# --- 19. تشغيل البوت ---
if __name__ == "__main__":

    init_db()

    print("البوت يعمل...")

    bot.infinity_polling()