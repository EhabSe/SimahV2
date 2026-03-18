import telebot
from telebot import types
import sqlite3
import pandas as pd
from datetime import datetime, date
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
import os
import logging
from dotenv import load_dotenv

# إعداد السجلات لتحري الأخطاء
logging.basicConfig(level=logging.INFO)

# تحميل المتغيرات البيئية
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
HR_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = telebot.TeleBot(TOKEN)

# تحديد مسار قاعدة البيانات (يدعم Docker أو التوصيل المحلي)
DB_PATH = "/data/hr_system.db" if os.path.exists("/data") else "hr_system.db"

# قاموس لتخزين بيانات الجلسة المؤقتة للمستخدمين
user_temp_data = {}

# =====================
# DATABASE INITIALIZATION
# =====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS employees(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        telegram_id INTEGER UNIQUE
    )""")
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
    )""")
    conn.commit()
    conn.close()

# =====================
# HELPERS
# =====================
def get_user_name(tid):
    if tid == HR_ADMIN_ID: return "المدير"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM employees WHERE telegram_id=?", (tid,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else None

def ensure_session(chat_id):
    if chat_id not in user_temp_data:
        name = get_user_name(chat_id)
        user_temp_data[chat_id] = {"name": name} if name else {}

# =====================
# START & MAIN MENU
# =====================
@bot.message_handler(commands=["start"])
def start(message):
    name = get_user_name(message.chat.id)
    if not name:
        bot.send_message(message.chat.id, "❌ عذراً، أنت غير مسجل في النظام. يرجى مراجعة المدير.")
        return

    ensure_session(message.chat.id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📝 تقديم طلب إجازة", "📜 سجل إجازاتي")
    if message.chat.id == HR_ADMIN_ID:
        markup.add("⚙️ لوحة الإدارة /admin")
    
    bot.send_message(message.chat.id, f"مرحباً بك يا {name} في نظام الإجازات.", reply_markup=markup)

# =====================
# ADMIN PANEL
# =====================
@bot.message_handler(commands=["admin"])
def admin_panel(message):
    if message.chat.id != HR_ADMIN_ID: return
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ إضافة موظف", callback_data="admin_add_emp"),
        types.InlineKeyboardButton("👥 إدارة الموظفين", callback_data="admin_manage_emp"),
        types.InlineKeyboardButton("⏳ الطلبات المعلقة", callback_data="admin_pending"),
        types.InlineKeyboardButton("📜 سجل الإجازات", callback_data="admin_all_leaves"),
        types.InlineKeyboardButton("📊 تصدير Excel", callback_data="admin_export")
    )
    bot.send_message(message.chat.id, "🛠 لوحة تحكم المدير:", reply_markup=markup)

# =====================
# CALLBACK HANDLER
# =====================
@bot.callback_query_handler(func=lambda call: True and not call.data.startswith('cbcal'))
def callback_handler(call):
    chat_id = call.message.chat.id
    ensure_session(chat_id)
    data = call.data

    try:
        # --- الإجازات ---
        if data.startswith("type_"):
            user_temp_data[chat_id]["leave_type"] = data.split("_")[1]
            show_duration(call.message)

        elif data.startswith("dur_"):
            user_temp_data[chat_id]["duration"] = data.split("_")[1]
            calendar, step = DetailedTelegramCalendar(min_date=date.today()).build()
            bot.edit_message_text(f"اختر التاريخ {LSTEP[step]}", chat_id, call.message.message_id, reply_markup=calendar)

        # --- الإدارة ---
        elif data == "admin_add_emp":
            msg = bot.send_message(chat_id, "👤 أرسل اسم الموظف الكامل:")
            bot.register_next_step_handler(msg, process_emp_name)

        elif data == "admin_manage_emp":
            show_employees(chat_id)

        elif data.startswith("confirm_del_"):
            target_id = data.split("_")[2]
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM employees WHERE id=?", (target_id,))
            emp_name = cursor.fetchone()[0]
            conn.close()
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ نعم، احذف", callback_data=f"real_del_{target_id}"),
                       types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_manage_emp"))
            bot.edit_message_text(f"⚠️ هل أنت متأكد من حذف الموظف: {emp_name}؟", chat_id, call.message.message_id, reply_markup=markup)

        elif data.startswith("real_del_"):
            target_id = data.split("_")[2]
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM employees WHERE id=?", (target_id,))
            conn.commit()
            conn.close()
            bot.answer_callback_query(call.id, "تم الحذف بنجاح")
            show_employees(chat_id)

        elif data == "admin_pending":
            show_pending_requests(chat_id)

        elif data == "admin_all_leaves":
            show_all_leaves(chat_id)

        elif data == "admin_export":
            export_excel(chat_id)

        elif data.startswith("decide_"): # decide_approve_leaveid_empid
            handle_decision(call)

    except Exception as e:
        logging.error(f"Callback Error: {e}")

# =====================
# CALENDAR HANDLER
# =====================
@bot.callback_query_handler(func=DetailedTelegramCalendar.func())
def calendar_handler(call):
    chat_id = call.message.chat.id
    result, key, step = DetailedTelegramCalendar(min_date=date.today()).process(call.data)
    if not result and key:
        bot.edit_message_text(f"اختر {LSTEP[step]}", chat_id, call.message.message_id, reply_markup=key)
    elif result:
        user_temp_data[chat_id]["date"] = result.strftime("%Y-%m-%d")
        bot.edit_message_text(f"📅 تم اختيار التاريخ: {result}", chat_id, call.message.message_id)
        msg = bot.send_message(chat_id, "📝 اكتب سبب الإجازة:")
        bot.register_next_step_handler(msg, save_leave_request)

