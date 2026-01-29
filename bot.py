# bot_new.py
# Full updated code with requested fixes:
# - two-message flow in PREDLOJKA (header message then post+links)
# - info button shows alert with details
# - DB writes fixed to persist necessary info
# Requires: aiogram, aiosqlite, aiohttp

import asyncio
import os
import time
import aiosqlite
from datetime import datetime
from aiohttp import web
from typing import Optional, List, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, MessageEntity, ContentType
from aiogram.filters import CommandStart

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

PREDLOJKA_ID = os.getenv("PREDLOJKA_ID")
CHANNEL_ID = os.getenv("CHANNEL_ID")

try:
    PREDLOJKA_ID = int(PREDLOJKA_ID) if PREDLOJKA_ID is not None else None
except Exception:
    PREDLOJKA_ID = None

try:
    CHANNEL_ID = int(CHANNEL_ID) if CHANNEL_ID is not None else None
except Exception:
    CHANNEL_ID = None

DB_PATH = os.getenv("DB_PATH", "data.db")
CHECK_UNBAN_SECONDS = 60

# ---------- TEXTS ----------
LANG_PROMPT_RU = "🗣️ Выберите язык"
LANG_PROMPT_UK = "🗣️ Виберіть мову"

WELCOME_RU = (
    "👋 Добро пожаловать в бота «Сущности Горишних Плавней»!\n"
    "Здесь Вы можете предложить пост или обратиться в поддержку канала.\n\n"
    "🆙 Ваша репутация: {rep}\n\n"
    "✅ Принятых постов: ~ {accepted}\n"
    "❌ Отклонённых постов: ~ {declined}\n\n"
    "Репутацию можно повысить предложив пост, который в следствии будет одобрен. Чем интереснее Ваш пост, тем больше репутации вы заработаете."
)

WELCOME_UK = (
    "👋 Ласкаво просимо до бота «Сущності Горішніх Плавнів»!\n"
    "Тут ви можете запропонувати пост або звернутися до підтримки каналу.\n\n"
    "🆙 Ваша репутація: {rep}\n\n"
    "✅ Прийнятих постів: ~ {accepted}\n"
    "❌ Відхилених постів: ~ {declined}\n\n"
    "Репутацію можна підвищити, запропонувавши пост, який в результаті буде схвалений. Чим цікавіший Ваш пост, тим більше репутації Ви заробите."
)

PROPOSE_PROMPT_RU = "🖼️ Пришлите свой пост. Это может быть видео, картинка или надпись. Помните: пост должен соответствовать нашей политике конфиденциальности."
PROPOSE_PROMPT_UK = "🖼️ Надішліть свій пост. Це може бути відео, зображення або напис. Пам'ятайте: пост повинен відповідати нашій політиці конфіденційності."

CONFIRM_SENT_RU = "✅ Ваш пост отправлен на рассмотрение. Дождитесь, пока его проверят."
CONFIRM_SENT_UK = "✅ Ваш пост відправлений на розгляд. Зачекайте, поки його перевірять."

CANCEL_TEXT_RU = "❌ Отменить"
CANCEL_TEXT_UK = "❌ Скасувати"

ACCEPT_NOTICE_RU = "🆙 Ваш пост был принят! Вы заработали +{n} репутации."
ACCEPT_NOTICE_UK = "🆙 Ваш пост був прийнятий! Ви заробили +{n} репутації."

DECLINE_NOTICE_RU = "❌ Ваш пост был отклонён."
DECLINE_NOTICE_UK = "❌ Ваш пост був відхилений."

DECLINE_PENALTY_NOTICE_RU = "❌ Ваш пост был отклонён. Вы потеряли -{n} репутации."
DECLINE_PENALTY_NOTICE_UK = "❌ Ваш пост був відхилений. Ви загубили -{n} репутації."

