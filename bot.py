# bot.py
# Full bot with propose system + /info + rep title toggles + fixes
# Delegates /info and "разбан" handling to command.py dynamically.

import asyncio
import os
import time
import asyncpg
from datetime import datetime
from aiohttp import web
from typing import Optional, Dict, Any, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    MessageEntity,
    ContentType,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    ChatMemberAdministrator
)
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramBadRequest

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

# Required group where proposals are posted
PREDLOJKA_ID = os.getenv("PREDLOJKA_ID")
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHAT_ID = os.getenv("CHAT_ID")  # chat where admin title is set for rep display

try:
    PREDLOJKA_ID = int(PREDLOJKA_ID) if PREDLOJKA_ID is not None else None
except Exception:
    PREDLOJKA_ID = None

try:
    CHANNEL_ID = int(CHANNEL_ID) if CHANNEL_ID is not None else None
except Exception:
    CHANNEL_ID = None

try:
    CHAT_ID = int(CHAT_ID) if CHAT_ID is not None else None
except Exception:
    CHAT_ID = None

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required (Neon/Postgres)")

DB_POOL: Optional[asyncpg.pool.Pool] = None

CHECK_UNBAN_SECONDS = 60  # background check interval

# ---------- TEXTS ----------
LANG_PROMPT_RU = "🗣️ Выберите язык"
LANG_PROMPT_UK = "🗣️ Виберіть мову"

WELCOME_RU = (
    "<b>👋 Добро пожаловать в бота «СГП»!</b>\n"
    "Здесь Вы можете предложить пост или обратиться в поддержку канала.\n\n"
    "🆙 Ваша репутация: {rep}\n"
    "✅ Принятых постов: {accepted}\n"
    "❌ Отклонённых постов: {declined}\n\n"
    "Репутацию можно повысить предложив пост, который в следствии будет одобрен."
)

WELCOME_UK = (
    "<b>👋 Ласкаво просимо до бота «СГП»!</b>\n"
    "Тут ви можете запропонувати пост або звернутися до підтримки каналу.\n\n"
    "🆙 Ваша репутація: {rep}\n"
    "✅ Прийнятих постів: {accepted}\n"
    "❌ Відхилених постів: {declined}\n\n"
    "Репутацію можна підвищити, запропонувавши пост, який в результаті буде схвалено."
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

DECLINE_NOTICE_RU = "❌ Ваш пост был отклонён."
DECLINE_NOTICE_UK = "❌ Ваш пост був відхилений."

DECLINE_PENALTY_NOTICE_RU = "❌ Ваш пост был отклонён. Вы потеряли -{n} репутации."
DECLINE_PENALTY_NOTICE_UK = "❌ Ваш пост був відхилений. Ви загубили -{n} репутації."

BANNED_NOTICE_RU = "🚫 Вы были забанены в опции предложения постов на {period}."
BANNED_NOTICE_UK = "🚫 Ви були забанені у опції пропозиції постів на {period}."

UNBANNED_NOTICE_RU = "🔓 Срок Вашего бана в опции предложения постов был окончен! Вы снова можете предлагать свои посты."
UNBANNED_NOTICE_UK = "🔓 Термін Вашого бану в опції пропозиції постів закінчився! Ви знову можете пропонувати свої пости."

APPENDED_LINKS_HTML = (
    '<a href="https://t.me/predlojka_gp_bot">Предложить пост</a> • '
    '<a href="https://t.me/comments_gp_plavni">Чат</a> • '
    '<a href="https://t.me/boost/channel_gp_plavni">Буст</a>'
)

PRIVACY_RU_URL = "https://telegra.ph/Politika-konfidencialnosti-01-29-96"
PRIVACY_UK_URL = "https://telegra.ph/Pol%D1%96tika-konf%D1%96denc%D1%96jnost%D1%96-01-29"

# russian month names in genitive (for "От ... • HH:MM • D month")
MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]

# ---------- DATABASE HELPERS (asyncpg) ----------
async def init_db():
    global DB_POOL
    if DB_POOL is None:
        DB_POOL = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    create_users = """
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        lang TEXT DEFAULT 'ru',
        lang_selected BOOLEAN DEFAULT FALSE,
        reputation INTEGER DEFAULT 0,
        banned_until BIGINT DEFAULT 0,
        in_propose BOOLEAN DEFAULT FALSE,
        accepted_count INTEGER DEFAULT 0,
        declined_count INTEGER DEFAULT 0
    );
    """

    create_proposals = """
    CREATE TABLE IF NOT EXISTS proposals (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        user_chat_id BIGINT NOT NULL,
        user_msg_id BIGINT NOT NULL,
        group_header_msg_id BIGINT,
        group_post_msg_id BIGINT,
        group_mod_msg_id BIGINT,
        created_at BIGINT NOT NULL,
        status TEXT DEFAULT 'pending',
        mod_id BIGINT,
        mod_action TEXT,
        mod_action_param TEXT
    );
    """

    async with DB_POOL.acquire() as conn:
        await conn.execute(create_users)
        await conn.execute(create_proposals)

async def ensure_user_row(user_id: int):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )

