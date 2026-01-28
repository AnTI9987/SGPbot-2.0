# bot_new.py
# Based on your uploaded bot.py and requirements. 0 1

import asyncio
import os
import time
import aiosqlite
from datetime import datetime, timezone, timedelta
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    ContentType,
)
from aiogram.filters import CommandStart, Command

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

# IDs (set these as environment variables)
PREDLOJKA_ID = os.getenv("PREDLOJKA_ID")  # group id for proposals (as integer string)
CHANNEL_ID = os.getenv("CHANNEL_ID")      # channel id for accepted posts (as integer string)

# Convert to ints when available, keep None otherwise
try:
    PREDLOJKA_ID = int(PREDLOJKA_ID) if PREDLOJKA_ID is not None else None
except Exception:
    PREDLOJKA_ID = None

try:
    CHANNEL_ID = int(CHANNEL_ID) if CHANNEL_ID is not None else None
except Exception:
    CHANNEL_ID = None

DB_PATH = os.getenv("DB_PATH", "data.db")
CHECK_UNBAN_SECONDS = 60  # background check interval

# ---------- TEXTS ----------
LANG_PROMPT_RU = "🗣️ Выберите язык"
LANG_PROMPT_UK = "🗣️ Виберіть мову"

WELCOME_RU = (
    "👋 Добро пожаловать в бота «Сущности Горишних Плавней»!\n"
    "Здесь Вы можете предложить пост или обратиться в поддержку канала.\n\n"
    "🆙 Ваша репутация\n"
    "{rep}\n\n"
    "Репутацию можно повысить предложив пост, который в следствии будет одобрен. Чем интереснее Ваш пост, тем больше репутации вы заработаете."
)

WELCOME_UK = (
    "👋 Ласкаво просимо до бота «Сущності Горішніх Плавнів»!\n"
    "Тут ви можете запропонувати пост або звернутися до підтримки каналу.\n\n"
    "🆙 Ваша репутація\n"
    "{rep}\n\n"
    "Репутацію можна підвищити, запропонувавши пост, який в результаті буде схвалений. Чим цікавіший Ваш пост, тим більше репутації Ви заробите."
)

PROPOSE_PROMPT_RU = (
    "🖼️ Пришлите свой пост. Это может быть видео, картинка или надпись. Помните: пост должен соответствовать нашей политике конфиденциальности."
)
PROPOSE_PROMPT_UK = (
    "🖼️ Надішліть свій пост. Це може бути відео, зображення або напис. Пам'ятайте: пост повинен відповідати нашій політиці конфіденційності."
)

CONFIRM_SENT_RU = "✅ Ваш пост отправлен на рассмотрение. Дождитесь, пока его проверят."
CONFIRM_SENT_UK = "✅ Ваш пост відправлений на розгляд. Зачекайте, поки його перевірять."

CANCEL_TEXT_RU = "❌ Отменить"
CANCEL_TEXT_UK = "❌ Скасувати"

ACCEPT_NOTICE_RU = "🆙 Ваш пост был принят! Вы заработали +{n} репутации."
ACCEPT_NOTICE_UK = "🆙 Ваш пост був прийнятий! Ви заробили +{n} репутації."

DECLINE_NOTICE_RU = "🙁 Ваш пост был отклонён."
DECLINE_NOTICE_UK = "🙁 Ваш пост був відхилений."

BANNED_NOTICE_RU = "🚫 Вы были забанены в опции предложения постов на {period}."
BANNED_NOTICE_UK = "🚫 Ви були забанені у опції пропозиції постів на {period}."

UNBANNED_NOTICE_RU = "🔓 Срок Вашего бана в опции предложения постов был окончен! Вы снова можете предлагать свои посты."
UNBANNED_NOTICE_UK = "🔓 Термін Вашого бану в опції пропозиції постів закінчився! Ви знову можете пропонувати свої пости."

# ---------- HELPERS (DB) ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                lang TEXT DEFAULT 'ru',
                reputation INTEGER DEFAULT 0,
                banned_until INTEGER DEFAULT 0,
                in_propose INTEGER DEFAULT 0
            )
            """
        )
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
                status TEXT DEFAULT 'pending'
            )
            """
        )
        await db.commit()


async def set_user_lang(user_id: int, lang: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, lang) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang",
            (user_id, lang),
        )
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, lang, reputation, banned_until, in_propose FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row


