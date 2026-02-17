import os
import time
import threading
import psycopg2
import telebot
from queue import Queue
from collections import defaultdict
from telebot.types import (
    InputMediaPhoto,
    InputMediaVideo,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

# =========================================================
# 🔧 CONFIGURATION
# =========================================================

API_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 8046643349  #  Replace with your Telegram ID

bot = telebot.TeleBot(API_TOKEN)

media_groups = defaultdict(list)
waiting_username = set()
broadcast_queue = Queue()

# =========================================================
# 🗄 DATABASE CONNECTION
# =========================================================

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = False

# =========================================================
# 🏗 DATABASE INITIALIZATION
# =========================================================

def init_db():
    with conn.cursor() as c:

        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT UNIQUE,
                banned BOOLEAN DEFAULT FALSE,
                auto_banned BOOLEAN DEFAULT FALSE,
                shadow_banned BOOLEAN DEFAULT FALSE,
                media_count INTEGER DEFAULT 0,
                last_media BIGINT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS banned_words (
                word TEXT PRIMARY KEY
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS message_map (
                bot_message_id BIGINT PRIMARY KEY,
                original_user_id BIGINT,
                receiver_id BIGINT
            )
        """)

        c.execute("""
            INSERT INTO settings (key,value)
            VALUES ('join_open','true')
            ON CONFLICT (key) DO NOTHING
        """)

        conn.commit()

init_db()

# =========================================================
# 📦 DATABASE HELPERS
# =========================================================

# =========================
# 👤 USER MANAGEMENT
# =========================
def get_all_users():
    """
    Return all users who are allowed to receive broadcast.
    Excludes manually banned and auto-banned users.
    """
    with conn.cursor() as c:
        c.execute("""
            SELECT user_id
            FROM users
            WHERE banned=FALSE
              AND auto_banned=FALSE
        """)
        return c.fetchall()


def add_user(user_id):
    """Add user to database."""
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (user_id,)
        )
        conn.commit()


def user_exists(user_id):
    """Check if user exists."""
    with conn.cursor() as c:
        c.execute("SELECT 1 FROM users WHERE user_id=%s", (user_id,))
        return c.fetchone() is not None


def get_username(user_id):
    """Get stored username."""
    with conn.cursor() as c:
        c.execute("SELECT username FROM users WHERE user_id=%s", (user_id,))
        r = c.fetchone()
        return r[0] if r else None


def set_username(user_id, username):
    """Set username."""
    with conn.cursor() as c:
        c.execute(
            "UPDATE users SET username=%s WHERE user_id=%s",
            (username.lower(), user_id)
        )
        conn.commit()


def username_taken(username):
    """Check if username already taken."""
    with conn.cursor() as c:
        c.execute(
            "SELECT 1 FROM users WHERE username=%s",
            (username.lower(),)
        )
        return c.fetchone() is not None


# =========================
# 🔨 BAN SYSTEM
# =========================

def ban_user(user_id):
    """Manual ban."""
    with conn.cursor() as c:
        c.execute(
            "UPDATE users SET banned=TRUE WHERE user_id=%s",
            (user_id,)
        )
        conn.commit()


def unban_user(user_id):
    """Manual unban."""
    with conn.cursor() as c:
        c.execute(
            "UPDATE users SET banned=FALSE WHERE user_id=%s",
            (user_id,)
        )
        conn.commit()


def is_banned(user_id):
    """Check manual ban."""
    with conn.cursor() as c:
        c.execute(
            "SELECT banned FROM users WHERE user_id=%s",
            (user_id,)
        )
        r = c.fetchone()
        return r and r[0]


def get_banned_users():
    """Return list of manually banned users."""
    with conn.cursor() as c:
        c.execute("SELECT user_id FROM users WHERE banned=TRUE")
        return c.fetchall()


# =========================
# 👻 SHADOW BAN
# =========================

def shadow_toggle(user_id):
    """Toggle shadow ban."""
    with conn.cursor() as c:
        c.execute("""
            UPDATE users
            SET shadow_banned = NOT shadow_banned
            WHERE user_id=%s
        """, (user_id,))
        conn.commit()


def is_shadow(user_id):
    """Check if shadow banned."""
    with conn.cursor() as c:
        c.execute(
            "SELECT shadow_banned FROM users WHERE user_id=%s",
            (user_id,)
        )
        r = c.fetchone()
        return r and r[0]


# =========================
# ⏳ AUTO INACTIVITY SYSTEM
# =========================

def update_media_activity(user_id):
    """Update last media time and increment media count."""
    now = int(time.time())
    with conn.cursor() as c:
        c.execute("""
            UPDATE users
            SET last_media=%s,
                media_count = media_count + 1,
                auto_banned=FALSE
            WHERE user_id=%s
        """, (now, user_id))
        conn.commit()


def check_inactive_users():
    """Auto ban inactive users (1 minute logic)."""
    limit = int(time.time()) - 60
    with conn.cursor() as c:
        c.execute("""
            UPDATE users
            SET auto_banned=TRUE
            WHERE last_media < %s
              AND banned=FALSE
        """, (limit,))
        conn.commit()


def is_auto_banned(user_id):
    """Check auto ban."""
    with conn.cursor() as c:
        c.execute(
            "SELECT auto_banned FROM users WHERE user_id=%s",
            (user_id,)
        )
        r = c.fetchone()
        return r and r[0]


# =========================
# 🚪 JOIN CONTROL
# =========================

def is_join_open():
    """Check if joining allowed."""
    with conn.cursor() as c:
        c.execute("SELECT value FROM settings WHERE key='join_open'")
        r = c.fetchone()
        return r and r[0] == "true"


def set_join_status(status: bool):
    """Set join open/close."""
    with conn.cursor() as c:
        c.execute(
            "UPDATE settings SET value=%s WHERE key='join_open'",
            ("true" if status else "false",)
        )
        conn.commit()


# =========================
# 📊 STATS HELPERS
# =========================

def get_total_users():
    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM users")
        return c.fetchone()[0]


def get_manual_banned_count():
    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM users WHERE banned=TRUE")
        return c.fetchone()[0]


def get_auto_banned_count():
    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM users WHERE auto_banned=TRUE")
        return c.fetchone()[0]


def get_shadow_banned_count():
    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM users WHERE shadow_banned=TRUE")
        return c.fetchone()[0]


# =========================
# 🚫 BANNED WORD SYSTEM
# =========================
def contains_banned_word(text):
    words = get_banned_words()

    text_lower = text.lower()

    for word in words:
        if word in text_lower:
            return True

    return False


def add_banned_word(word):
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO banned_words (word) VALUES (%s) ON CONFLICT DO NOTHING",
            (word.lower(),)
        )
        conn.commit()


def remove_banned_word(word):
    with conn.cursor() as c:
        c.execute(
            "DELETE FROM banned_words WHERE word=%s",
            (word.lower(),)
        )
        conn.commit()


def get_banned_words():
    with conn.cursor() as c:
        c.execute("SELECT word FROM banned_words")
        return [r[0] for r in c.fetchall()]


# =========================
# 📩 MESSAGE TRACKING
# =========================

def save_message_map(bot_msg_id, original_user_id, receiver_id):
    """Store broadcasted message mapping."""
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO message_map
            (bot_message_id, original_user_id, receiver_id)
            VALUES (%s,%s,%s)
            ON CONFLICT DO NOTHING
        """, (bot_msg_id, original_user_id, receiver_id))
        conn.commit()


def get_original_user(bot_msg_id):
    with conn.cursor() as c:
        c.execute("""
            SELECT original_user_id FROM message_map
            WHERE bot_message_id=%s
        """, (bot_msg_id,))
        r = c.fetchone()
        return r[0] if r else None


def get_user_messages(user_id):
    """Get all broadcasted messages of a user."""
    with conn.cursor() as c:
        c.execute("""
            SELECT bot_message_id, receiver_id
            FROM message_map
            WHERE original_user_id=%s
        """, (user_id,))
        return c.fetchall()
def user_blocked_by_system(user_id):
    """
    Returns (blocked: bool, reason_message: str or None)
    Used before broadcasting.
    """

    if is_banned(user_id):
        return True, "🚫 You are banned."

    if is_auto_banned(user_id):
        return True, "⏳ You are temporarily auto-banned due to inactivity."

    return False, None

# =========================================================
# 👤 USER FLOW
# =========================================================

@bot.message_handler(commands=['start'])
def start(message):
    uid=message.chat.id
    if not user_exists(uid):
        if not is_join_open():
            bot.reply_to(message,"🚪 Joining is closed by admin.")
            return
        add_user(uid)
        waiting_username.add(uid)
        bot.reply_to(message,"👋 Welcome! Send your username.")
        return
        now = int(time.time())
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET last_media=%s WHERE user_id=%s",
                (now, user_id)
            )
            conn.commit()


    if not get_username(uid):
        waiting_username.add(uid)
        bot.reply_to(message,"✍ Send your username.")
        return

    bot.reply_to(message,"👋 Welcome back!")

