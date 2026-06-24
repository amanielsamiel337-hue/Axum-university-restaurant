from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, MessageHandler,
                          CommandHandler, CallbackQueryHandler,
                          filters, ContextTypes)
from groq import Groq
from dotenv import load_dotenv
import sqlite3
import json
import os
from datetime import datetime
import random

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
# RESTAURANT PAYMENT INFO
# Store this once - your actual Telebirr number
# ============================================

RESTAURANT_TELEBIRR_NUMBER = "0991004736"  
RESTAURANT_NAME = "AXUM UNI Student Restaurant"

# The Telegram group ID for restaurant staff
STAFF_GROUP_ID = os.getenv("STAFF_GROUP_ID")


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
            timestamp TEXT NOT NULL,
            pickup_code TEXT
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
        FROM menu
        WHERE available = 1
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
    
    # NOTICE - we do NOT deduct portions yet
    # We only deduct portions once payment is confirmed
    # This protects against students reserving food they never pay for
    
    cursor.execute("""
        INSERT INTO orders
        (student_id, student_name, food_name, quantity, 
         price_total, status, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (student_id, student_name, food_name, quantity,
          price_total, "awaiting_payment",
          datetime.now().strftime("%Y-%m-%d %H:%M")))
    
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id

def get_student_orders(student_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT order_id, food_name, quantity, price_total, status, timestamp
        FROM orders
        WHERE student_id = ?
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


def add_menu_item(food_name, price):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO menu VALUES (?, ?, ?, ?)
    """, (food_name, price, 999, 1))
    conn.commit()
    conn.close()

def update_menu_price(food_name, price):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE menu SET price = ?
        WHERE food_name = ?
    """, (price, food_name))
    conn.commit()
    conn.close()

def remove_menu_item(food_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM menu WHERE food_name = ?
    """, (food_name,))
    conn.commit()
    conn.close()



    # ============================================
# NEW HELPER FUNCTION
# Sends a message to any student by their ID
# Does not require them to have messaged first
# ============================================

async def notify_student(bot, student_id, message_text):
    # bot is the Telegram bot object - it can send messages
    # to ANY chat ID, not just ones that just messaged us
    try:
        await bot.send_message(
            chat_id=student_id,
            text=message_text,
            parse_mode="Markdown"
        )
        return True
    except Exception as e:
        # This can fail if the student blocked the bot
        # or never started a chat with it - we handle that gracefully
        print(f"Could not notify student {student_id}: {e}")
        return False
    



def generate_pickup_code(order_id):
    code = str(random.randint(100, 999))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE orders SET pickup_code = ?
        WHERE order_id = ?
    """, (code, order_id))
    conn.commit()
    conn.close()
    return code


# ============================================
# UPDATED FUNCTION — needed to get the
# student_id and food details for an order
# ============================================

def get_order_details(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT student_id, student_name, food_name, quantity
        FROM orders
        WHERE order_id = ?
    """, (order_id,))
    row = cursor.fetchone()
    conn.close()
    return row
    # Returns None if no order with that ID exists


    
# ============================================
# NEW FUNCTION — confirm payment
# Manager runs this after checking their Telebirr 
# app and seeing the money actually arrived
# ============================================