async def ensure_user_row(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()


async def set_in_propose(user_id: int, value: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET in_propose = ? WHERE user_id = ?", (1 if value else 0, user_id))
        await db.commit()


async def set_banned_until(user_id: int, until_ts: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET banned_until = ? WHERE user_id = ?", (until_ts, user_id))
        await db.commit()


async def add_reputation(user_id: int, delta: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET reputation = reputation + ? WHERE user_id = ?", (delta, user_id))
        await db.commit()


async def get_reputation(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT reputation FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0


async def create_proposal_entry(user_id: int, user_chat_id: int, user_msg_id: int) -> int:
    ts = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO proposals (user_id, user_chat_id, user_msg_id, created_at) VALUES (?, ?, ?, ?)",
            (user_id, user_chat_id, user_msg_id, ts),
        )
        await db.commit()
        return cur.lastrowid


async def update_proposal_ids(proposal_id: int, header_msg_id: int = None, post_msg_id: int = None, mod_msg_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        q = "UPDATE proposals SET "
        parts = []
        args = []
        if header_msg_id is not None:
            parts.append("group_header_msg_id = ?")
            args.append(header_msg_id)
        if post_msg_id is not None:
            parts.append("group_post_msg_id = ?")
            args.append(post_msg_id)
        if mod_msg_id is not None:
            parts.append("group_mod_msg_id = ?")
            args.append(mod_msg_id)
        if not parts:
            return
        q += ", ".join(parts) + " WHERE id = ?"
        args.append(proposal_id)
        await db.execute(q, tuple(args))
        await db.commit()


async def get_proposal(proposal_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, user_id, user_chat_id, user_msg_id, group_mod_msg_id, status FROM proposals WHERE id = ?", (proposal_id,))
        return await cur.fetchone()


async def set_proposal_status(proposal_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))
        await db.commit()


# ---------- UTIL ----------

def make_lang_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 RU", callback_data="set_lang:ru"),
         InlineKeyboardButton(text="🇺🇦 UK", callback_data="set_lang:uk")]
    ])
    return kb


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

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text_propose, callback_data="main:propose")],
        [InlineKeyboardButton(text=text_support, callback_data="main:support")],
        [InlineKeyboardButton(text=text_lang, callback_data="main:lang")],
        [InlineKeyboardButton(text=text_privacy, callback_data="main:privacy")],
    ])
    return kb


def cancel_kb(lang: str):
    txt = CANCEL_TEXT_UK if lang == "uk" else CANCEL_TEXT_RU
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=txt, callback_data="propose:cancel")]
    ])
    return kb


def mod_buttons(proposal_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"mod:accept:{proposal_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:decline:{proposal_id}"),
            InlineKeyboardButton(text="🚫 Бан пользователя", callback_data=f"mod:ban:{proposal_id}"),
        ]
    ])
    return kb


def ban_duration_kb(proposal_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 12 часов", callback_data=f"ban:12h:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 24 часов", callback_data=f"ban:24h:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 3 дня", callback_data=f"ban:3d:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 1 неделя", callback_data=f"ban:7d:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 Навсегда", callback_data=f"ban:forever:{proposal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"ban:back:{proposal_id}")],
    ])
    return kb


def rep_buttons(proposal_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🆙 +3 репутации", callback_data=f"rep:3:{proposal_id}"),
            InlineKeyboardButton(text="🆙 +2 репутации", callback_data=f"rep:2:{proposal_id}"),
            InlineKeyboardButton(text="🆙 +1 репутация", callback_data=f"rep:1:{proposal_id}"),
        ]
    ])
    return kb


def format_remaining(ts_end: int) -> str:
    if ts_end <= 0:
        return "0д, 0ч, 0м"
    rem = ts_end - int(time.time())
    if rem <= 0:
        return "0д, 0ч, 0м"
    days = rem // 86400
    hours = (rem % 86400) // 3600
    minutes = (rem % 3600) // 60
    return f"{days}д, {hours}ч, {minutes}м"


def human_date(ts: int):
    dt = datetime.fromtimestamp(ts)
    day = dt.day
    month_name = dt.strftime("%B")  # will be English by default; user didn't require localization
    return f"{day} {month_name}"