@bot.message_handler(func=lambda m: m.chat.id in waiting_username,content_types=['text'])
def receive_username(message):
    uid=message.chat.id
    name=message.text.strip().lower()
    if len(name)<3:
        bot.reply_to(message,"❌ Username too short.")
        return
    if username_taken(name):
        bot.reply_to(message,"❌ Username already taken.")
        return
    set_username(uid,name)
    waiting_username.discard(uid)
    bot.reply_to(message,f"✅ Username set to @{name}")
@bot.message_handler(func=lambda m: m.chat.id in waiting_username, content_types=['text'])
def receive_username(message):
    user_id = message.chat.id
    username = message.text.strip().lower()

    # Username length check
    if len(username) < 3:
        bot.reply_to(message, "❌ Username too short (min 3 characters).")
        return

    # Username uniqueness check
    if username_taken(username):
        bot.reply_to(message, "❌ Username already taken. Try another.")
        return

    # Save username
    set_username(user_id, username)

    waiting_username.discard(user_id)

    bot.reply_to(message, f"✅ Username set to @{username}")


# =========================================================
# 📡 RELAY
# =========================================================
def broadcast_worker():
    while True:
        job = broadcast_queue.get()

        if job["type"] == "single":
            _process_single(job["message"])

        elif job["type"] == "album":
            _process_album(job["messages"])

        broadcast_queue.task_done()