def confirm_payment(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    
    # First get the order details so we know
    # what food and quantity to deduct
    cursor.execute("""
        SELECT food_name, quantity, status
        FROM orders
        WHERE order_id = ?
    """, (order_id,))
    result = cursor.fetchone()
    
    if result is None:
        conn.close()
        return False, "Order not found"
    
    food_name, quantity, current_status = result
    
    # IMPORTANT CHECK - only confirm if it's actually 
    # awaiting payment - this enforces our state machine
    # Cannot confirm an order that's already confirmed
    if current_status != "awaiting_payment":
        conn.close()
        return False, f"Order is already {current_status}"
    
    # Check portions are still available 
    # (someone else might have taken the last one)
    cursor.execute("""
        SELECT portions_left
        FROM menu 
        WHERE food_name = ?
    """, (food_name,))
    menu_result = cursor.fetchone()
    
    if menu_result[0] < quantity:
        conn.close()
        return False, "Not enough portions left anymore"
    
    # Now deduct portions and update status together
    cursor.execute("""
        UPDATE menu
        SET portions_left = portions_left - ?
        WHERE food_name = ?
    """, (quantity, food_name))
    
    cursor.execute("""
        UPDATE orders
        SET status = 'confirmed' 
        WHERE order_id = ?
    """, (order_id,))
    
    conn.commit()
    conn.close()
    return True, "Payment confirmed"



# ============================================
# NEW FUNCTION — get orders awaiting payment
# So manager can see what needs checking
# ============================================

def get_awaiting_payment_orders():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT order_id, student_id, student_name, 
               food_name, quantity, price_total, timestamp
        FROM orders 
        WHERE status = 'awaiting_payment'
        ORDER BY timestamp ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


# ============================================
# NOTIFY STAFF GROUP
# Called every time a new order is placed
# Posts the order into the staff Telegram group
# ============================================

async def notify_staff_group(bot, order_id, student_name, food_name, quantity, price_total):
    if not STAFF_GROUP_ID:
        return

    # Inline button — manager taps instead of typing /confirmpay
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ Confirm Payment",
            callback_data=f"confirmpay_{order_id}"
        )]
    ])

    message = (
        f"🆕 *New Order — #{order_id}*\n\n"
        f"👤 {student_name}\n"
        f"🍲 {food_name} x{quantity}\n"
        f"💰 {price_total} birr\n\n"
        f"💳 Check Telebirr, then tap the button below."
    )

    try:
        await bot.send_message(
            chat_id=STAFF_GROUP_ID,
            text=message,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"Could not notify staff group: {e}")

# ============================================
# OPENING HOURS CHECK
# Restaurant open 6:00 AM to 10:00 PM
# ============================================

def is_restaurant_open():
    now = datetime.now()
    return 6 <= now.hour < 23


# ============================================
# QUEUE POSITION
# Counts how many confirmed orders are ahead
# of this one — used to estimate wait time
# ============================================