BANNED_NOTICE_RU = "🚫 Вы были забанены в опции предложения постов на {period}."
BANNED_NOTICE_UK = "🚫 Ви були забанені у опції пропозиції постів на {period}."

UNBANNED_NOTICE_RU = "🔓 Срок Вашего бана в опции предложения постов был окончен! Вы снова можете предлагать свои посты."
UNBANNED_NOTICE_UK = "🔓 Термін Вашого бану в опції пропозиції постів закінчився! Ви знову можете пропонувати свої пости."

APPENDED_LINKS_HTML = (
    '<a href="https://t.me/predlojka_gp_bot">Предложить пост</a>  •  '
    '<a href="https://t.me/comments_gp_plavni">Чат</a>  •  '
    '<a href="https://t.me/boost/channel_gp_plavni">Буст</a>'
)

# ---------- DB ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # users
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                lang TEXT DEFAULT 'ru',
                lang_selected INTEGER DEFAULT 0,
                reputation INTEGER DEFAULT 0,
                banned_until INTEGER DEFAULT 0,
                in_propose INTEGER DEFAULT 0,
                accepted_count INTEGER DEFAULT 0,
                declined_count INTEGER DEFAULT 0
            )
            """
        )
        # proposals
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_chat_id INTEGER NOT NULL,
                user_msg_id INTEGER NOT NULL,
                group_header_msg_id INTEGER,
                group_post_msg_id INTEGER,
                group_mod_msg_id INTEGER,
                created_at INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                mod_id INTEGER,
                mod_action TEXT,
                mod_action_param TEXT
            )
            """
        )
        await db.commit()

async def ensure_user_row(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def set_user_lang(user_id: int, lang: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.execute("UPDATE users SET lang = ?, lang_selected = 1 WHERE user_id = ?", (lang, user_id))
        await db.commit()

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, lang, lang_selected, reputation, banned_until, in_propose, accepted_count, declined_count FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "user_id": row[0],
            "lang": row[1],
            "lang_selected": bool(row[2]),
            "reputation": row[3],
            "banned_until": row[4],
            "in_propose": bool(row[5]),
            "accepted_count": row[6],
            "declined_count": row[7],
        }

async def set_in_propose(user_id: int, value: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.execute("UPDATE users SET in_propose = ? WHERE user_id = ?", (1 if value else 0, user_id))
        await db.commit()

async def set_banned_until(user_id: int, until_ts: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.execute("UPDATE users SET banned_until = ? WHERE user_id = ?", (until_ts, user_id))
        await db.commit()

async def add_reputation(user_id: int, delta: int):
    await ensure_user_row(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET reputation = reputation + ? WHERE user_id = ?", (delta, user_id))
        await db.commit()

async def increment_accepted(user_id: int, delta: int = 1):
    await ensure_user_row(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET accepted_count = accepted_count + ? WHERE user_id = ?", (delta, user_id))
        await db.commit()

async def increment_declined(user_id: int, delta: int = 1):
    await ensure_user_row(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET declined_count = declined_count + ? WHERE user_id = ?", (delta, user_id))
        await db.commit()

async def create_proposal_entry(user_id: int, user_chat_id: int, user_msg_id: int) -> int:
    ts = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO proposals (user_id, user_chat_id, user_msg_id, created_at) VALUES (?, ?, ?, ?)", (user_id, user_chat_id, user_msg_id, ts))
        await db.commit()
        return cur.lastrowid

async def update_proposal_ids(proposal_id: int, header_msg_id: int = None, post_msg_id: int = None, mod_msg_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        parts = []
        args = []
        if header_msg_id is not None:
            parts.append("group_header_msg_id = ?"); args.append(header_msg_id)
        if post_msg_id is not None:
            parts.append("group_post_msg_id = ?"); args.append(post_msg_id)
        if mod_msg_id is not None:
            parts.append("group_mod_msg_id = ?"); args.append(mod_msg_id)
        if not parts:
            return
        q = "UPDATE proposals SET " + ", ".join(parts) + " WHERE id = ?"
        args.append(proposal_id)
        await db.execute(q, tuple(args))
        await db.commit()

async def set_proposal_status_and_mod(proposal_id: int, status: str, mod_id: Optional[int] = None, action: Optional[str] = None, param: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE proposals SET status = ?, mod_id = ?, mod_action = ?, mod_action_param = ? WHERE id = ?", (status, mod_id, action, param, proposal_id))
        await db.commit()

async def get_proposal(proposal_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, user_id, user_chat_id, user_msg_id, group_header_msg_id, group_post_msg_id, group_mod_msg_id, created_at, status, mod_id, mod_action, mod_action_param FROM proposals WHERE id = ?", (proposal_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "user_id": row[1],
            "user_chat_id": row[2],
            "user_msg_id": row[3],
            "group_header_msg_id": row[4],
            "group_post_msg_id": row[5],
            "group_mod_msg_id": row[6],
            "created_at": row[7],
            "status": row[8],
            "mod_id": row[9],
            "mod_action": row[10],
            "mod_action_param": row[11],
        }

# ---------- UTIL ----------
def make_lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 RU", callback_data="set_lang:ru"),
         InlineKeyboardButton(text="🇺🇦 UK", callback_data="set_lang:uk")]
    ])

def main_menu_kb(lang: str):
    if lang == "uk":
        text_propose = "🖼️ Запропонувати пост"
        text_support = "📩 Підтримка"
        text_lang = "🗣️ Змінити мову"
        text_privacy = "📋 Політика конфіденційності"
    else:
        text_propose = "🖼️ Предложить пост"
        text_support = "📩 Поддержка"
        text_lang = "🗣️ Сменить язык"
        text_privacy = "📋 Политика конфиденциальности"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text_propose, callback_data="main:propose")],
        [InlineKeyboardButton(text=text_support, callback_data="main:support")],
        [InlineKeyboardButton(text=text_lang, callback_data="main:lang")],
        [InlineKeyboardButton(text=text_privacy, callback_data="main:privacy")],
    ])

def cancel_kb(lang: str):
    txt = CANCEL_TEXT_UK if lang == "uk" else CANCEL_TEXT_RU
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=txt, callback_data="propose:cancel")]])