def build_prefix(user_id):
    username = get_username(user_id)

    if username:
        return f" #{username}:\n"
    else:
        return "👤 Unknown:\n"
def _process_single(message):

    users = get_all_users()
    prefix = build_prefix(message.chat.id)

    for (uid,) in users:

        if uid == message.chat.id:
            continue

        try:

            # TEXT MESSAGE
            if message.content_type == "text":
                sent = bot.send_message(
                    uid,
                    prefix + message.text
                )

            # PHOTO
            elif message.content_type == "photo":
                caption = message.caption or ""
                sent = bot.send_photo(
                    uid,
                    message.photo[-1].file_id,
                    caption=prefix + caption
                )

            # VIDEO
            elif message.content_type == "video":
                caption = message.caption or ""
                sent = bot.send_video(
                    uid,
                    message.video.file_id,
                    caption=prefix + caption
                )

            else:
                continue

            # Save message mapping
            save_message_map(
                sent.message_id,
                message.chat.id,
                uid
            )

            time.sleep(0.04)

        except:
            pass
def _process_album(messages):

    users = get_all_users()
    prefix = build_prefix(messages[0].chat.id)

    media_batch = []

    for i, msg in enumerate(messages):

        caption = msg.caption or ""

        # Add prefix only to first media
        if i == 0:
            caption = prefix + caption

        if msg.content_type == "photo":
            media_batch.append(
                InputMediaPhoto(
                    msg.photo[-1].file_id,
                    caption=caption
                )
            )

        elif msg.content_type == "video":
            media_batch.append(
                InputMediaVideo(
                    msg.video.file_id,
                    caption=caption
                )
            )

    chunks = [media_batch[i:i+10] for i in range(0, len(media_batch), 10)]

    for (uid,) in users:

        if uid == messages[0].chat.id:
            continue

        for chunk in chunks:
            try:
                sent_msgs = bot.send_media_group(uid, chunk)

                for sm in sent_msgs:
                    save_message_map(
                        sm.message_id,
                        messages[0].chat.id,
                        uid
                    )

                time.sleep(0.04)

            except:
                pass

