import telebot
from telebot import types
import sqlite3
import pandas as pd
from datetime import datetime
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
import os
from dotenv import load_dotenv

# --- Security & Core Settings ---
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = telebot.TeleBot(BOT_TOKEN)

# Dictionary to store temporary user sessions during the leave request process
user_sessions = {}
admin_sessions = {}

# --- 1. Database Management ---
def init_db():
    conn = sqlite3.connect('hr_system.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            telegram_id INTEGER UNIQUE
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leaves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_name TEXT,
            emp_id INTEGER,
            type TEXT,
            duration TEXT,
            date TEXT,
            time TEXT,
            reason TEXT,
            status TEXT DEFAULT 'انتظار',
            timestamp TEXT
        )
    ''')
    
    # Optional: Automatically insert the admin as an employee if not exists
    cursor.execute("INSERT OR IGNORE INTO employees (name, telegram_id) VALUES (?, ?)", ("Admin", HR_ADMIN_ID))
    
    conn.commit()
    conn.close()

def is_registered(telegram_id):
    conn = sqlite3.connect('hr_system.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM employees WHERE telegram_id = ?", (telegram_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

# --- 2. Keyboards ---
def main_menu_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📝 تقديم طلب إجازة"), types.KeyboardButton("🗂 سجل إجازاتي"))
    return markup

def admin_menu_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("➕ إضافة موظف"), types.KeyboardButton("👥 إدارة الموظفين"))
    markup.add(types.KeyboardButton("⏳ الطلبات المعلقة"), types.KeyboardButton("📊 تصدير Excel"))
    markup.add(types.KeyboardButton("🏠 القائمة الرئيسية"))
    return markup

def leave_types_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    types_list = ["إدارية", "مرضية", "غير مدفوعة", "زواج", "حج", "❌ إلغاء"]
    markup.add(*[types.KeyboardButton(t) for t in types_list])
    return markup

def duration_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("ساعية ⏰"), types.KeyboardButton("يومية 📅"), types.KeyboardButton("❌ إلغاء"))
    return markup

# --- 3. User Interface (Employee) ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        user_name = is_registered(message.chat.id)
        if user_name or message.chat.id == HR_ADMIN_ID:
            bot.send_message(message.chat.id, f"أهلاً بك {user_name or 'أيها المدير'} في نظام إدارة الإجازات.", reply_markup=main_menu_keyboard())
        else:
            bot.send_message(message.chat.id, "عذراً، أنت غير مسجل في النظام. يرجى مراجعة الإدارة.")
    except Exception as e:
        print(f"Error in start: {e}")

@bot.message_handler(func=lambda message: message.text == "📝 تقديم طلب إجازة")
def start_leave_request(message):
    if not is_registered(message.chat.id): return
    user_sessions[message.chat.id] = {'step': 'type'}
    bot.send_message(message.chat.id, "اختر نوع الإجازة:", reply_markup=leave_types_keyboard())

@bot.message_handler(func=lambda message: message.chat.id in user_sessions and user_sessions[message.chat.id].get('step') == 'type')
def process_leave_type(message):
    if message.text == "❌ إلغاء":
        user_sessions.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "تم الإلغاء.", reply_markup=main_menu_keyboard())
        return
    user_sessions[message.chat.id]['type'] = message.text
    user_sessions[message.chat.id]['step'] = 'duration'
    bot.send_message(message.chat.id, "اختر مدة الإجازة:", reply_markup=duration_keyboard())

@bot.message_handler(func=lambda message: message.chat.id in user_sessions and user_sessions[message.chat.id].get('step') == 'duration')
def process_leave_duration(message):
    if message.text == "❌ إلغاء":
        user_sessions.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "تم الإلغاء.", reply_markup=main_menu_keyboard())
        return
    user_sessions[message.chat.id]['duration'] = message.text
    user_sessions[message.chat.id]['step'] = 'date'
    
    calendar, step = DetailedTelegramCalendar().build()
    bot.send_message(message.chat.id, f"اختر التاريخ {LSTEP[step]}:", reply_markup=calendar)

@bot.callback_query_handler(func=DetailedTelegramCalendar.func())
def process_calendar(call):
    result, key, step = DetailedTelegramCalendar().process(call.data)
    if not result and key:
        bot.edit_message_text(f"اختر {LSTEP[step]}", call.message.chat.id, call.message.message_id, reply_markup=key)
    elif result:
        bot.edit_message_text(f"تم اختيار التاريخ: {result}", call.message.chat.id, call.message.message_id)
        user_sessions[call.message.chat.id]['date'] = result
        user_sessions[call.message.chat.id]['step'] = 'reason'
        bot.send_message(call.message.chat.id, "الرجاء كتابة سبب الإجازة (أو اكتب 'لا يوجد'):")

@bot.message_handler(func=lambda message: message.chat.id in user_sessions and user_sessions[message.chat.id].get('step') == 'reason')
def process_leave_reason(message):
    try:
        session = user_sessions[message.chat.id]
        session['reason'] = message.text
        emp_name = is_registered(message.chat.id)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Save to Database
        conn = sqlite3.connect('hr_system.db')
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO leaves 
                          (emp_name, emp_id, type, duration, date, time, reason, timestamp) 
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                       (emp_name, message.chat.id, session['type'], session['duration'], 
                        session['date'], "N/A", session['reason'], timestamp))
        leave_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        bot.send_message(message.chat.id, "✅ تم تسجيل طلبك بنجاح وإرساله للمدير.", reply_markup=main_menu_keyboard())
        
        # Notify Admin
        admin_msg = f"📩 طلب إجازة جديد!\n\nالموظف: {emp_name}\nالنوع: {session['type']}\nالمدة: {session['duration']}\nالتاريخ: {session['date']}\nالسبب: {session['reason']}"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{leave_id}"),
                   types.InlineKeyboardButton("❌ رفض", callback_data=f"reject_{leave_id}"))
        
        bot.send_message(HR_ADMIN_ID, admin_msg, reply_markup=markup)
        user_sessions.pop(message.chat.id, None)
        
    except Exception as e:
        bot.send_message(message.chat.id, "حدث خطأ أثناء معالجة الطلب.")
        print(f"Error: {e}")

# --- 4. Admin Panel ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.chat.id == HR_ADMIN_ID:
        bot.send_message(message.chat.id, "👨‍💼 أهلاً بك في لوحة تحكم المدير:", reply_markup=admin_menu_keyboard())

@bot.message_handler(func=lambda message: message.text == "🏠 القائمة الرئيسية")
def back_to_main(message):
    send_welcome(message)

# Handle Add Employee
@bot.message_handler(func=lambda message: message.text == "➕ إضافة موظف" and message.chat.id == HR_ADMIN_ID)
def add_employee_start(message):
    admin_sessions[message.chat.id] = {'step': 'add_emp_name'}
    bot.send_message(message.chat.id, "أدخل اسم الموظف الجديد:")

@bot.message_handler(func=lambda message: message.chat.id in admin_sessions and admin_sessions[message.chat.id].get('step') == 'add_emp_name')
def add_employee_name(message):
    admin_sessions[message.chat.id]['emp_name'] = message.text
    admin_sessions[message.chat.id]['step'] = 'add_emp_id'
    bot.send_message(message.chat.id, "أدخل Telegram ID الخاص بالموظف:")

@bot.message_handler(func=lambda message: message.chat.id in admin_sessions and admin_sessions[message.chat.id].get('step') == 'add_emp_id')
def add_employee_id(message):
    try:
        emp_id = int(message.text)
        emp_name = admin_sessions[message.chat.id]['emp_name']
        
        conn = sqlite3.connect('hr_system.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO employees (name, telegram_id) VALUES (?, ?)", (emp_name, emp_id))
        conn.commit()
        conn.close()
        
        bot.send_message(message.chat.id, f"✅ تم إضافة الموظف {emp_name} بنجاح.", reply_markup=admin_menu_keyboard())
    except ValueError:
        bot.send_message(message.chat.id, "❌ الرجاء إدخال ID صحيح (أرقام فقط).")
    except sqlite3.IntegrityError:
        bot.send_message(message.chat.id, "❌ هذا الموظف موجود بالفعل في قاعدة البيانات.")
    except Exception as e:
        bot.send_message(message.chat.id, f"حدث خطأ: {e}")
    finally:
        admin_sessions.pop(message.chat.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_') or call.data.startswith('reject_'))
def handle_leave_decision(call):
    try:
        data_parts = call.data.split('_')
        action = data_parts[0]
        leave_id = data_parts[1]
        
        status = "مقبول" if action == "approve" else "مرفوض"
        
        conn = sqlite3.connect('hr_system.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE leaves SET status = ? WHERE id = ?", (status, leave_id))
        cursor.execute("SELECT emp_id, emp_name FROM leaves WHERE id = ?", (leave_id,))
        result = cursor.fetchone()
        conn.commit()
        conn.close()
        
        if result:
            emp_id, emp_name = result
            bot.edit_message_text(f"تم {status} طلب الموظف {emp_name}.", call.message.chat.id, call.message.message_id)
            bot.send_message(emp_id, f"إشعار إداري: تم {status} طلب إجازتك رقم {leave_id}.")
        else:
            bot.edit_message_text("❌ لم يتم العثور على الطلب.", call.message.chat.id, call.message.message_id)
            
    except Exception as e:
        print(f"Decision Error: {e}")

@bot.message_handler(func=lambda message: message.text == "📊 تصدير Excel" and message.chat.id == HR_ADMIN_ID)
def export_excel(message):
    try:
        conn = sqlite3.connect('hr_system.db')
        df = pd.read_sql_query("SELECT * FROM leaves", conn)
        conn.close()
        
        if df.empty:
            bot.send_message(message.chat.id, "لا توجد إجازات لتصديرها حالياً.")
            return
            
        file_name = 'leaves_report.xlsx'
        df.to_excel(file_name, index=False)
        
        with open(file_name, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="📊 تقرير الإجازات الكامل")
    except Exception as e:
        bot.send_message(message.chat.id, f"حدث خطأ أثناء التصدير: {e}")

# --- 5. Delete Confirmation Logic (Admin) ---
def send_delete_confirmation(chat_id, emp_id_to_delete, emp_name):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("⚠️ نعم، احذف", callback_data=f"confirm_delete_{emp_id_to_delete}"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel_delete")
    )
    bot.send_message(chat_id, f"هل أنت متأكد من حذف الموظف: {emp_name}؟", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_delete_') or call.data == 'cancel_delete')
def handle_delete_confirmation(call):
    if call.data == 'cancel_delete':
        bot.edit_message_text("✅ تم إلغاء عملية الحذف.", call.message.chat.id, call.message.message_id)
    else:
        emp_id = call.data.split('_')[2]
        conn = sqlite3.connect('hr_system.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM employees WHERE telegram_id = ?", (emp_id,))
        conn.commit()
        conn.close()
        bot.edit_message_text("🗑 تم حذف الموظف بنجاح.", call.message.chat.id, call.message.message_id)

# --- Start Bot ---
if __name__ == "__main__":
    init_db()
    print("Bot is running securely...")
    # Using infinity_polling to prevent the bot from crashing on timeout
    bot.infinity_polling(timeout=10, long_polling_timeout=5)