async def set_user_lang(user_id: int, lang: str):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, lang, lang_selected) VALUES ($1, $2, TRUE) "
            "ON CONFLICT (user_id) DO UPDATE SET lang = EXCLUDED.lang, lang_selected = TRUE",
            user_id,
            lang,
        )

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, lang, lang_selected, reputation, banned_until, in_propose, accepted_count, declined_count "
            "FROM users WHERE user_id = $1",
            user_id,
        )
        if not row:
            return None
        return {
            "user_id": row["user_id"],
            "lang": row["lang"],
            "lang_selected": bool(row["lang_selected"]),
            "reputation": row["reputation"],
            "banned_until": row["banned_until"],
            "in_propose": bool(row["in_propose"]),
            "accepted_count": row["accepted_count"],
            "declined_count": row["declined_count"],
        }

async def set_in_propose(user_id: int, value: bool):
    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE users SET in_propose = $1 WHERE user_id = $2", value, user_id)

async def set_banned_until(user_id: int, until_ts: int):
    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE users SET banned_until = $1 WHERE user_id = $2", until_ts, user_id)

async def add_reputation(user_id: int, delta: int):
    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE users SET reputation = reputation + $1 WHERE user_id = $2", delta, user_id)

async def get_user_reputation_db(user_id: int) -> int:
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT reputation FROM users WHERE user_id = $1", user_id)
        return row["reputation"] if row else 0

async def increment_accepted(user_id: int, delta: int = 1):
    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE users SET accepted_count = accepted_count + $1 WHERE user_id = $2", delta, user_id)

async def increment_declined(user_id: int, delta: int = 1):
    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE users SET declined_count = declined_count + $1 WHERE user_id = $2", delta, user_id)

async def create_proposal_entry(user_id: int, user_chat_id: int, user_msg_id: int) -> int:
    ts = int(time.time())
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO proposals (user_id, user_chat_id, user_msg_id, created_at) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            user_id,
            user_chat_id,
            user_msg_id,
            ts,
        )
        return int(row["id"])

async def update_proposal_ids(proposal_id: int, header_msg_id: int = None, post_msg_id: int = None, mod_msg_id: int = None):
    parts = []
    args = []
    if header_msg_id is not None:
        parts.append("group_header_msg_id = $" + str(len(args) + 1))
        args.append(header_msg_id)
    if post_msg_id is not None:
        parts.append("group_post_msg_id = $" + str(len(args) + 1))
        args.append(post_msg_id)
    if mod_msg_id is not None:
        parts.append("group_mod_msg_id = $" + str(len(args) + 1))
        args.append(mod_msg_id)
    if not parts:
        return
    set_clause = ", ".join(parts)
    params = args + [proposal_id]
    placeholders = len(params)
    async with DB_POOL.acquire() as conn:
        await conn.execute(f"UPDATE proposals SET {set_clause} WHERE id = ${placeholders}", *params)

async def set_proposal_status_and_mod(proposal_id: int, status: str, mod_id: Optional[int] = None, action: Optional[str] = None, param: Optional[str] = None):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "UPDATE proposals SET status = $1, mod_id = $2, mod_action = $3, mod_action_param = $4 WHERE id = $5",
            status,
            mod_id,
            action,
            param,
            proposal_id,
        )

async def get_proposal(proposal_id: int) -> Optional[Dict[str, Any]]:
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, user_chat_id, user_msg_id, group_header_msg_id, group_post_msg_id, group_mod_msg_id, created_at, status, mod_id, mod_action, mod_action_param "
            "FROM proposals WHERE id = $1",
            proposal_id,
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "user_chat_id": row["user_chat_id"],
            "user_msg_id": row["user_msg_id"],
            "group_header_msg_id": row["group_header_msg_id"],
            "group_post_msg_id": row["group_post_msg_id"],
            "group_mod_msg_id": row["group_mod_msg_id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "mod_id": row["mod_id"],
            "mod_action": row["mod_action"],
            "mod_action_param": row["mod_action_param"],
        }

# ---------- UTIL (keyboards & helpers) ----------
def make_lang_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 RU", callback_data="set_lang:ru"),
         InlineKeyboardButton(text="🇺🇦 UK", callback_data="set_lang:uk")]
    ])
    return kb

def persistent_reply_kb(lang: str):
    if lang == "uk":
        b_menu = KeyboardButton("📋 Меню")
        b_propose = KeyboardButton("🖼️ Запропонувати пост")
        b_support = KeyboardButton("📩 Підтримка")
        b_lang = KeyboardButton("🗣️ Змінити мову")
    else:
        b_menu = KeyboardButton("📋 Меню")
        b_propose = KeyboardButton("🖼️ Предложить пост")
        b_support = KeyboardButton("📩 Поддержка")
        b_lang = KeyboardButton("🗣️ Сменить язык")
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[b_menu, b_propose], [b_support, b_lang]])
    return kb

def main_menu_kb(lang: str):
    privacy_url = PRIVACY_UK_URL if lang == "uk" else PRIVACY_RU_URL
    if lang == "uk":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖼️ Запропонувати пост", callback_data="main:propose")],
            [InlineKeyboardButton(text="📩 Підтримка", callback_data="main:support")],
            [InlineKeyboardButton(text="🗣️ Змінити мову", callback_data="main:lang")],
            [InlineKeyboardButton(text="📋 Політика конфіденційності", url=privacy_url)],
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖼️ Предложить пост", callback_data="main:propose")],
            [InlineKeyboardButton(text="📩 Поддержка", callback_data="main:support")],
            [InlineKeyboardButton(text="🗣️ Сменить язык", callback_data="main:lang")],
            [InlineKeyboardButton(text="📋 Политика конфиденциальности", url=privacy_url)],
        ])
    return kb