#@bot.message_handler(content_types=['text', 'photo', 'video'])
@bot.message_handler(
    func=lambda m: not m.text or not m.text.startswith('/'),
    content_types=['text','photo','video']
)
def relay(message):

    user_id = message.chat.id

    # 🚫 Manual ban
   # Manual ban always blocks
    if is_banned(user_id):
        bot.reply_to(message, "🚫 You are banned.")
        return
    
    # If auto-banned and user sends media → allow recovery
    if is_auto_banned(user_id):
    
        if message.content_type in ['photo', 'video']:
            update_media_activity(user_id)
            bot.reply_to(message, "🎉 You are unbanned. Stay active!")
        else:
            bot.reply_to(message, "⏳ You are inactive. Send media to reactivate.")
            return

    # 👻 Shadow behavior
    if is_shadow(user_id):
        bot.reply_to(message, "✅ Message sent.")
        return

    # 🚫 Word filter
    if message.content_type == "text":
        if contains_banned_word(message.text):
            bot.reply_to(message, " Message contains banned word.")
            return

    # ⏳ Inactivity check
    check_inactive_users()

    # Media tracking
    if message.content_type in ['photo', 'video']:
        update_media_activity(user_id)

    # =========================
    # 📦 Album Handling
    # =========================
    
    if message.media_group_id:
    
        group_id = message.media_group_id
    
        # Add message to group
        media_groups[group_id].append(message)
    
        # If this is not the first item, just return
        if len(media_groups[group_id]) > 1:
            return
    
        # First item waits for rest
        time.sleep(0.7)
    
        album = media_groups.pop(group_id, [])
    
        if album:
            broadcast_queue.put({
                "type": "album",
                "messages": album
            })
    
        return  # IMPORTANT: stop here for album messages

    else:
        broadcast_queue.put({
            "type": "single",
            "message": message
        })

# =========================================================
# 🛠 ADMIN COMMANDS
# =========================================================
def is_admin(user_id):
    return user_id == ADMIN_ID

@bot.message_handler(commands=['stats'])
def stats(message):
    if not is_admin(message.chat.id):
        return

    total = get_total_users()
    banned = get_manual_banned_count()
    auto = get_auto_banned_count()
    shadow = get_shadow_banned_count()

    bot.reply_to(
        message,
        f"📊✨ BOT STATISTICS ✨📊\n\n"
        f"👥 Total Users: {total}\n"
        f"🔨 Manual Banned: {banned}\n"
        f"⏳ Auto Banned: {auto}\n"
        f"👻 Shadow Banned: {shadow}"
    )

@bot.message_handler(commands=['info'])
def info(message):

    if not is_admin(message.chat.id):
        return

    uid = None

    # 🔁 Reply method
    if message.reply_to_message:
        uid = get_original_user(message.reply_to_message.message_id)

    # 🆔 ID method
    else:
        parts = message.text.split()
        if len(parts) > 1:
            try:
                uid = int(parts[1])
            except:
                pass

    if not uid:
        bot.reply_to(message, "❌ Use:\n/info USER_ID\nor reply to a user message.")
        return

    with conn.cursor() as c:
        c.execute("""
            SELECT user_id, username, banned,
                   auto_banned, shadow_banned, media_count
            FROM users WHERE user_id=%s
        """, (uid,))
        data = c.fetchone()

    if not data:
        bot.reply_to(message, "❌ User not found.")
        return

    bot.reply_to(
        message,
        f"👤✨ USER INFO ✨👤\n\n"
        f"🆔 ID: {data[0]}\n"
        f"🏷 Username: @{data[1]}\n"
        f"🔨 Banned: {data[2]}\n"
        f"⏳ Auto Banned: {data[3]}\n"
        f"👻 Shadow Banned: {data[4]}\n"
        f"📸 Media Sent: {data[5]}"
    )

@bot.message_handler(commands=['ban'])
def reply_ban(message):
    if not is_admin(message.chat.id):
        return

    if not message.reply_to_message:
        bot.reply_to(message, "Reply to a message to ban.")
        return

    uid = get_original_user(message.reply_to_message.message_id)

    if not uid:
        bot.reply_to(message, "Could not find user.")
        return

    ban_user(uid)
    bot.reply_to(message, f"🔨 User {uid} banned.")
@bot.message_handler(commands=['unban'])
def admin_unban(message):

    if not is_admin(message.chat.id):
        return

    uid = None

    # 🔁 If reply method
    if message.reply_to_message:
        uid = get_original_user(message.reply_to_message.message_id)

    # 🆔 If ID method
    else:
        parts = message.text.split()
        if len(parts) > 1:
            try:
                uid = int(parts[1])
            except:
                pass

    if not uid:
        bot.reply_to(message, "❌ Could not detect user.")
        return

    unban_user(uid)
    bot.reply_to(message, f"✅ User {uid} unbanned.")

