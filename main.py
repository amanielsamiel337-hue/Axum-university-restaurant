from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (Application, MessageHandler,
                          CommandHandler, filters,
                          ContextTypes)
from groq import Groq
from dotenv import load_dotenv
import sqlite3
import json
import os
from datetime import datetime

# Load environment variables from .env file
# This reads your secrets without them appearing in your code
load_dotenv()

# Read secrets from environment — not hardcoded
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MANAGER_ID = os.getenv("MANAGER_ID")

# If any secret is missing — tell us clearly
if not TELEGRAM_TOKEN or not GROQ_API_KEY or not MANAGER_ID:
    raise ValueError("Missing required environment variables. Check your .env file.")

groq_client = Groq(api_key=GROQ_API_KEY)

# Database path — works correctly on any computer or server
DB_PATH = os.path.join(os.path.dirname(__file__), "restaurant.db")

# ============================================
# DATABASE FUNCTIONS
# ============================================

def get_connection():
    # One function to open database connection
    # Makes our code cleaner — we call this instead
    # of writing sqlite3.connect everywhere
    return sqlite3.connect(DB_PATH)

def setup_database():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY,
            student_id TEXT NOT NULL,
            student_name TEXT,
            food_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price_total REAL NOT NULL,
            status TEXT DEFAULT 'confirmed',
            timestamp TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY,
            student_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu (
            food_name TEXT PRIMARY KEY,
            price REAL NOT NULL,
            portions_left INTEGER NOT NULL,
            available INTEGER DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS managers (
            manager_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            added_date TEXT NOT NULL
        )
    """)
    # Managers table — solves the single point of failure
    # from your Day 11 answer. Multiple managers now possible.

    conn.commit()
    conn.close()

def seed_initial_data():
    conn = get_connection()
    cursor = conn.cursor()

    # Seed real menu
    real_menu = [
        ("Key Wot only", 15, 50, 1),
        ("Key Wot with Injera", 25, 50, 1),
        ("Shiro Wot only", 12, 40, 1),
        ("Shiro Wot with Injera", 22, 40, 1),
        ("Tikel Gomen only", 10, 30, 1),
        ("Tikel Gomen with Injera", 20, 30, 1),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO menu VALUES (?, ?, ?, ?)",
        real_menu
    )

    # Seed the first manager from environment variable
    cursor.execute("""
        INSERT OR IGNORE INTO managers VALUES (?, ?, ?)
    """, (MANAGER_ID, "Head Manager",
          datetime.now().strftime("%Y-%m-%d")))

    conn.commit()
    conn.close()

def is_manager(telegram_id):
    # Now checks the database instead of one hardcoded ID
    # Any ID in the managers table is a valid manager
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT manager_id
           FROM managers 
           WHERE manager_id = ?
        """,
        (str(telegram_id),)
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None

def add_manager(telegram_id, name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO managers VALUES (?, ?, ?)
    """, (str(telegram_id), name,
          datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def load_menu():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT food_name, price, portions_left
        FROM menu WHERE
        available = 1
    """)
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        seed_initial_data()
        return load_menu()
    return {row[0]: {"price": row[1],
                     "portions_left": row[2]} for row in rows}

def save_message(student_id, role, content):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO conversations (student_id, role, content, timestamp)
        VALUES (?, ?, ?, ?)
    """, (student_id, role, content,
          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def load_conversation(student_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role, content FROM conversations
        WHERE student_id = ?
        ORDER BY timestamp ASC
        LIMIT 20
    """, (student_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in rows]

def save_order(student_id, student_name, food_name, quantity, price_total):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE menu
        SET portions_left = portions_left - ?
        WHERE food_name = ?
    """, (quantity, food_name))
    cursor.execute("""
        INSERT INTO orders
        (student_id, student_name, food_name, quantity, price_total, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (student_id, student_name, food_name, quantity,
          price_total, datetime.now().strftime("%Y-%m-%d %H:%M")))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id

def get_student_orders(student_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT order_id, food_name, quantity, price_total, status, timestamp
        FROM orders WHERE student_id = ?
        ORDER BY timestamp DESC
        LIMIT 5
    """, (student_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_all_pending_orders():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT order_id, student_name, food_name,
               quantity, price_total, timestamp
        FROM orders
        WHERE status = 'confirmed'
        ORDER BY timestamp ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def mark_order_ready(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE orders
        SET status = 'ready' 
        WHERE order_id = ?
    """, (order_id,))
    conn.commit()
    conn.close()

def update_availability(food_name, available):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE menu
        SET available = ? 
        WHERE food_name = ?
    """, (available, food_name))
    conn.commit()
    conn.close()

# ============================================
# AI BRAIN
# ============================================

def process_message(student_id, student_name, student_message):
    history = load_conversation(student_id)
    menu = load_menu()

    menu_text = "\n".join([
        f"- {food}: {details['price']} birr ({details['portions_left']} left)"
        for food, details in menu.items()
    ])

    save_message(student_id, "user", student_message)
    history.append({"role": "user", "content": student_message})

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": f"""You are a friendly assistant for a 
                    student restaurant at an Ethiopian university.
                    
                    About this restaurant:
                    - Students get free injera from the government cafe
                    - This restaurant sells wot dishes only
                    - Students choose wot only or wot with injera
                    - Be warm, brief, and helpful
                    
                    Today's menu:
                    {menu_text}
                    
                    When student places a clear order:
                    FRIENDLY: [short confirmation]
                    ORDER_JSON: {{"food_name": "exact name", "quantity": 1}}
                    
                    Otherwise respond in 1 to 2 sentences."""
                }
            ] + history
        )
        ai_reply = response.choices[0].message.content

    except Exception as e:
        # If the AI call fails — give a friendly error
        # The app does not crash — it handles the problem gracefully
        print(f"AI error: {e}")
        ai_reply = "Sorry, I am having trouble right now. Please try again in a moment."

    if "ORDER_JSON:" in ai_reply:
        parts = ai_reply.split("ORDER_JSON:")
        friendly = parts[0].replace("FRIENDLY:", "").strip()
        try:
            order_data = json.loads(parts[1].strip())
            food_name = order_data["food_name"]
            quantity = order_data["quantity"]

            if food_name in menu and menu[food_name]["portions_left"] >= quantity:
                price = menu[food_name]["price"] * quantity
                order_id = save_order(
                    student_id, student_name,
                    food_name, quantity, price)
                final_reply = (
                    f"{friendly}\n\n"
                    f"✅ Order #{order_id} confirmed\n"
                    f"🍲 {food_name} x{quantity}\n"
                    f"💰 {price} birr\n"
                    f"📱 Show this message at the counter"
                )
            else:
                final_reply = "Sorry, that item is not available right now."
        except Exception as e:
            print(f"Order processing error: {e}")
            final_reply = friendly if friendly else ai_reply
    else:
        final_reply = ai_reply

    save_message(student_id, "assistant", final_reply)
    return final_reply

# ============================================
# STUDENT COMMANDS
# ============================================

async def start_command(update: Update,
                        context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["/menu", "/myorders"], ["/help"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "👋 Welcome to the AAU Restaurant Bot!\n\n"
        "I help you pre-order your wot and skip the queue.\n\n"
        "Just tell me what you want or use the buttons below.",
        reply_markup=reply_markup
    )

async def menu_command(update: Update,
                       context: ContextTypes.DEFAULT_TYPE):
    menu = load_menu()
    text = "📋 *Today's Menu*\n\n"
    for food, details in menu.items():
        warning = " ⚠️" if details['portions_left'] < 5 else ""
        text += (f"*{food}*\n"
                 f"{details['price']} birr — "
                 f"{details['portions_left']} portions left"
                 f"{warning}\n\n")
    await update.message.reply_text(text, parse_mode="Markdown")

async def myorders_command(update: Update,
                           context: ContextTypes.DEFAULT_TYPE):
    student_id = str(update.message.from_user.id)
    orders = get_student_orders(student_id)
    if not orders:
        await update.message.reply_text(
            "No orders yet. Tell me what you want to order!")
        return
    text = "📦 *Your Recent Orders*\n\n"
    for o in orders:
        emoji = "🍽" if o[4] == "ready" else "⏳"
        text += (f"Order #{o[0]}\n"
                 f"{o[1]} x{o[2]} — {o[3]} birr\n"
                 f"{emoji} {o[4]} — {o[5]}\n\n")
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_command(update: Update,
                       context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just type naturally. Examples:\n\n"
        "• 'What wot is available?'\n"
        "• 'I want shiro wot only'\n"
        "• 'Give me key wot with injera'\n\n"
        "Use /menu to see today's options."
    )

# ============================================
# MANAGER COMMANDS
# ============================================

async def orders_command(update: Update,
                         context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.message.from_user.id):
        await update.message.reply_text("Staff only.")
        return
    pending = get_all_pending_orders()
    if not pending:
        await update.message.reply_text("No pending orders.")
        return
    text = "📋 *Pending Orders*\n\n"
    for o in pending:
        text += (f"#{o[0]} — {o[5]}\n"
                 f"👤 {o[1]}\n"
                 f"🍲 {o[2]} x{o[3]} — {o[4]} birr\n\n")
    await update.message.reply_text(text, parse_mode="Markdown")

async def ready_command(update: Update,
                        context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.message.from_user.id):
        await update.message.reply_text("Staff only.")
        return
    try:
        order_id = int(context.args[0])
        mark_order_ready(order_id)
        await update.message.reply_text(
            f"✅ Order #{order_id} marked as ready.")
    except:
        await update.message.reply_text(
            "Usage: /ready [order number]")

async def soldout_command(update: Update,
                          context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.message.from_user.id):
        await update.message.reply_text("Staff only.")
        return
    try:
        food_name = " ".join(context.args)
        update_availability(food_name, 0)
        await update.message.reply_text(
            f"⛔ {food_name} marked as sold out.")
    except:
        await update.message.reply_text(
            "Usage: /soldout [food name]")

async def addmanager_command(update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
    # /addmanager 123456789 Dawit
    # Solves the single point of failure problem
    if not is_manager(update.message.from_user.id):
        await update.message.reply_text("Staff only.")
        return
    try:
        new_id = context.args[0]
        new_name = " ".join(context.args[1:])
        add_manager(new_id, new_name)
        await update.message.reply_text(
            f"✅ {new_name} added as manager.")
    except:
        await update.message.reply_text(
            "Usage: /addmanager [telegram_id] [name]")

# ============================================
# MESSAGE HANDLER
# ============================================

async def handle_message(update: Update,
                         context: ContextTypes.DEFAULT_TYPE):
    student_id = str(update.message.from_user.id)
    student_name = update.message.from_user.first_name or "Student"
    student_message = update.message.text

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        reply = process_message(student_id, student_name, student_message)
    except Exception as e:
        print(f"Unexpected error: {e}")
        reply = "Something went wrong. Please try again."

    await update.message.reply_text(reply)

# ============================================
# MAIN — START THE BOT
# ============================================

def main():
    setup_database()
    seed_initial_data()
    print("AAU Restaurant Bot starting...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Student commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("help", help_command))

    # Manager commands
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("ready", ready_command))
    app.add_handler(CommandHandler("soldout", soldout_command))
    app.add_handler(CommandHandler("addmanager", addmanager_command))

    # Free text
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    print("Bot is running 24/7.")
    app.run_polling()

main()