def mod_buttons_vertical(proposal_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"mod:accept:{proposal_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:decline:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 Бан пользователя", callback_data=f"mod:ban:{proposal_id}")],
    ])

def ban_duration_kb(proposal_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 12 часов", callback_data=f"ban:12h:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 24 часов", callback_data=f"ban:24h:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 3 дня", callback_data=f"ban:3d:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 1 неделя", callback_data=f"ban:7d:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 Навсегда", callback_data=f"ban:forever:{proposal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"ban:back:{proposal_id}")],
    ])

def rep_buttons_vertical(proposal_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆙 +3 репутации", callback_data=f"rep:3:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 +2 репутации", callback_data=f"rep:2:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 +1 репутация", callback_data=f"rep:1:{proposal_id}")],
    ])

def decline_penalty_kb(proposal_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆙 -0 репутации", callback_data=f"declpen:0:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 -1 репутация", callback_data=f"declpen:1:{proposal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"declpen:back:{proposal_id}")],
    ])

def final_choice_kb(action_label: str, proposal_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"ℹ️ Выбрано: {action_label}", callback_data=f"info:{proposal_id}")]
    ])

def format_remaining(ts_end: int) -> str:
    if ts_end <= 0:
        return "0д, 0ч, 0м"
    rem = ts_end - int(time.time())
    if rem <= 0:
        return "0д, 0ч, 0м"
    days = rem // 86400; hours = (rem % 86400) // 3600; minutes = (rem % 3600) // 60
    return f"{days}д, {hours}ч, {minutes}м"