def cancel_kb(lang: str):
    txt = CANCEL_TEXT_UK if lang == "uk" else CANCEL_TEXT_RU
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=txt, callback_data="propose:cancel")]
    ])
    return kb

def mod_buttons_vertical(proposal_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"mod:accept:{proposal_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:decline:{proposal_id}")],
        [InlineKeyboardButton(text="🚫 Бан пользователя", callback_data=f"mod:ban:{proposal_id}")],
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

def rep_buttons_vertical(proposal_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆙 +3 репутации", callback_data=f"rep:3:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 +2 репутации", callback_data=f"rep:2:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 +1 репутация", callback_data=f"rep:1:{proposal_id}")],
    ])
    return kb

def decline_penalty_kb(proposal_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆙 -0 репутации", callback_data=f"declpen:0:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 -1 репутация", callback_data=f"declpen:1:{proposal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"declpen:back:{proposal_id}")],
    ])
    return kb

def final_choice_kb(action_label: str, proposal_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"ℹ️ Выбрано: {action_label}", callback_data=f"info:{proposal_id}")]
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

def human_date(ts: int) -> str:
    dt = datetime.fromtimestamp(ts)
    day = dt.day
    month_name = MONTHS_RU[dt.month - 1]
    return f"{day} {month_name}"

def escape_html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def user_mention_html_from_user(user: types.User) -> str:
    full_name = (user.full_name or str(user.id))
    return f'<a href="tg://openmessage?user_id={user.id}">{escape_html(full_name)}</a>'

# helper to safely edit a (possibly media) message to swap/mod buttons and text/caption
async def safe_edit_message_replace(bot: Bot, chat_id: int, message_id: int, new_text: Optional[str], reply_markup: Optional[InlineKeyboardMarkup]):
    try:
        if new_text is None:
            new_text = ""
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=new_text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except Exception:
        pass
    try:
        if new_text is None:
            new_text = ""
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=new_text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except Exception:
        pass
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except Exception:
        pass
    return False

# ---------- BOT SETUP ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- HELPERS specific to rep-title behavior ----------

async def has_rep_title(bot_inst: Bot, user_id: int) -> bool:
    if CHAT_ID is None:
        return False
    try:
        member = await bot_inst.get_chat_member(CHAT_ID, user_id)
        if isinstance(member, ChatMemberAdministrator):
            ct = getattr(member, "custom_title", None)
            if ct and str(ct).startswith("Репутация:"):
                return True
        return False
    except Exception:
        return False

async def ensure_update_custom_title(bot_inst: Bot, user_id: int):
    if CHAT_ID is None:
        return
    try:
        member = await bot_inst.get_chat_member(CHAT_ID, user_id)
    except Exception:
        return
    if not isinstance(member, ChatMemberAdministrator):
        return
    ct = getattr(member, "custom_title", None)
    if not ct:
        return
    if not ct.startswith("Репутация:"):
        return
    rep = await get_user_reputation_db(user_id)
    try:
        await bot_inst.set_chat_administrator_custom_title(chat_id=CHAT_ID, user_id=user_id, custom_title=f"Репутация: {rep}")
    except Exception:
        pass