# =====================
# EMPLOYEE LOGIC
# =====================
@bot.message_handler(func=lambda m: m.text == "📝 تقديم طلب إجازة")
def leave_request_start(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    types_list = ["إدارية", "مرضية", "غير مدفوعة", "زواج", "حج"]
    markup.add(*[types.InlineKeyboardButton(t, callback_data=f"type_{t}") for t in types_list])
    bot.send_message(message.chat.id, "اختر نوع الإجازة:", reply_markup=markup)

def show_duration(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⏰ ساعية", callback_data="dur_ساعية"),
               types.InlineKeyboardButton("📅 يومية", callback_data="dur_يومية"))
    bot.edit_message_text("اختر مدة الإجازة:", message.chat.id, message.message_id, reply_markup=markup)

def save_leave_request(message):
    chat_id = message.chat.id
    ensure_session(chat_id)
    reason = message.text
    data = user_temp_data.get(chat_id, {})

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO leaves(emp_name, emp_id, type, duration, date, time, reason, status, timestamp)
    VALUES(?,?,?,?,?,?,?,?,?)""", (
        data.get("name"), chat_id, data.get("leave_type"), data.get("duration"),
        data.get("date"), datetime.now().strftime("%H:%M"), reason, "انتظار",
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))
    leave_id = cursor.lastrowid
    conn.commit()
    conn.close()

    bot.send_message(chat_id, "✅ تم إرسال طلبك للمراجعة.")
    
    # إشعار للمدير مع أزرار مباشرة
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ قبول", callback_data=f"decide_approve_{leave_id}_{chat_id}"),
               types.InlineKeyboardButton("❌ رفض", callback_data=f"decide_reject_{leave_id}_{chat_id}"))
    
    bot.send_message(HR_ADMIN_ID, f"🔔 طلب جديد من: {data.get('name')}\nالنوع: {data.get('leave_type')}\nالتاريخ: {data.get('date')}\nالسبب: {reason}", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📜 سجل إجازاتي")
def my_history(message):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT type, date, status FROM leaves WHERE emp_id=? ORDER BY id DESC LIMIT 10", (message.chat.id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        bot.send_message(message.chat.id, "لا يوجد لديك طلبات سابقة.")
        return

    history_text = "📜 آخر 10 طلبات لك:\n\n"
    for r in rows:
        status_icon = "⏳" if r[2] == "انتظار" else "✅" if r[2] == "مقبول" else "❌"
        history_text += f"{status_icon} {r[0]} | {r[1]} | {r[2]}\n"
    bot.send_message(message.chat.id, history_text)

# =====================
# ADMIN LOGIC
# =====================
def process_emp_name(message):
    user_temp_data[message.chat.id]['new_emp_name'] = message.text
    msg = bot.send_message(message.chat.id, "🆔 أرسل الـ Telegram ID للموظف:")
    bot.register_next_step_handler(msg, process_emp_id)

def process_emp_id(message):
    try:
        new_id = int(message.text)
        new_name = user_temp_data[message.chat.id]['new_emp_name']
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO employees (name, telegram_id) VALUES (?, ?)", (new_name, new_id))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f"✅ تم إضافة {new_name} بنجاح.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ خطأ: {e}")

def show_employees(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, telegram_id FROM employees")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "لا يوجد موظفين مسجلين.")
        return

    for r in rows:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌ حذف الموظف", callback_data=f"confirm_del_{r[0]}"))
        bot.send_message(chat_id, f"👤 الموظف: {r[1]}\n🆔 ID: {r[2]}", reply_markup=markup)

def handle_decision(call):
    # decide_status_leaveid_empid
    _, status_raw, leave_id, emp_id = call.data.split("_")
    status = "مقبول" if status_raw == "approve" else "مرفوض"
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE leaves SET status=? WHERE id=?", (status, leave_id))
    conn.commit()
    conn.close()

    bot.edit_message_text(f"تم تحديث الطلب إلى: {status}", call.message.chat.id, call.message.message_id)
    bot.send_message(int(emp_id), f"📢 إشعار: تم {status} طلب الإجازة الخاص بك.")

def export_excel(chat_id):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM leaves", conn)
    conn.close()
    
    path = "hr_report.xlsx"
    df.to_excel(path, index=False)
    with open(path, "rb") as f:
        bot.send_document(chat_id, f, caption="📊 تقرير الإجازات الكامل")

def show_all_leaves(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT emp_name, type, date, status FROM leaves ORDER BY id DESC LIMIT 15")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        bot.send_message(chat_id, "السجل فارغ.")
        return
        
    text = "📜 آخر 15 عملية:\n\n"
    for r in rows:
        text += f"▪️ {r[0]} | {r[1]} | {r[2]} | {r[3]}\n"
    bot.send_message(chat_id, text)

def show_pending_requests(chat_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, emp_name, type, date, emp_id FROM leaves WHERE status='انتظار'")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        bot.send_message(chat_id, "لا توجد طلبات معلقة حالياً.")
        return

    for r in rows:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ قبول", callback_data=f"decide_approve_{r[0]}_{r[4]}"),
                   types.InlineKeyboardButton("❌ رفض", callback_data=f"decide_reject_{r[0]}_{r[4]}"))
        bot.send_message(chat_id, f"⏳ طلب معلق:\nالموظف: {r[1]}\nالنوع: {r[2]}\nالتاريخ: {r[3]}", reply_markup=markup)

# =====================
# RUN
# =====================
if __name__ == "__main__":
    init_db()
    print("🚀 البوت يعمل الآن...")
    bot.infinity_polling(skip_pending=True)