def human_date(ts: int) -> str:
    dt = datetime.fromtimestamp(ts)
    return f"{dt.day} {dt.strftime('%B')}"

def user_mention_html_from_user(user: types.User) -> str:
    if user.username:
        return f"@{user.username}"
    else:
        full = user.full_name or str(user.id)
        return f'<a href="tg://user?id={user.id}">{full}</a>'

# ---------- entity -> HTML converter ----------
def escape_html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def entities_to_html(text: str, entities: Optional[List[MessageEntity]]) -> str:
    if not entities:
        return escape_html(text)
    ents = sorted(entities, key=lambda e: e.offset)
    parts = []
    last = 0
    for e in ents:
        start = e.offset; end = e.offset + e.length
        if start > last:
            parts.append(escape_html(text[last:start]))
        segment = text[start:end]; seg_escaped = escape_html(segment)
        t = e.type
        if t == "bold":
            parts.append(f"<b>{seg_escaped}</b>")
        elif t == "italic":
            parts.append(f"<i>{seg_escaped}</i>")
        elif t == "underline":
            parts.append(f"<u>{seg_escaped}</u>")
        elif t == "strikethrough":
            parts.append(f"<s>{seg_escaped}</s>")
        elif t == "code":
            parts.append(f"<code>{seg_escaped}</code>")
        elif t == "pre":
            parts.append(f"<pre>{seg_escaped}</pre>")
        elif t == "text_link":
            url = getattr(e, "url", "")
            parts.append(f'<a href="{escape_html(url)}">{seg_escaped}</a>')
        elif t == "text_mention":
            user = getattr(e, "user", None)
            if user:
                parts.append(f'<a href="tg://user?id={user.id}">{seg_escaped}</a>')
            else:
                parts.append(seg_escaped)
        else:
            parts.append(seg_escaped)
        last = end
    if last < len(text):
        parts.append(escape_html(text[last:]))
    return "".join(parts)

# ---------- BOT SETUP ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- HANDLERS ----------
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    await ensure_user_row(user.id)
    row = await get_user(user.id)
    if row and row["lang_selected"]:
        lang = row["lang"] or "ru"
        rep = row["reputation"]; accepted = row["accepted_count"]; declined = row["declined_count"]
        text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
        await message.answer(text, reply_markup=main_menu_kb(lang))
        return
    # else show lang selection
    prompt = LANG_PROMPT_UK if (row and row.get("lang") == "uk") else LANG_PROMPT_RU
    await message.answer(prompt, reply_markup=make_lang_kb())

@dp.callback_query(F.data and F.data.startswith("set_lang:"))
async def cb_set_lang(call: types.CallbackQuery):
    await call.answer()
    lang = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    await ensure_user_row(user_id)
    await set_user_lang(user_id, lang)
    # remove selection message
    try:
        await call.message.delete()
    except Exception:
        pass
    row = await get_user(user_id)
    rep = row["reputation"] if row else 0
    accepted = row["accepted_count"] if row else 0
    declined = row["declined_count"] if row else 0
    text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
    await call.message.answer(text, reply_markup=main_menu_kb(lang))

@dp.callback_query(F.data == "main:lang")
async def cb_main_change_lang(call: types.CallbackQuery):
    await call.answer()
    row = await get_user(call.from_user.id)
    lang = row["lang"] if (row and row.get("lang")) else "ru"
    prompt = LANG_PROMPT_UK if lang == "uk" else LANG_PROMPT_RU
    await call.message.answer(prompt, reply_markup=make_lang_kb())