@bot.message_handler(commands=['purge'])
def purge_user(message):
    if not is_admin(message.chat.id):
        return

    if not message.reply_to_message:
        bot.reply_to(message, "Reply to user message to purge.")
        return

    uid = get_original_user(message.reply_to_message.message_id)

    if not uid:
        bot.reply_to(message, "Cannot detect user.")
        return

    rows = get_user_messages(uid)

    deleted = 0

    for mid, receiver_id in rows:
        try:
            bot.delete_message(receiver_id, mid)
            deleted += 1
        except:
            pass

    bot.reply_to(message, f"🧹 Purged {deleted} messages.")
@bot.message_handler(commands=['del'])
def delete_everywhere(message):
    if not is_admin(message.chat.id):
        return

    if not message.reply_to_message:
        bot.reply_to(message, "Reply to a message to delete.")
        return

    original_uid = get_original_user(message.reply_to_message.message_id)

    if not original_uid:
        bot.reply_to(message, "Message not tracked.")
        return

    rows = get_user_messages(original_uid)

    deleted = 0

    for mid, receiver_id in rows:
        try:
            bot.delete_message(receiver_id, mid)
            deleted += 1
        except:
            pass

    bot.reply_to(message, f"🗑 Deleted from {deleted} chats.")
@bot.message_handler(commands=['addword'])
def add_word(message):
    if not is_admin(message.chat.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /addword word")
        return

    add_banned_word(parts[1])
    bot.reply_to(message, "🚫 Word added.")
@bot.message_handler(commands=['removeword'])
def remove_word(message):
    if not is_admin(message.chat.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /removeword word")
        return

    remove_banned_word(parts[1])
    bot.reply_to(message, "✅ Word removed.")
@bot.message_handler(commands=['words'])
def list_words(message):
    if not is_admin(message.chat.id):
        return

    words = get_banned_words()

    if not words:
        bot.reply_to(message, "🎉 No banned words set.")
        return

    text = "🚫 BANNED WORDS:\n\n"
    for w in words:
        text += f"• {w}\n"

    bot.reply_to(message, text)
@bot.message_handler(commands=['shadow'])
def shadow_command(message):
    if not is_admin(message.chat.id):
        return

    if not message.reply_to_message:
        bot.reply_to(message, "Reply to user message.")
        return

    uid = get_original_user(message.reply_to_message.message_id)

    if not uid:
        bot.reply_to(message, "Cannot detect user.")
        return

    shadow_toggle(uid)
    bot.reply_to(message, "👻 Shadow status toggled.")

@bot.message_handler(commands=['closejoin'])
def close_join(message):
    if is_admin(message.chat.id):
        set_join_status(False)
        bot.reply_to(message, "🔒 Joining closed.")

@bot.message_handler(commands=['openjoin'])
def open_join(message):
    if is_admin(message.chat.id):
        set_join_status(True)
        bot.reply_to(message, "🔓 Joining opened.")

@bot.message_handler(commands=['panel'])
def panel(message):
    if not is_admin(message.chat.id):
        return

    markup = InlineKeyboardMarkup()

    markup.add(
        InlineKeyboardButton("📊 Stats", callback_data="stats")
    )

    markup.add(
        InlineKeyboardButton("🔓 Open Join", callback_data="open"),
        InlineKeyboardButton("🔒 Close Join", callback_data="close")
    )

    bot.send_message(
        message.chat.id,
        "🎛 ADMIN PANEL",
        reply_markup=markup
    )
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if not is_admin(call.message.chat.id):
        return

    if call.data == "stats":
        stats(call.message)

    elif call.data == "open":
        set_join_status(True)
        bot.send_message(call.message.chat.id, "🔓 Joining opened.")

    elif call.data == "close":
        set_join_status(False)
        bot.send_message(call.message.chat.id, "🔒 Joining closed.")


threading.Thread(target=broadcast_worker, daemon=True).start()

# =========================================================
# ▶ START BOT
# =========================================================

print("Bot is starting...")
bot.infinity_polling(skip_pending=True)