async def grant_rep_title_bot_admin(bot_inst: Bot, user_id: int, rep: int):
    if CHAT_ID is None:
        return False
    try:
        await bot_inst.promote_chat_member(
            chat_id=CHAT_ID,
            user_id=user_id,
            can_manage_chat=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        await bot_inst.set_chat_administrator_custom_title(chat_id=CHAT_ID, user_id=user_id, custom_title=f"Репутация: {rep}")
        return True
    except Exception:
        return False

async def remove_rep_title_and_demote(bot_inst: Bot, user_id: int):
    if CHAT_ID is None:
        return False
    try:
        try:
            await bot_inst.set_chat_administrator_custom_title(chat_id=CHAT_ID, user_id=user_id, custom_title="")
        except Exception:
            await bot_inst.promote_chat_member(
                chat_id=CHAT_ID,
                user_id=user_id,
                can_manage_chat=False,
                can_post_messages=False,
                can_edit_messages=False,
                can_delete_messages=False,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
            )
        return True
    except Exception:
        return False

# ---------- HANDLERS ----------

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # only allow /start in private chat
    chat_type = getattr(message.chat, "type", None)
    if chat_type != "private":
        try:
            await message.reply("Команда /start доступна только в личных сообщениях с ботом.")
        except Exception:
            pass
        return

    user = message.from_user
    await ensure_user_row(user.id)
    row = await get_user(user.id)
    if row and row["lang_selected"]:
        lang = row["lang"] or "ru"
        rep = row["reputation"]
        accepted = row["accepted_count"]
        declined = row["declined_count"]
        text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
        await message.answer(text, reply_markup=main_menu_kb(lang), parse_mode="HTML")
        prompt = "Выберите действие:" if lang != "uk" else "Оберіть дію:"
        await message.answer(prompt, reply_markup=persistent_reply_kb(lang))
        return

    prompt = LANG_PROMPT_UK if (row and row.get("lang") == "uk") else LANG_PROMPT_RU
    await message.answer(prompt, reply_markup=make_lang_kb())

@dp.callback_query(F.data and F.data.startswith("set_lang:"))
async def cb_set_lang(call: types.CallbackQuery):
    await call.answer()
    lang = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    await ensure_user_row(user_id)
    await set_user_lang(user_id, lang)
    try:
        await call.message.delete()
    except Exception:
        pass

    row = await get_user(user_id)
    rep = row["reputation"] if row else 0
    accepted = row["accepted_count"] if row else 0
    declined = row["declined_count"] if row else 0
    text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
    await call.message.answer(text, reply_markup=main_menu_kb(lang), parse_mode="HTML")
    prompt = "Выберите действие:" if lang != "uk" else "Оберіть дію:"
    try:
        await call.message.answer(prompt, reply_markup=persistent_reply_kb(lang))
    except Exception:
        pass

@dp.callback_query(F.data == "main:lang")
async def cb_main_change_lang(call: types.CallbackQuery):
    await call.answer()
    try:
        await call.message.answer(" ", reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass
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
        text = BANNED_NOTICE_UK.format(period=rem) if lang == "uk" else BANNED_NOTICE_RU.format(period=rem)
        await call.message.answer(text)
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
    lang = row["lang"] if row else "ru"
    rep = row["reputation"] if row else 0
    accepted = row["accepted_count"] if row else 0
    declined = row["declined_count"] if row else 0
    try:
        await call.message.delete()
    except Exception:
        pass
    text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
    await call.message.answer(text, reply_markup=main_menu_kb(lang), parse_mode="HTML")
    prompt = "Выберите действие:" if lang != "uk" else "Оберіть дію:"
    try:
        await call.message.answer(prompt, reply_markup=persistent_reply_kb(lang))
    except Exception:
        pass

@dp.callback_query(F.data == "main:support")
async def cb_main_support(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    row = await get_user(user_id)
    lang = row["lang"] if row else "ru"
    try:
        import bot2
        await bot2.send_support(bot, user_id, lang)
    except Exception:
        text = "Поддержка" if lang != "uk" else "Підтримка"
        try:
            await bot.send_message(user_id, text)
        except Exception:
            pass

# while in propose mode: treat any incoming content as a post
@dp.message()
async def handle_any_message(message: types.Message):
    # First: handle reply-keyboard shortcuts and language/menu commands
    user = message.from_user
    uid = user.id
    if message.text:
        txt = message.text.strip()
        # Menu
        if txt in ("📋 Меню", "📋 Меню"):
            row = await get_user(uid)
            lang = row["lang"] if row else "ru"
            rep = row["reputation"] if row else 0
            accepted = row["accepted_count"] if row else 0
            declined = row["declined_count"] if row else 0
            text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
            await message.answer(text, reply_markup=main_menu_kb(lang), parse_mode="HTML")
            return

        # Start propose via keyboard
        if txt in ("🖼️ Предложить пост", "🖼️ Запропонувати пост"):
            await ensure_user_row(uid)
            row = await get_user(uid)
            lang = row["lang"] if row else "ru"
            await set_in_propose(uid, True)
            prompt = PROPOSE_PROMPT_UK if lang == "uk" else PROPOSE_PROMPT_RU
            await message.answer(prompt, reply_markup=cancel_kb(lang))
            return

        # Support
        if txt in ("📩 Поддержка", "📩 Підтримка"):
            try:
                import bot2
                await bot2.send_support(bot, uid, (await get_user(uid))["lang"])
            except Exception:
                lang = (await get_user(uid))["lang"] if await get_user(uid) else "ru"
                text = "Поддержка" if lang != "uk" else "Підтримка"
                await message.answer(text)
            return

        # Change language
        if txt in ("🗣️ Сменить язык", "🗣️ Змінити мову"):
            await message.answer(LANG_PROMPT_RU, reply_markup=make_lang_kb())
            try:
                await message.answer(" ", reply_markup=ReplyKeyboardRemove())
            except Exception:
                pass
            return

    # Delegation: commands implemented in command.py
    # Check for /info or text variants -> delegate to command.handle_info
    if message.text:
        low = message.text.strip().lower()
        if low in ("/info", "инфо", "інфо", "информация", "інформація"):
            try:
                import command
                await command.handle_info(message)
            except Exception:
                # fallback minimal info card
                await ensure_user_row(uid)
                row = await get_user(uid)
                lang = row["lang"] if row else "ru"
                rep = row["reputation"] if row else 0
                accepted = row["accepted_count"] if row else 0
                has_title_now = await has_rep_title(bot, uid)
                text = (WELCOME_UK if lang == "uk" else WELCOME_RU).format(rep=rep, accepted=accepted, declined=(row["declined_count"] if row else 0))
                try:
                    await message.answer(text)
                except Exception:
                    pass
            return

        # razban delegation: only when message starts with razban keywords
        tstr = message.text.strip()
        if tstr.startswith("разбан ") or tstr.startswith("/разбан ") or tstr.startswith("razban ") or tstr.startswith("/razban "):
            try:
                import command
                await command.handle_razban(message)
            except Exception:
                # fallback: old behaviour (minimal)
                if message.chat is None or PREDLOJKA_ID is None:
                    return
                if message.chat.id != PREDLOJKA_ID:
                    try:
                        await message.reply("Команда разбан доступна только в группе предложки.")
                    except Exception:
                        pass
                    return
                parts = tstr.split(None, 1)
                if len(parts) < 2:
                    try:
                        await message.reply("Укажите пользователя по @юзернейму или ID. Пример: разбан 123456789")
                    except Exception:
                        pass
                    return
                target = parts[1].strip()
                target_id = None
                if target.startswith("@"):
                    try:
                        chat = await bot.get_chat(target)
                        target_id = chat.id
                    except Exception:
                        target_id = None
                else:
                    try:
                        target_id = int(target)
                    except Exception:
                        try:
                            chat = await bot.get_chat("@" + target)
                            target_id = chat.id
                        except Exception:
                            target_id = None
                if target_id is None:
                    try:
                        await message.reply("Не удалось определить пользователя. Укажите корректный @юзернейм или числовой ID.")
                    except Exception:
                        pass
                    return
                try:
                    await set_banned_until(target_id, 0)
                except Exception:
                    try:
                        await message.reply("Ошибка при записи в базу. Попробуйте позже.")
                    except Exception:
                        pass
                    return
                try:
                    await message.reply(f"Пользователь {target} (ID {target_id}) разбанен в предложке.")
                except Exception:
                    pass
                try:
                    await bot.send_message(target_id, "Вас разбанили в системе предложений постов. Вы снова можете предлагать посты.")
                except Exception:
                    pass
            return

    # Otherwise, normal propose-mode handling below

    await ensure_user_row(uid)
    row = await get_user(uid)
    in_propose = row["in_propose"] if row else False
    if not in_propose:
        return

    banned_until = row["banned_until"] if row else 0
    now = int(time.time())
    lang = row["lang"] if row else "ru"
    if banned_until and banned_until > now:
        rem = format_remaining(banned_until)
        text = BANNED_NOTICE_UK.format(period=rem) if lang == "uk" else BANNED_NOTICE_RU.format(period=rem)
        await message.reply(text)
        await set_in_propose(uid, False)
        return

    # create DB entry
    try:
        proposal_id = await create_proposal_entry(uid, message.chat.id, message.message_id)
    except Exception:
        await message.reply("Ошибка при сохранении заявки. Попробуйте позже.")
        await set_in_propose(uid, False)
        return

    # header text
    post_ts = int(time.time())
    hhmm = datetime.fromtimestamp(post_ts).strftime("%H:%M")
    human = human_date(post_ts)
    try:
        mention_text = user_mention_html_from_user(user)
        header_text = f"От {mention_text} • {hhmm} • {human}"
    except Exception:
        header_text = f"От {uid} • {hhmm} • {human}"

    if PREDLOJKA_ID is None:
        await message.reply("PREDLOJKA_ID not configured in environment. Обратитесь к администратору.")
        await set_in_propose(uid, False)
        return

    group_post_msg_id = None
    group_header_msg_id = None
    group_mod_msg_id = None

    try:
        header_sent = await bot.send_message(PREDLOJKA_ID, header_text, parse_mode="HTML")
        group_header_msg_id = header_sent.message_id

        if message.content_type == ContentType.TEXT:
            orig_text = message.text or ""
            html_text = entities_to_html(orig_text, message.entities or [])
            combined_html = f"{html_text}\n\n{APPENDED_LINKS_HTML}"
            sent = await bot.send_message(
                PREDLOJKA_ID,
                combined_html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            group_post_msg_id = sent.message_id
            attached = await safe_edit_message_replace(bot, PREDLOJKA_ID, group_post_msg_id, combined_html, mod_buttons_vertical(proposal_id))
            if attached:
                group_mod_msg_id = group_post_msg_id
            else:
                try:
                    await bot.edit_message_reply_markup(chat_id=PREDLOJKA_ID, message_id=group_header_msg_id, reply_markup=mod_buttons_vertical(proposal_id))
                    group_mod_msg_id = group_header_msg_id
                except Exception:
                    pass

        else:
            copied = await bot.copy_message(chat_id=PREDLOJKA_ID, from_chat_id=message.chat.id, message_id=message.message_id)
            if copied:
                group_post_msg_id = copied.message_id
                existing_caption = getattr(message, "caption", None)
                existing_text = getattr(message, "text", None)
                base = existing_caption if existing_caption is not None else (existing_text or "")
                if base:
                    caption_entities = getattr(message, "caption_entities", None) or getattr(message, "entities", None) or []
                    base_html = entities_to_html(base, caption_entities)
                else:
                    base_html = ""
                combined_html = f"{base_html}\n\n{APPENDED_LINKS_HTML}" if base_html else f"{APPENDED_LINKS_HTML}"
                attached = await safe_edit_message_replace(bot, PREDLOJKA_ID, group_post_msg_id, combined_html, mod_buttons_vertical(proposal_id))
                if attached:
                    group_mod_msg_id = group_post_msg_id
                else:
                    try:
                        await bot.edit_message_reply_markup(chat_id=PREDLOJKA_ID, message_id=group_header_msg_id, reply_markup=mod_buttons_vertical(proposal_id))
                        group_mod_msg_id = group_header_msg_id
                    except Exception:
                        pass

        if group_mod_msg_id is None:
            try:
                await bot.edit_message_reply_markup(chat_id=PREDLOJKA_ID, message_id=group_header_msg_id, reply_markup=mod_buttons_vertical(proposal_id))
                group_mod_msg_id = group_header_msg_id
            except Exception:
                pass

    except Exception:
        try:
            await message.reply("Ошибка при отправке в предложку. Попробуйте позже.")
        except Exception:
            pass
        await set_in_propose(uid, False)
        return

    try:
        await update_proposal_ids(proposal_id, header_msg_id=group_header_msg_id, post_msg_id=group_post_msg_id, mod_msg_id=group_mod_msg_id)
    except Exception:
        pass

    confirm_text = CONFIRM_SENT_UK if lang == "uk" else CONFIRM_SENT_RU
    try:
        await message.reply(confirm_text)
    except Exception:
        try:
            await bot.send_message(uid, confirm_text)
        except Exception:
            pass

    await set_in_propose(uid, False)

    await asyncio.sleep(0.5)
    row2 = await get_user(uid)
    rep2 = row2["reputation"] if row2 else 0
    accepted2 = row2["accepted_count"] if row2 else 0
    declined2 = row2["declined_count"] if row2 else 0
    welcome = WELCOME_UK.format(rep=rep2, accepted=accepted2, declined=declined2) if lang == "uk" else WELCOME_RU.format(rep=rep2, accepted=accepted2, declined=declined2)
    try:
        await bot.send_message(uid, welcome, reply_markup=main_menu_kb(lang), parse_mode="HTML")
        prompt = "Выберите действие:" if lang != "uk" else "Оберіть дію:"
        await bot.send_message(uid, prompt, reply_markup=persistent_reply_kb(lang))
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

    target_chat = call.message.chat.id
    target_msg_id = call.message.message_id

    if action == "accept":
        # If we have CHANNEL_ID and a recorded post message, try to post to channel.
        # For text posts: send as text with disable_web_page_preview=True (so links remain but preview is hidden).
        # For media / other types: fall back to copy_message.
        if CHANNEL_ID and prop.get("group_post_msg_id"):
            try:
                # If the moderator clicked the button on a text message, prefer send_message with preview disabled.
                content_type = getattr(call.message, "content_type", None)
                if content_type == ContentType.TEXT:
                    # Use the message text (or APPENDED_LINKS_HTML) and ensure appended links are present.
                    content = call.message.text or ""
                    combined = f"{content}\n\n{APPENDED_LINKS_HTML}" if content else APPENDED_LINKS_HTML
                    try:
                        await bot.send_message(chat_id=CHANNEL_ID, text=combined, parse_mode="HTML", disable_web_page_preview=True)
                    except Exception:
                        # fallback: try copy_message (may fail if message was edited/removed)
                        try:
                            await bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=PREDLOJKA_ID, message_id=prop["group_post_msg_id"])
                        except Exception:
                            pass
                else:
                    # For media / other message types, copy the message (preserves media)
                    try:
                        await bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=PREDLOJKA_ID, message_id=prop["group_post_msg_id"])
                    except Exception:
                        pass
            except Exception:
                # ignore channel-send errors
                pass

        # update DB state and show rep-buttons for moderators
        await set_proposal_status_and_mod(proposal_id, "accepted", None, "accept", None)
        try:
            await safe_edit_message_replace(bot, target_chat, target_msg_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, rep_buttons_vertical(proposal_id))
        except Exception:
            try:
                await bot.edit_message_reply_markup(chat_id=target_chat, message_id=target_msg_id, reply_markup=rep_buttons_vertical(proposal_id))
            except Exception:
                pass
        return

    if action == "decline":
        try:
            await safe_edit_message_replace(bot, call.message.chat.id, call.message.message_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, decline_penalty_kb(proposal_id))
        except Exception:
            try:
                await bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=decline_penalty_kb(proposal_id))
            except Exception:
                pass
        return

    if action == "ban":
        try:
            await safe_edit_message_replace(bot, call.message.chat.id, call.message.message_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, ban_duration_kb(proposal_id))
        except Exception:
            try:
                await bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=ban_duration_kb(proposal_id))
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
            await safe_edit_message_replace(bot, call.message.chat.id, call.message.message_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, mod_buttons_vertical(proposal_id))
        except Exception:
            try:
                await bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=mod_buttons_vertical(proposal_id))
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

    user_id = prop["user_id"]
    user_chat_id = prop["user_chat_id"]
    user_msg_id = prop["user_msg_id"]

    mod_id = call.from_user.id

    if penalty == 0:
        await set_proposal_status_and_mod(proposal_id, "declined", mod_id, "decline", "0")
        await increment_declined(user_id, 1)
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
            await safe_edit_message_replace(bot, call.message.chat.id, call.message.message_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, final_choice_kb(final_label, proposal_id))
        except Exception:
            pass
        return
    elif penalty == 1:
        await add_reputation(user_id, -1)
        await set_proposal_status_and_mod(proposal_id, "declined", mod_id, "decline", "-1")
        await increment_declined(user_id, 1)
        try:
            await ensure_update_custom_title(bot, user_id)
        except Exception:
            pass
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
            await safe_edit_message_replace(bot, call.message.chat.id, call.message.message_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, final_choice_kb(final_label, proposal_id))
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
            await safe_edit_message_replace(bot, call.message.chat.id, call.message.message_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, mod_buttons_vertical(proposal_id))
        except Exception:
            pass
        return

    prop = await get_proposal(proposal_id)
    if not prop:
        return

    user_id = prop["user_id"]
    user_chat_id = prop["user_chat_id"]
    user_msg_id = prop["user_msg_id"]

    now = int(time.time())
    if dur == "12h":
        until = now + 12 * 3600
        timestr = "12 часов"
    elif dur == "24h":
        until = now + 24 * 3600
        timestr = "24 часов"
    elif dur == "3d":
        until = now + 3 * 24 * 3600
        timestr = "3 дня"
    elif dur == "7d":
        until = now + 7 * 24 * 3600
        timestr = "1 неделя"
    elif dur == "forever":
        until = 2 ** 31 - 1
        timestr = "навсегда"
    else:
        return

    await set_banned_until(user_id, until)
    await set_proposal_status_and_mod(proposal_id, "banned", call.from_user.id, "ban", timestr)

    urow = await get_user(user_id)
    lang = urow["lang"] if urow else "ru"
    period = format_remaining(until)
    text = BANNED_NOTICE_UK.format(period=period) if lang == "uk" else BANNED_NOTICE_RU.format(period=period)
    try:
        await bot.send_message(user_chat_id, text, reply_to_message_id=user_msg_id)
    except Exception:
        try:
            await bot.send_message(user_chat_id, text)
        except Exception:
            pass

    final_label = f"🚫 Бан"
    try:
        await safe_edit_message_replace(bot, call.message.chat.id, call.message.message_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, final_choice_kb(final_label, proposal_id))
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

    user_id = prop["user_id"]
    user_chat_id = prop["user_chat_id"]
    user_msg_id = prop["user_msg_id"]

    await add_reputation(user_id, rep_amount)
    await increment_accepted(user_id, 1)
    await set_proposal_status_and_mod(proposal_id, "published", call.from_user.id, "accept", str(rep_amount))

    try:
        await ensure_update_custom_title(bot, user_id)
    except Exception:
        pass

    urow = await get_user(user_id)
    lang = urow["lang"] if urow else "ru"
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
        await safe_edit_message_replace(bot, call.message.chat.id, call.message.message_id, call.message.caption or call.message.text or APPENDED_LINKS_HTML, final_choice_kb(final_label, proposal_id))
    except Exception:
        pass