def get_queue_position(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM orders
        WHERE status = 'confirmed'
        AND order_id < ?
    """, (order_id,))
    count = cursor.fetchone()[0]
    conn.close()
    # Position is count of orders ahead, plus 1 for themselves
    return count + 1

# ============================================
# AI BRAIN
# ============================================

async def process_message(student_id, student_name, student_message,context):
    history = load_conversation(student_id)
    menu = load_menu()

    menu_text = "\n".join([
        f"- {food}: {details['price']} birr ({details['portions_left']} left)"
        for food, details in menu.items()
    ])

    # Block orders outside opening hours
    if not is_restaurant_open():
        return "Sorry, the restaurant is closed right now. We are open every day from 6:00 AM to 10:00 PM. Come back then! 🕕"

    save_message(student_id, "user", student_message)
    history.append({"role": "user", "content": student_message})

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": f"""You are a friendly assistant for a student restaurant at an Ethiopian university.

                    About this restaurant:
                    - Students get free injera from the government cafe
                    - This restaurant sells wot (stew) dishes only — no injera, no drinks, no sides
                    - Students order wot only, or wot to eat with their own injera
                    - Be warm, brief, and helpful

                    Today's menu (these are the ONLY valid food_name values you may ever output, copied EXACTLY as written below):
                    {menu_text}

                    MATCHING RULES — read carefully:
                    - Students will often misspell, abbreviate, or phrase food names differently than the menu
                    (e.g. "shiro", "shero wot", "doro", "misir", "shiro wot with injera", "shiro only", "shiro pls").
                    - You must map ANY such input to the closest matching item name EXACTLY as it appears in the menu above.
                    - The food_name field must be copied character-for-character from the menu. Never modify it,
                    never add or remove words, never append things like "only", "with injera", "please", "x2", etc.
                    - Words like "only" or "with injera" describe how the student plans to eat it — they are NOT part
                    of the dish name. Mention that detail only in your FRIENDLY sentence, never in food_name.
                    - If the student's request doesn't clearly match exactly one menu item, do NOT output ORDER_JSON.
                    Instead ask a short clarifying question, e.g. "We don't have that — did you mean Shiro Wot or Misir Wot?"

                    OUTPUT FORMAT RULES:
                    - When a student places a CLEAR order matching exactly one menu item, respond in EXACTLY this
                    format and nothing else — no text before FRIENDLY, no text after ORDER_JSON:
                    FRIENDLY: [short warm confirmation, you may mention injera here]
                    ORDER_JSON: {{"food_name": "exact menu name", "quantity": 1}}
                    - Both lines are required together every time an order is confirmed. Never send ORDER_JSON
                    without the FRIENDLY line above it. Never send ORDER_JSON alone.
                    - If there is no clear, confirmed order yet, respond in 1–2 sentences only, with no FRIENDLY
                    or ORDER_JSON labels at all.

                    EXAMPLES:
                    Student: "shiro only please"  (menu has "Shiro Wot")
                    FRIENDLY: Sure! One Shiro Wot, no injera — got it!
                    ORDER_JSON: {{"food_name": "Shiro Wot", "quantity": 1}}

                    Student: "misir wot with injera, 2"  (menu has "Misir Wot")
                    FRIENDLY: Great choice! Two Misir Wot with injera coming up.
                    ORDER_JSON: {{"food_name": "Misir Wot", "quantity": 2}}

                    Student: "what do you have today"
                    What's on today's menu, no ORDER_JSON — just answer normally in 1-2 sentences."""
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
            menu = load_menu()

            if food_name in menu and menu[food_name]["portions_left"] >= quantity:
                price = menu[food_name]["price"] * quantity
                order_id = save_order(
                    student_id, student_name, 
                    food_name, quantity, price)
                
                # Notify staff group the moment order is placed
                await notify_staff_group(
                    context.bot, order_id, student_name,
                    food_name, quantity, price
                )


                final_reply = (
                    f"{friendly}\n\n"
                    f"📝 Order #{order_id} reserved\n"
                    f"🍲 {food_name} x{quantity}\n"
                    f"💰 Total: {price} birr\n\n"
                    f"💳 *To confirm your order, pay now:*\n"
                    f"Send {price} birr via Telebirr to:\n"
                    f"*{RESTAURANT_TELEBIRR_NUMBER}*\n"
                    f"({RESTAURANT_NAME})\n\n"
                    f"After paying, send me your transaction ID "
                    f"or a screenshot, mentioning Order #{order_id}"
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
        "👋 Welcome to the AU Restaurant Bot!\n\n"
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
        


async def addmenu_command(update: Update,
                          context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.message.from_user.id):
        await update.message.reply_text("Staff only.")
        return
    try:
        price = float(context.args[-1])
        food_name = " ".join(context.args[:-1])
        add_menu_item(food_name, price)
        await update.message.reply_text(
            f"✅ *{food_name}* added to menu at {price} birr.",
            parse_mode="Markdown"
        )
    except:
        await update.message.reply_text(
            "Usage: /addmenu [food name] [price]\n"
            "Example: /addmenu Misir Wot 18"
        )

async def updatemenu_command(update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.message.from_user.id):
        await update.message.reply_text("Staff only.")
        return
    try:
        price = float(context.args[-1])
        food_name = " ".join(context.args[:-1])
        update_menu_price(food_name, price)
        await update.message.reply_text(
            f"✅ *{food_name}* price updated to {price} birr.",
            parse_mode="Markdown"
        )
    except:
        await update.message.reply_text(
            "Usage: /updatemenu [food name] [price]\n"
            "Example: /updatemenu Shiro Wot 15"
        )

async def removemenu_command(update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.message.from_user.id):
        await update.message.reply_text("Staff only.")
        return
    try:
        food_name = " ".join(context.args)
        remove_menu_item(food_name)
        await update.message.reply_text(
            f"🗑 *{food_name}* removed from menu.",
            parse_mode="Markdown"
        )
    except:
        await update.message.reply_text(
            "Usage: /removemenu [food name]\n"
            "Example: /removemenu Misir Wot"
        )

 
# ============================================
# NEW MANAGER COMMAND — /pending
# Shows orders waiting for payment verification
# ============================================

async def pending_command(update: Update,
                          context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(update.message.from_user.id):
        await update.message.reply_text("Staff only.")
        return
    
    pending = get_awaiting_payment_orders()
    
    if not pending:
        await update.message.reply_text(
            "No orders awaiting payment confirmation.")
        return
    
    text = "💳 *Awaiting Payment Confirmation*\n\n"
    for o in pending:
        text += (f"Order #{o[0]} — {o[6]}\n"
                 f"👤 {o[2]}\n"
                 f"🍲 {o[3]} x{o[4]} — {o[5]} birr\n\n")
    text += "Check your Telebirr app, then use:\n/confirmpay [order number]"
    
    await update.message.reply_text(text, parse_mode="Markdown")



# ============================================
# PHOTO HANDLER
# When a student sends a payment screenshot,
# forward it to the staff group automatically
# ============================================

async def handle_photo(update: Update,
                       context: ContextTypes.DEFAULT_TYPE):
    student_name = update.message.from_user.first_name or "Student"
    student_id = str(update.message.from_user.id)

    # Tell the student we got it
    await update.message.reply_text(
        "📸 Screenshot received! Staff will verify your payment shortly."
    )

    # Forward the photo to the staff group
    if STAFF_GROUP_ID:
        try:
            await context.bot.forward_message(
                chat_id=STAFF_GROUP_ID,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
            # Send a note so staff knows who sent it
            await context.bot.send_message(
                chat_id=STAFF_GROUP_ID,
                text=f"👆 Payment screenshot from *{student_name}*",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Could not forward screenshot: {e}")



# ============================================
# INLINE BUTTON HANDLER
# Handles button taps from the staff group
# ============================================

async def handle_button(update: Update,
                        context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Only managers can use these buttons
    if not is_manager(query.from_user.id):
        await query.answer("Staff only.", show_alert=True)
        return

    data = query.data

    # ── Confirm Payment button ──
    if data.startswith("confirmpay_"):
        order_id = int(data.split("_")[1])
        success, message = confirm_payment(order_id)

        if success:
            order = get_order_details(order_id)
            student_id, student_name, food_name, quantity = order
            code = generate_pickup_code(order_id)
            position = get_queue_position(order_id)
            wait_minutes = (position - 1) * 25
            wait_text = "Your order is first in queue! 🎉" if position == 1 else f"Estimated wait: ~{wait_minutes} minutes"

            # Notify student
            await notify_student(
                context.bot, student_id,
                f"✅ *Payment confirmed!*\n\n"
                f"Order #{order_id} — {food_name} x{quantity}\n\n"
                f"📊 You are *#{position}* in queue\n"
                f"⏱ {wait_text}\n\n"
                f"🎫 Your pickup code: *AU-{code}*\n"
                f"Show this code at the counter when collecting your food.\n"
                f"We'll notify you when it's ready for pickup."
            )

            # Update the staff group message with Mark Ready button
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🍲 Mark as Ready",
                    callback_data=f"ready_{order_id}"
                )]
            ])
            await query.edit_message_text(
                f"✅ *Payment Confirmed — #{order_id}*\n\n"
                f"👤 {student_name}\n"
                f"🍲 {food_name} x{quantity}\n\n"
                f"Tap below when food is ready.",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await query.answer(message, show_alert=True)

    # ── Mark as Ready button ──
    elif data.startswith("ready_"):
        order_id = int(data.split("_")[1])
        order = get_order_details(order_id)

        if order is None:
            await query.answer("Order not found.", show_alert=True)
            return

        student_id, student_name, food_name, quantity = order
        mark_order_ready(order_id)

        # Notify student food is ready
        await notify_student(
            context.bot, student_id,
            f"🍲 *Your order is ready!*\n\n"
            f"Order #{order_id} — {food_name} x{quantity}\n"
            f"Please come collect it at the counter now."
        )

        # Add collected button so staff can verify pickup
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📦 Mark as Collected",
                callback_data=f"collected_{order_id}"
            )]
        ])
        await query.edit_message_text(
            f"🍲 *Ready for Pickup — #{order_id}*\n\n"
            f"👤 {student_name}\n"
            f"🍲 {food_name} x{quantity}\n\n"
            f"✅ Student has been notified. Tap below when collected.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    # ── Mark as Collected button ──
    elif data.startswith("collected_"):
        order_id = int(data.split("_")[1])
        order = get_order_details(order_id)

        if order is None:
            await query.answer("Order not found.", show_alert=True)
            return

        student_id, student_name, food_name, quantity = order

        # Mark as collected in database
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE orders SET status = 'collected'
            WHERE order_id = ?
        """, (order_id,))
        conn.commit()
        conn.close()

        # Final update — no more buttons
        await query.edit_message_text(
            f"✅ *Collected — #{order_id}*\n\n"
            f"👤 {student_name}\n"
            f"🍲 {food_name} x{quantity}\n\n"
            f"Order complete.",
            parse_mode="Markdown"
        )

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
        reply = await process_message(student_id, student_name, student_message, context)
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
    print("AU Restaurant Bot starting...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Student commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myorders", myorders_command))

    # Manager commands
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("soldout", soldout_command))
    app.add_handler(CommandHandler("addmanager", addmanager_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("addmenu", addmenu_command))
    app.add_handler(CommandHandler("updatemenu", updatemenu_command))
    app.add_handler(CommandHandler("removemenu", removemenu_command))

    # Free text
    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("Bot is running 24/7.")
    app.run_polling()

main()