def user_mention_html(user: types.User):
    if user.username:
        return f"@{user.username}"
    else:
        # mention by link
        full_name = (user.full_name or str(user.id))
        return f'<a href="tg://user?id={user.id}">{full_name}</a>'


# ---------- BOT SETUP ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---------- HANDLERS ----------

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    await ensure_user_row(user.id)
    # Default prompt is Russian unless user's lang is uk
    row = await get_user(user.id)
    lang = "uk" if (row and row[1] == "uk") else "ru"
    prompt = LANG_PROMPT_UK if lang == "uk" else LANG_PROMPT_RU
    await message.answer(prompt, reply_markup=make_lang_kb())


@dp.callback_query(F.data and F.data.startswith("set_lang:"))
async def cb_set_lang(call: types.CallbackQuery):
    await call.answer()
    lang = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    await ensure_user_row(user_id)
    await set_user_lang(user_id, lang)
    # delete the language selection message
    try:
        await call.message.delete()
    except Exception:
        pass

    # send welcome message in chosen language
    rep = await get_reputation(user_id)
    if lang == "uk":
        text = WELCOME_UK.format(rep=rep)
    else:
        text = WELCOME_RU.format(rep=rep)

    await call.message.answer(text, reply_markup=main_menu_kb(lang))


@dp.callback_query(F.data == "main:lang")
async def cb_main_change_lang(call: types.CallbackQuery):
    await call.answer()
    # show language selector; but if user selected Ukrainian earlier, show Ukrainian prompt text
    row = await get_user(call.from_user.id)
    lang = "uk" if (row and row[1] == "uk") else "ru"
    prompt = LANG_PROMPT_UK if lang == "uk" else LANG_PROMPT_RU
    await call.message.answer(prompt, reply_markup=make_lang_kb())