@dp.callback_query(F.data and F.data.startswith("info:"))
async def cb_info(call: types.CallbackQuery):
    parts = call.data.split(":")
    proposal_id = int(parts[1]) if len(parts) > 1 else None
    if proposal_id is None:
        await call.answer("Не найдена заявка.", show_alert=True)
        return
    prop = await get_proposal(proposal_id)
    if not prop:
        await call.answer("Не найдена заявка.", show_alert=True)
        return

    # --- Ensure info callback is registered (fallback) ---
# Some environments / decorator ordering can cause the decorated handler not to be active;
# register explicitly as a fallback so button presses "info:..." are always handled.
try:
    dp.callback_query.register(cb_info, lambda c: c.data and c.data.startswith("info:"))
except Exception:
    # ignore registration errors (older aiogram versions / duplicate registration)
    pass

    proposer_id = prop["user_id"]
    mod_id = prop["mod_id"]

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

    def nick_and_username(u: Optional[types.User]) -> (str, str):
        if not u:
            return ("отсутствует", "отсутствует")
        nick = u.full_name or str(u.id)
        uname = f"@{u.username}" if getattr(u, "username", None) else "отсутствует"
        return (nick, uname)

    a_nick, a_uname = nick_and_username(proposer)
    m_nick, m_uname = nick_and_username(moderator)

    action_key = prop["mod_action"] or "—"
    param = prop["mod_action_param"] or "—"

    action_label = "—"
    if action_key == "accept":
        action_label = "✅ Принять"
    elif action_key == "decline":
        action_label = "❌ Отклонить"
    elif action_key == "ban":
        action_label = "🚫 Бан пользователя"
    else:
        action_label = action_key

    if action_key == "ban":
        urow = await get_user(proposer_id)
        banned_until = urow["banned_until"] if urow else 0
        rep_or_ban = f"Срок бана: {format_remaining(banned_until)}"
    else:
        try:
            v = int(param)
            rep_or_ban = f"Репутация: {v:+d}"
        except Exception:
            rep_or_ban = f"Репутация: {param}"

    info_text = (
        f"©️ 𝗔𝗨𝗧𝗛𝗢𝗥\n"
        f"Ник: {escape_html(a_nick)}\n"
        f"Юз: {a_uname}\n"
        f"Айди: {proposer_id}\n\n"
        f"🛡️ 𝗔𝗗𝗠𝗜𝗡\n"
        f"Ник: {escape_html(m_nick)}\n"
        f"Юз: {m_uname}\n"
        f"Айди: {mod_id or '—'}\n\n"
        f"ℹ️ 𝗔𝗖𝗧𝗜𝗢𝗡\n"
        f"Действие: {action_label}\n"
        f"{rep_or_ban}"
    )
    await call.answer(info_text, show_alert=True)