@dp.callback_query(F.data == "main:propose")
async def cb_main_propose(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    await ensure_user_row(user_id)
    row = await get_user(user_id)
    lang = row["lang"] if row else "ru"
    banned_until = row["banned_until"] if row else 0
    now = int(time.time())
    if banned_until and banned_until > now:
        rem = format_remaining(banned_until)
        await call.message.answer(BANNED_NOTICE_UK.format(period=rem) if lang == "uk" else BANNED_NOTICE_RU.format(period=rem))
        return
    await set_in_propose(user_id, True)
    prompt = PROPOSE_PROMPT_UK if lang == "uk" else PROPOSE_PROMPT_RU
    await call.message.answer(prompt, reply_markup=cancel_kb(lang))

@dp.callback_query(F.data == "propose:cancel")
async def cb_propose_cancel(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    await set_in_propose(user_id, False)
    row = await get_user(user_id)
    lang = row["lang"] if row else "ru"; rep = row["reputation"] if row else 0
    accepted = row["accepted_count"] if row else 0; declined = row["declined_count"] if row else 0
    try:
        await call.message.delete()
    except Exception:
        pass
    text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
    await call.message.answer(text, reply_markup=main_menu_kb(lang))

# handle any message while in propose mode
@dp.message()
async def handle_any_message(message: types.Message):
    user = message.from_user
    uid = user.id
    await ensure_user_row(uid)
    row = await get_user(uid)
    in_propose = row["in_propose"] if row else False
    if not in_propose:
        return
    banned_until = row["banned_until"] if row else 0
    now = int(time.time())
    lang = row["lang"] if row else "ru"
    if banned_until and banned_until > now:
        await message.reply(BANNED_NOTICE_UK.format(period=format_remaining(banned_until)) if lang == "uk" else BANNED_NOTICE_RU.format(period=format_remaining(banned_until)))
        await set_in_propose(uid, False)
        return

    # create proposal DB entry
    proposal_id = await create_proposal_entry(uid, message.chat.id, message.message_id)

    # header text message (first message)
    post_ts = int(time.time())
    hhmm = datetime.fromtimestamp(post_ts).strftime("%H:%M")
    human = human_date(post_ts)
    mention_text = user_mention_html_from_user(user)
    header_text = f"От {mention_text} • {hhmm} • {human}"

    if PREDLOJKA_ID is None:
        await message.reply("PREDLOJKA_ID not configured in environment. Обратитесь к администратору.")
        await set_in_propose(uid, False)
        return

    try:
        # 1) send header message
        header_msg = await bot.send_message(PREDLOJKA_ID, header_text, parse_mode="HTML")
        header_msg_id = header_msg.message_id

        # 2) copy the user's original message into group (preserves formatting/media)
        copied = await bot.copy_message(chat_id=PREDLOJKA_ID, from_chat_id=message.chat.id, message_id=message.message_id)
        post_msg_id = copied.message_id if copied else None

        # 3) edit the copied message to append the APPENDED_LINKS_HTML on a new line
        # For text messages: edit_message_text; for media with caption: edit_message_caption (try both)
        # We need to preserve original formatting, so we convert original entities to HTML and concatenate.
        if message.content_type == ContentType.TEXT:
            orig_text = message.text or ""
            orig_entities = message.entities or []
            orig_html = entities_to_html(orig_text, orig_entities)
            combined_html = f"{orig_html}\n\n{APPENDED_LINKS_HTML}"
            try:
                await bot.edit_message_text(chat_id=PREDLOJKA_ID, message_id=post_msg_id, text=combined_html, parse_mode="HTML")
            except Exception:
                # fallback: replace whole message with combined text
                try:
                    await bot.send_message(PREDLOJKA_ID, combined_html, parse_mode="HTML")
                    # if fallback used, we can optionally delete the copied message
                    # attempt to delete original copied
                    try:
                        await bot.delete_message(PREDLOJKA_ID, post_msg_id)
                    except Exception:
                        pass
                    # update post_msg_id to newly sent
                    # (we won't have its id, but it's fine)
                except Exception:
                    pass
        else:
            # media or other: existing caption or text
            base = getattr(message, "caption", None) or getattr(message, "text", None) or ""
            caption_entities = getattr(message, "caption_entities", None) or getattr(message, "entities", None) or []
            base_html = entities_to_html(base, caption_entities) if base else ""
            if base_html:
                combined_html = f"{base_html}\n\n{APPENDED_LINKS_HTML}"
            else:
                combined_html = f"{APPENDED_LINKS_HTML}"
            # try to edit caption
            try:
                await bot.edit_message_caption(chat_id=PREDLOJKA_ID, message_id=post_msg_id, caption=combined_html, parse_mode="HTML")
            except Exception:
                try:
                    await bot.edit_message_text(chat_id=PREDLOJKA_ID, message_id=post_msg_id, text=combined_html, parse_mode="HTML")
                except Exception:
                    # fallback: send a new message containing links and delete nothing
                    try:
                        await bot.send_message(PREDLOJKA_ID, APPENDED_LINKS_HTML, parse_mode="HTML")
                    except Exception:
                        pass

        # 4) send moderation message (links + mod buttons) below the content message
        mod_msg = await bot.send_message(PREDLOJKA_ID, APPENDED_LINKS_HTML, parse_mode="HTML", reply_markup=mod_buttons_vertical(proposal_id))
        mod_msg_id = mod_msg.message_id

        # update DB record with message ids
        await update_proposal_ids(proposal_id, header_msg_id=header_msg_id, post_msg_id=post_msg_id, mod_msg_id=mod_msg_id)

    except Exception:
        await message.reply("Ошибка при отправке в предложку. Попробуйте позже.")
        await set_in_propose(uid, False)
        return

    # notify user
    await message.reply(CONFIRM_SENT_UK if lang == "uk" else CONFIRM_SENT_RU)
    await set_in_propose(uid, False)

    # return main menu after a second
    await asyncio.sleep(1)
    row2 = await get_user(uid)
    rep2 = row2["reputation"] if row2 else 0
    accepted2 = row2["accepted_count"] if row2 else 0
    declined2 = row2["declined_count"] if row2 else 0
    welcome = WELCOME_UK.format(rep=rep2, accepted=accepted2, declined=declined2) if lang == "uk" else WELCOME_RU.format(rep=rep2, accepted=accepted2, declined=declined2)
    try:
        await bot.send_message(uid, welcome, reply_markup=main_menu_kb(lang))
    except Exception:
        pass

# ---------- Moderation callbacks ----------
@dp.callback_query(F.data and F.data.startswith("mod:"))
async def cb_mod_actions(call: types.CallbackQuery):
    await call.answer()
    parts = call.data.split(":")
    action = parts[1]
    proposal_id = int(parts[2]) if len(parts) > 2 else None
    if not proposal_id:
        await call.message.answer("Неверные данные.")
        return

    prop = await get_proposal(proposal_id)
    if not prop:
        await call.message.answer("Заявка не найдена.")
        return

    if action == "accept":
        # copy final post to channel if configured
        if CHANNEL_ID and prop.get("group_post_msg_id"):
            try:
                await bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=PREDLOJKA_ID, message_id=prop["group_post_msg_id"])
            except Exception:
                pass
        await set_proposal_status_and_mod(proposal_id, "accepted", None, "accept", None)
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=rep_buttons_vertical(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

    if action == "decline":
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=decline_penalty_kb(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

    if action == "ban":
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=ban_duration_kb(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

@dp.callback_query(F.data and F.data.startswith("declpen:"))
async def cb_decline_penalty(call: types.CallbackQuery):
    await call.answer()
    parts = call.data.split(":", 2)
    arg = parts[1]
    proposal_id = int(parts[2]) if len(parts) > 2 else None
    if proposal_id is None:
        return
    if arg == "back":
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=mod_buttons_vertical(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return
    try:
        penalty = int(arg)
    except Exception:
        return

    prop = await get_proposal(proposal_id)
    if not prop:
        return
    user_id = prop["user_id"]; user_chat_id = prop["user_chat_id"]; user_msg_id = prop["user_msg_id"]
    mod_id = call.from_user.id

    if penalty == 0:
        await set_proposal_status_and_mod(proposal_id, "declined", mod_id, "decline", "0")
        await increment_declined(user_id, 1)
        # notify author (no rep change)
        urow = await get_user(user_id)
        lang = urow["lang"] if urow else "ru"
        text = DECLINE_NOTICE_UK if lang == "uk" else DECLINE_NOTICE_RU
        try:
            await bot.send_message(user_chat_id, text, reply_to_message_id=user_msg_id)
        except Exception:
            try:
                await bot.send_message(user_chat_id, text)
            except Exception:
                pass
        final_label = "❌ Отклонить"
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=final_choice_kb(final_label, proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return
    elif penalty == 1:
        await add_reputation(user_id, -1)
        await set_proposal_status_and_mod(proposal_id, "declined", mod_id, "decline", "-1")
        await increment_declined(user_id, 1)
        urow = await get_user(user_id)
        lang = urow["lang"] if urow else "ru"
        text = DECLINE_PENALTY_NOTICE_UK.format(n=1) if lang == "uk" else DECLINE_PENALTY_NOTICE_RU.format(n=1)
        try:
            await bot.send_message(user_chat_id, text, reply_to_message_id=user_msg_id)
        except Exception:
            try:
                await bot.send_message(user_chat_id, text)
            except Exception:
                pass
        final_label = "❌ Отклонить"
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=final_choice_kb(final_label, proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

@dp.callback_query(F.data and F.data.startswith("ban:"))
async def cb_ban_duration(call: types.CallbackQuery):
    await call.answer()
    parts = call.data.split(":", 2)
    dur = parts[1]
    proposal_id = int(parts[2]) if len(parts) > 2 else None
    if proposal_id is None:
        return
    if dur == "back":
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=mod_buttons_vertical(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return
    prop = await get_proposal(proposal_id)
    if not prop:
        return
    user_id = prop["user_id"]; user_chat_id = prop["user_chat_id"]; user_msg_id = prop["user_msg_id"]
    now = int(time.time())
    if dur == "12h": until = now + 12*3600; timestr = "12 часов"
    elif dur == "24h": until = now + 24*3600; timestr = "24 часов"
    elif dur == "3d": until = now + 3*24*3600; timestr = "3 дня"
    elif dur == "7d": until = now + 7*24*3600; timestr = "1 неделя"
    elif dur == "forever": until = 2**31-1; timestr = "навсегда"
    else: return

    await set_banned_until(user_id, until)
    await set_proposal_status_and_mod(proposal_id, "banned", call.from_user.id, "ban", timestr)
    urow = await get_user(user_id); lang = urow["lang"] if urow else "ru"
    period = format_remaining(until)
    text = BANNED_NOTICE_UK.format(period=period) if lang == "uk" else BANNED_NOTICE_RU.format(period=period)
    try:
        await bot.send_message(user_chat_id, text, reply_to_message_id=user_msg_id)
    except Exception:
        try:
            await bot.send_message(user_chat_id, text)
        except Exception:
            pass
    final_label = "🚫 Бан"
    try:
        await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=final_choice_kb(final_label, proposal_id), parse_mode="HTML")
    except Exception:
        pass

@dp.callback_query(F.data and F.data.startswith("rep:"))
async def cb_rep_buttons(call: types.CallbackQuery):
    await call.answer()
    parts = call.data.split(":")
    if len(parts) < 3: return
    rep_amount = int(parts[1]); proposal_id = int(parts[2])
    prop = await get_proposal(proposal_id)
    if not prop:
        await call.message.answer("Заявка не найдена.")
        return
    user_id = prop["user_id"]; user_chat_id = prop["user_chat_id"]; user_msg_id = prop["user_msg_id"]
    await add_reputation(user_id, rep_amount)
    await increment_accepted(user_id, 1)
    await set_proposal_status_and_mod(proposal_id, "published", call.from_user.id, "accept", str(rep_amount))
    urow = await get_user(user_id); lang = urow["lang"] if urow else "ru"
    text = (ACCEPT_NOTICE_UK if lang == "uk" else ACCEPT_NOTICE_RU).format(n=rep_amount)
    try:
        await bot.send_message(user_chat_id, text, reply_to_message_id=user_msg_id)
    except Exception:
        try:
            await bot.send_message(user_chat_id, text)
        except Exception:
            pass
    final_label = f"✅ Принять +{rep_amount}"
    try:
        await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=final_choice_kb(final_label, proposal_id), parse_mode="HTML")
    except Exception:
        pass

@dp.callback_query(F.data and F.data.startswith("info:"))
async def cb_info(call: types.CallbackQuery):
    # show alert with info
    parts = call.data.split(":")
    proposal_id = int(parts[1]) if len(parts) > 1 else None
    if proposal_id is None:
        await call.answer("Неверные данные", show_alert=True)
        return
    prop = await get_proposal(proposal_id)
    if not prop:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    proposer_id = prop["user_id"]
    mod_id = prop["mod_id"]
    # get proposer and moderator info if possible
    try:
        proposer = await bot.get_chat(proposer_id)
    except Exception:
        proposer = None
    if mod_id:
        try:
            moderator = await bot.get_chat(mod_id)
        except Exception:
            moderator = None
    else:
        moderator = None

    def name_and_username(chat_obj: Optional[types.User]):
        if not chat_obj:
            return ("нет юзернейма", "нет юзернейма")
        nick = chat_obj.full_name or str(chat_obj.id)
        uname = f"@{chat_obj.username}" if getattr(chat_obj, "username", None) else "нет юзернейма"
        return (nick, uname)

    p_nick, p_uname = name_and_username(proposer)
    if moderator:
        m_nick, m_uname = name_and_username(moderator)
        m_id_display = str(mod_id)
    else:
        m_nick, m_uname = ("нет юзернейма", "нет юзернейма")
        m_id_display = "—"

    action = prop["mod_action"] or "—"
    param = prop["mod_action_param"] or "—"

    info_text = (
        f"📩 Предложил: {p_nick} • {p_uname} • {proposer_id}\n"
        f"😎 Обработал: {m_nick} • {m_uname} • {m_id_display}\n"
        f"❓ Выбранное действие: {action} • {param}"
    )
    await call.answer(info_text, show_alert=True)

# ---------- Background unban watcher ----------
async def unban_watcher():
    while True:
        try:
            now = int(time.time())
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id FROM users WHERE banned_until > 0 AND banned_until <= ?", (now,))
                rows = await cur.fetchall()
                for r in rows:
                    uid = r[0]
                    await db.execute("UPDATE users SET banned_until = 0 WHERE user_id = ?", (uid,))
                    await db.commit()
                    try:
                        cur2 = await db.execute("SELECT lang FROM users WHERE user_id = ?", (uid,))
                        r2 = await cur2.fetchone()
                        lang = r2[0] if r2 else "ru"
                    except Exception:
                        lang = "ru"
                    text = UNBANNED_NOTICE_UK if lang == "uk" else UNBANNED_NOTICE_RU
                    try:
                        await bot.send_message(uid, text)
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(CHECK_UNBAN_SECONDS)

# ---------- Health server ----------
async def start_health_server():
    port = int(os.environ.get("PORT", "8000"))
    async def health(request):
        return web.Response(text="OK")
    app = web.Application()
    app.add_routes([web.get('/', health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"[health] Listening on 0.0.0.0:{port}")

# ---------- START ----------
async def main():
    await init_db()
    try:
        await start_health_server()
    except Exception as e:
        print(f"[health] failed: {e}")
    asyncio.create_task(unban_watcher())
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