@dp.callback_query(F.data == "main:propose")
async def cb_main_propose(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    await ensure_user_row(user_id)
    row = await get_user(user_id)
    lang = "uk" if (row and row[1] == "uk") else "ru"
    banned_until = row[3] if row else 0
    now = int(time.time())
    if banned_until and banned_until > now:
        rem = format_remaining(banned_until)
        text = BANNED_NOTICE_UK.format(period=rem) if lang == "uk" else BANNED_NOTICE_RU.format(period=rem)
        await call.message.answer(text)
        return
    # set user into propose mode and prompt
    await set_in_propose(user_id, True)
    prompt = PROPOSE_PROMPT_UK if lang == "uk" else PROPOSE_PROMPT_RU
    await call.message.answer(prompt, reply_markup=cancel_kb(lang))


@dp.callback_query(F.data == "propose:cancel")
async def cb_propose_cancel(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    await set_in_propose(user_id, False)
    row = await get_user(user_id)
    lang = "uk" if (row and row[1] == "uk") else "ru"
    rep = await get_reputation(user_id)
    text = WELCOME_UK.format(rep=rep) if lang == "uk" else WELCOME_RU.format(rep=rep)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(text, reply_markup=main_menu_kb(lang))


# while in propose mode: treat any incoming content as a post
@dp.message()
async def handle_any_message(message: types.Message):
    user = message.from_user
    uid = user.id
    # ensure user exists
    await ensure_user_row(uid)
    row = await get_user(uid)
    in_propose = bool(row[4]) if row else False
    if not in_propose:
        # ignore other messages (for simplicity) or you can respond with main menu
        return

    # user is in propose mode: check ban again
    banned_until = row[3] if row else 0
    now = int(time.time())
    lang = "uk" if (row and row[1] == "uk") else "ru"
    if banned_until and banned_until > now:
        rem = format_remaining(banned_until)
        text = BANNED_NOTICE_UK.format(period=rem) if lang == "uk" else BANNED_NOTICE_RU.format(period=rem)
        await message.reply(text)
        await set_in_propose(uid, False)
        return

    # create proposal record
    proposal_id = await create_proposal_entry(uid, message.chat.id, message.message_id)

    # header: "От <username> • 00:00 • 1 апреля"
    try:
        post_ts = int(time.time())
        hhmm = datetime.fromtimestamp(post_ts).strftime("%H:%M")
        human = human_date(post_ts)
        mention = await bot.get_chat(message.from_user.id)
        mention_text = user_mention_html(mention) if mention else f"{message.from_user.id}"
        header_text = f"От {mention_text} • {hhmm} • {human}"
    except Exception:
        header_text = f"От {message.from_user.id} • {datetime.now().strftime('%H:%M')} • {human_date(int(time.time()))}"

    # send header to group (if configured)
    header_msg_id = None
    post_copy_msg_id = None
    mod_msg_id = None

    if PREDLOJKA_ID is None:
        # If group not set, just notify user and return
        await message.reply("PREDLOJKA_ID not configured in environment. Обратитесь к администратору.")
        await set_in_propose(uid, False)
        return

    try:
        header = await bot.send_message(PREDLOJKA_ID, header_text, parse_mode="HTML")
        header_msg_id = header.message_id
    except Exception:
        header_msg_id = None

    # copy the user's message to group (this preserves media)
    try:
        copied = await bot.copy_message(chat_id=PREDLOJKA_ID, from_chat_id=message.chat.id, message_id=message.message_id)
        post_copy_msg_id = copied.message_id
    except Exception:
        # fallback: try to forward
        try:
            fwd = await bot.forward_message(chat_id=PREDLOJKA_ID, from_chat_id=message.chat.id, message_id=message.message_id)
            post_copy_msg_id = fwd.message_id
        except Exception:
            post_copy_msg_id = None

    # send appended links + moderation buttons (in the same group)
    # The appended text must contain the 3 links per spec
    appended_text = (
        '<a href="https://t.me/predlojka_gp_bot">Предложить пост</a>  •  '
        '<a href="https://t.me/comments_gp_plavni">Чат</a>  •  '
        '<a href="https://t.me/boost/channel_gp_plavni">Буст</a>'
    )
    try:
        mod_msg = await bot.send_message(PREDLOJKA_ID, appended_text, parse_mode="HTML", reply_markup=mod_buttons(proposal_id))
        mod_msg_id = mod_msg.message_id
    except Exception:
        mod_msg_id = None

    # update proposal record with group message ids
    await update_proposal_ids(proposal_id, header_msg_id=header_msg_id, post_msg_id=post_copy_msg_id, mod_msg_id=mod_msg_id)

    # reply to user that their post is submitted
    confirm_text = CONFIRM_SENT_UK if lang == "uk" else CONFIRM_SENT_RU
    try:
        await message.reply(confirm_text)
    except Exception:
        try:
            await bot.send_message(uid, confirm_text)
        except Exception:
            pass

    # exit propose mode for user
    await set_in_propose(uid, False)

    # after 1 second, send the main menu to the user again
    await asyncio.sleep(1)
    rep = await get_reputation(uid)
    welcome = WELCOME_UK.format(rep=rep) if lang == "uk" else WELCOME_RU.format(rep=rep)
    try:
        await bot.send_message(uid, welcome, reply_markup=main_menu_kb(lang))
    except Exception:
        pass


# ---------- Moderation callbacks in PREDLOJKA group ----------

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

    # prop fields: id, user_id, user_chat_id, user_msg_id, group_mod_msg_id, status
    user_id = prop[1]
    user_chat_id = prop[2]
    user_msg_id = prop[3]
    mod_msg_id = prop[4]

    # Accept
    if action == "accept":
        # copy the original post to the CHANNEL_ID if configured
        if CHANNEL_ID:
            try:
                # copy the user's original message to the channel as final post
                await bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=user_chat_id, message_id=user_msg_id)
            except Exception:
                pass
        # set proposal status
        await set_proposal_status(proposal_id, "accepted")
        # edit moderation message to reputation buttons
        try:
            await bot.edit_message_text(call.message.text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=rep_buttons(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

    # Decline
    if action == "decline":
        await set_proposal_status(proposal_id, "declined")
        # notify author in reply to their message in bot chat
        # choose language of user
        urow = await get_user(user_id)
        lang = "uk" if (urow and urow[1] == "uk") else "ru"
        text = DECLINE_NOTICE_UK if lang == "uk" else DECLINE_NOTICE_RU
        try:
            await bot.send_message(user_chat_id, text, reply_to_message_id=user_msg_id)
        except Exception:
            try:
                await bot.send_message(user_chat_id, text)
            except Exception:
                pass
        # remove moderation buttons / mark status
        try:
            await bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass
        return

    # Ban: show durations keyboard
    if action == "ban":
        try:
            await bot.edit_message_text(call.message.text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=ban_duration_kb(proposal_id), parse_mode="HTML")
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
    prop = await get_proposal(proposal_id)
    if not prop:
        return
    user_id = prop[1]
    user_chat_id = prop[2]
    user_msg_id = prop[3]

    if dur == "back":
        # revert to mod buttons
        try:
            await bot.edit_message_text(call.message.text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=mod_buttons(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

    now = int(time.time())
    if dur == "12h":
        until = now + 12 * 3600
    elif dur == "24h":
        until = now + 24 * 3600
    elif dur == "3d":
        until = now + 3 * 24 * 3600
    elif dur == "7d":
        until = now + 7 * 24 * 3600
    elif dur == "forever":
        until = 2 ** 31 - 1  # far future
    else:
        return

    # apply ban in DB
    await set_banned_until(user_id, until)
    await set_proposal_status(proposal_id, "banned")

    # notify the user about ban (reply to their message in private chat if possible)
    urow = await get_user(user_id)
    lang = "uk" if (urow and urow[1] == "uk") else "ru"
    period = format_remaining(until)
    text = BANNED_NOTICE_UK.format(period=period) if lang == "uk" else BANNED_NOTICE_RU.format(period=period)
    try:
        await bot.send_message(user_chat_id, text, reply_to_message_id=user_msg_id)
    except Exception:
        try:
            await bot.send_message(user_chat_id, text)
        except Exception:
            pass

    # edit moderation message to reflect ban applied and clear buttons
    try:
        await bot.edit_message_text(f"Пользователь забанен на {period}", chat_id=call.message.chat.id, message_id=call.message.message_id)
    except Exception:
        pass


@dp.callback_query(F.data and F.data.startswith("rep:"))
async def cb_rep_buttons(call: types.CallbackQuery):
    await call.answer()
    parts = call.data.split(":")
    if len(parts) < 3:
        return
    rep_amount = int(parts[1])
    proposal_id = int(parts[2])

    prop = await get_proposal(proposal_id)
    if not prop:
        await call.message.answer("Заявка не найдена.")
        return
    user_id = prop[1]
    user_chat_id = prop[2]
    user_msg_id = prop[3]

    # add reputation
    await add_reputation(user_id, rep_amount)
    await set_proposal_status(proposal_id, "published")

    # notify author with reply to their message in private chat
    urow = await get_user(user_id)
    lang = "uk" if (urow and urow[1] == "uk") else "ru"
    text = (ACCEPT_NOTICE_UK if lang == "uk" else ACCEPT_NOTICE_RU).format(n=rep_amount)
    try:
        await bot.send_message(user_chat_id, text, reply_to_message_id=user_msg_id)
    except Exception:
        try:
            await bot.send_message(user_chat_id, text)
        except Exception:
            pass

    # remove buttons in group (or change to none)
    try:
        await bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
    except Exception:
        pass


# ---------- Background unban notifier ----------
async def unban_watcher():
    while True:
        try:
            now = int(time.time())
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id, banned_until, lang FROM users WHERE banned_until > 0 AND banned_until <= ?", (now,))
                rows = await cur.fetchall()
                if rows:
                    for r in rows:
                        user_id = r[0]
                        lang = r[2] or "ru"
                        # reset ban
                        await db.execute("UPDATE users SET banned_until = 0 WHERE user_id = ?", (user_id,))
                        await db.commit()
                        # notify user
                        text = UNBANNED_NOTICE_UK if lang == "uk" else UNBANNED_NOTICE_RU
                        try:
                            await bot.send_message(user_id, text)
                        except Exception:
                            pass
        except Exception:
            # swallow exceptions to keep loop running
            pass
        await asyncio.sleep(CHECK_UNBAN_SECONDS)


# ---------- Health server (PATCH) ----------
async def start_health_server():
    """
    Start a minimal aiohttp server that listens on $PORT (or 8000).
    Render expects a web process to bind to $PORT when running a Web Service.
    """
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
    # start health server so Render (Web Service) sees an open port
    # awaiting ensures server is started before polling begins
    try:
        await start_health_server()
    except Exception as e:
        print(f"[health] failed to start health server: {e}")
    # start background unban watcher
    asyncio.create_task(unban_watcher())
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