# ---------- /info command (registered) ----------
@dp.message(Command("info"))
async def cmd_info_entry(message: types.Message):
    # /info invoked explicitly: delegate to command.handle_info
    try:
        import command
        await command.handle_info(message)
    except Exception:
        # fallback: minimal info
        user = message.from_user
        await ensure_user_row(user.id)
        row = await get_user(user.id)
        lang = row["lang"] if row else "ru"
        rep = row["reputation"] if row else 0
        accepted = row["accepted_count"] if row else 0
        has_title_now = await has_rep_title(bot, user.id)
        text = (main_menu_kb(lang) and "Информация недоступна") if False else f"🆙 Репутация: {rep}\n✅ Принятых: {accepted}"
        try:
            await message.answer(text)
        except Exception:
            pass

@dp.message(F.text.lower().in_({"инфо", "інфо", "информация", "інформація"}))
async def cmd_info_text_variants(message: types.Message):
    # text variants — delegate to command.handle_info
    try:
        import command
        await command.handle_info(message)
    except Exception:
        # fallback: do nothing
        pass

@dp.callback_query(F.data.startswith("toggle_rep:"))
async def cb_toggle_rep(call: types.CallbackQuery):
    await call.answer()
    parts = call.data.split(":")
    target_id = int(parts[1])
    user = call.from_user
    if user.id != target_id:
        await call.answer("❌ Это не ваша кнопка", show_alert=True)
        return
    row = await get_user(user.id)
    lang = row["lang"] if row else "ru"
    rep = row["reputation"] if row else 0
    has = await has_rep_title(bot, user.id)
    if not has:
        if rep < 25:
            msg = "❌ Вы не можете отобразить свою репутацию если у Вас меньше 25-ти балов репутации" if lang != "uk" else "❌ Ви не можете відобразити свою репутацію, якщо у Вас менше 25 балів репутації"
            await call.answer(msg, show_alert=True)
            return
        ok = await grant_rep_title_bot_admin(bot, user.id, rep)
        if ok:
            msg = "➕ Вы установили отображение репутации рядом со своим никнеймом." if lang != "uk" else "➕ Ви встановили відображення репутації поруч зі своїм нікнейком."
            await call.answer(msg, show_alert=True)
        else:
            await call.answer("Ошибка при установке отображения. Обратитесь к администратору.", show_alert=True)
    else:
        ok = await remove_rep_title_and_demote(bot, user.id)
        if ok:
            msg = "➖ Преписка с вашей репутацией была убрана из отображения рядом с вашим никнеймом." if lang != "uk" else "➖ Приписка з вашою репутацією була видалена з відображення поруч із вашим нікнейком."
            await call.answer(msg, show_alert=True)
        else:
            await call.answer("Не удалось убрать отображение. Обратитесь к администратору.", show_alert=True)

# ---------- Разбан command handled via message handler delegation earlier ----------
# (also there is still legacy unban_command_in_group but delegation is primary)

# ---------- Background unban notifier ----------
async def unban_watcher():
    while True:
        try:
            now = int(time.time())
            async with DB_POOL.acquire() as conn:
                rows = await conn.fetch("SELECT user_id, banned_until, lang FROM users WHERE banned_until > 0 AND banned_until <= $1", now)
                if rows:
                    for r in rows:
                        user_id = r["user_id"]
                        await conn.execute("UPDATE users SET banned_until = 0 WHERE user_id = $1", user_id)
                        lang = r["lang"] if r and "lang" in r else "ru"
                        text = UNBANNED_NOTICE_UK if lang == "uk" else UNBANNED_NOTICE_RU
                        try:
                            await bot.send_message(user_id, text)
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

# ---------- Utilities: entity -> HTML converter ----------
def entities_to_html(text: str, entities: Optional[List[MessageEntity]]) -> str:
    if not entities:
        return escape_html(text)
    ents = sorted(entities, key=lambda e: e.offset)
    parts = []
    last = 0
    for e in ents:
        start = e.offset
        end = e.offset + e.length
        if start > last:
            parts.append(escape_html(text[last:start]))
        segment = text[start:end]
        seg_escaped = escape_html(segment)
        etype = getattr(e, "type", None) or getattr(e, "t", None)
        if etype == "bold":
            parts.append(f"<b>{seg_escaped}</b>")
        elif etype == "italic":
            parts.append(f"<i>{seg_escaped}</i>")
        elif etype == "underline":
            parts.append(f"<u>{seg_escaped}</u>")
        elif etype == "strikethrough":
            parts.append(f"<s>{seg_escaped}</s>")
        elif etype == "code":
            parts.append(f"<code>{seg_escaped}</code>")
        elif etype == "pre":
            lang = getattr(e, "language", "")
            if lang:
                parts.append(f"<pre><code class=\"language-{escape_html(lang)}\">{seg_escaped}</code></pre>")
            else:
                parts.append(f"<pre>{seg_escaped}</pre>")
        elif etype == "text_link":
            url = getattr(e, "url", "")
            parts.append(f'<a href="{escape_html(url)}">{seg_escaped}</a>')
        elif etype == "text_mention":
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

# ---------- START ----------
async def main():
    global DB_POOL
    await init_db()
    try:
        await start_health_server()
    except Exception as e:
        print(f"[health] failed to start health server: {e}")

    asyncio.create_task(unban_watcher())
    try:
        await dp.start_polling(bot)
    finally:
        try:
            if DB_POOL:
                await DB_POOL.close()
        except Exception:
            pass
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
