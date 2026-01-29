# bot.py
# Main bot: propose flow, moderation UI, DB on Postgres (Neon via asyncpg)

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
)
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

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required for Neon/Postgres")

DB_POOL: Optional[asyncpg.Pool] = None

CHECK_UNBAN_SECONDS = 60  # background check interval

# ---------- TEXTS ----------
LANG_PROMPT_RU = "🗣️ Выберите язык"
LANG_PROMPT_UK = "🗣️ Виберіть мову"

WELCOME_RU = (
    "**👋 Добро пожаловать в бота «СГП»!**\n"
    "Здесь Вы можете предложить пост или обратиться в поддержку канала.\n\n"
    "🆙 Ваша репутация: {rep}\n"
    "✅ Принятых постов: {accepted}\n"
    "❌ Отклонённых постов: {declined}\n\n"
    "Репутацию можно повысить предложив пост, который в следствии будет одобрен."
)

WELCOME_UK = (
    "**👋 Ласкаво просимо до бота «СГП»!**\n"
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

CONFIRM_SENT_RU = "✅ Ваш пост отправлен на рассмотрение."
CONFIRM_SENT_UK = "✅ Ваш пост відправлений на розгляд."

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

# appended text with links (HTML)
APPENDED_LINKS_HTML = (
    '<a href="https://t.me/predlojka_gp_bot">Предложить пост</a>  •  '
    '<a href="https://t.me/comments_gp_plavni">Чат</a>  •  '
    '<a href="https://t.me/boost/channel_gp_plavni">Буст</a>'
)

# privacy links
PRIVACY_RU = "https://telegra.ph/Politika-konfidencialnosti-01-29-96"
PRIVACY_UK = "https://telegra.ph/Pol%D1%96tika-konf%D1%96denc%D1%96jnost%D1%96-01-29"

# ---------- DB (asyncpg) ----------
async def init_db_pool():
    global DB_POOL
    DB_POOL = await asyncpg.create_pool(DATABASE_URL, max_size=10)
    # create tables if needed
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """
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
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proposals (
                id BIGSERIAL PRIMARY KEY,
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
        )


async def ensure_user_row(user_id: int):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )


async def set_user_lang(user_id: int, lang: str):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, lang, lang_selected) VALUES ($1, $2, true)
            ON CONFLICT (user_id) DO UPDATE SET lang = EXCLUDED.lang, lang_selected = true
            """,
            user_id,
            lang,
        )


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, lang, lang_selected, reputation, banned_until, in_propose, accepted_count, declined_count FROM users WHERE user_id = $1",
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


async def increment_accepted(user_id: int, delta: int = 1):
    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE users SET accepted_count = accepted_count + $1 WHERE user_id = $2", delta, user_id)


async def increment_declined(user_id: int, delta: int = 1):
    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE users SET declined_count = declined_count + $1 WHERE user_id = $2", delta, user_id)


async def create_proposal_entry(user_id: int, user_chat_id: int, user_msg_id: int) -> int:
    ts = int(time.time())
    async with DB_POOL.acquire() as conn:
        rec = await conn.fetchrow(
            "INSERT INTO proposals (user_id, user_chat_id, user_msg_id, created_at) VALUES ($1, $2, $3, $4) RETURNING id",
            user_id,
            user_chat_id,
            user_msg_id,
            ts,
        )
        return rec["id"]


async def update_proposal_ids(proposal_id: int, header_msg_id: int = None, post_msg_id: int = None, mod_msg_id: int = None):
    parts = []
    args = []
    if header_msg_id is not None:
        parts.append("group_header_msg_id = $%d" % (len(args) + 1))
        args.append(header_msg_id)
    if post_msg_id is not None:
        parts.append("group_post_msg_id = $%d" % (len(args) + 1))
        args.append(post_msg_id)
    if mod_msg_id is not None:
        parts.append("group_mod_msg_id = $%d" % (len(args) + 1))
        args.append(mod_msg_id)
    if not parts:
        return
    set_clause = ", ".join(parts)
    async with DB_POOL.acquire() as conn:
        await conn.execute(f"UPDATE proposals SET {set_clause} WHERE id = ${len(args)+1}", *args, proposal_id)


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
            "SELECT id, user_id, user_chat_id, user_msg_id, group_header_msg_id, group_post_msg_id, group_mod_msg_id, created_at, status, mod_id, mod_action, mod_action_param FROM proposals WHERE id = $1",
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


def privacy_kb(lang: str):
    url = PRIVACY_UK if lang == "uk" else PRIVACY_RU
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Политика конфиденциальности" if lang != "uk" else "📋 Політика конфіденційності", url=url)]
    ])
    return kb


def cancel_kb(lang: str):
    txt = CANCEL_TEXT_UK if lang == "uk" else CANCEL_TEXT_RU
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=txt, callback_data="propose:cancel")]
    ])
    return kb


def system_reply_kb(lang: str) -> ReplyKeyboardMarkup:
    # system (bottom) keyboard — localized
    if lang == "uk":
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton("📋 Меню")],
                [KeyboardButton("🖼️ Запропонувати пост"), KeyboardButton("📩 Підтримка")],
                [KeyboardButton("🗣️ Змінити мову")]
            ],
            resize_keyboard=True
        )
    else:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton("📋 Меню")],
                [KeyboardButton("🖼️ Предложить пост"), KeyboardButton("📩 Поддержка")],
                [KeyboardButton("🗣️ Сменить язык")]
            ],
            resize_keyboard=True
        )
    return kb


def mod_buttons_vertical(proposal_id: int):
    # vertical: each button in its own row
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
    # awarding reputation: vertical buttons
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆙 +3 репутации", callback_data=f"rep:3:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 +2 репутации", callback_data=f"rep:2:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 +1 репутация", callback_data=f"rep:1:{proposal_id}")],
    ])
    return kb


def decline_penalty_kb(proposal_id: int):
    # first choose penalty -0 or -1 (vertical)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆙 -0 репутации", callback_data=f"declpen:0:{proposal_id}")],
        [InlineKeyboardButton(text="🆙 -1 репутация", callback_data=f"declpen:1:{proposal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"declpen:back:{proposal_id}")],
    ])
    return kb


def final_choice_kb(action_label: str, proposal_id: int):
    # single info button
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
    month_name = dt.strftime("%B")
    return f"{day} {month_name}"


def user_mention_html_from_user(user: types.User) -> str:
    if user.username:
        return f"@{user.username}"
    else:
        full_name = (user.full_name or str(user.id))
        return f'<a href="tg://user?id={user.id}">{full_name}</a>'


# ---------- BOT SETUP ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# helper: robust send/copy with retries
async def _retry(coro_fn, *args, attempts=3, delay=0.5, **kwargs):
    last_exc = None
    for i in range(attempts):
        try:
            return await coro_fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            await asyncio.sleep(delay)
    raise last_exc


# ---------- HANDLERS ----------
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    await ensure_user_row(user.id)
    row = await get_user(user.id)
    # If user selected language before -> show main menu immediately
    if row and row["lang_selected"]:
        lang = row["lang"] or "ru"
        rep = row["reputation"]
        accepted = row["accepted_count"]
        declined = row["declined_count"]
        text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
        # send welcome + system keyboard
        await message.answer(text, reply_markup=system_reply_kb(lang), parse_mode="Markdown")
        return

    # else show language selection
    prompt = LANG_PROMPT_UK if (row and row.get("lang") == "uk") else LANG_PROMPT_RU
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

    # send welcome message in chosen language + show reply keyboard
    row = await get_user(user_id)
    rep = row["reputation"] if row else 0
    accepted = row["accepted_count"] if row else 0
    declined = row["declined_count"] if row else 0
    text = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
    # show system keyboard
    await bot.send_message(user_id, text, reply_markup=system_reply_kb(lang), parse_mode="Markdown")


@dp.callback_query(F.data == "main:lang")
async def cb_main_change_lang(call: types.CallbackQuery):
    await call.answer()
    row = await get_user(call.from_user.id)
    lang = row["lang"] if (row and row.get("lang")) else "ru"
    prompt = LANG_PROMPT_UK if lang == "uk" else LANG_PROMPT_RU
    # hide system keyboard while selecting language
    try:
        await call.message.delete_reply_markup()
    except Exception:
        pass
    await bot.send_message(call.from_user.id, prompt, reply_markup=make_lang_kb())


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
    await call.message.answer(text, reply_markup=system_reply_kb(lang), parse_mode="Markdown")


# while in propose mode: treat any incoming content as a post
@dp.message()
async def handle_any_message(message: types.Message):
    user = message.from_user
    uid = user.id
    await ensure_user_row(uid)
    row = await get_user(uid)
    in_propose = row["in_propose"] if row else False
    if not in_propose:
        # Also allow quick actions from reply keyboard
        text = (message.text or "").strip()
        if text in ("📋 Меню", "📋 Меню".strip()):
            # show main menu
            lang = row["lang"] if row else "ru"
            rep = row["reputation"] if row else 0
            accepted = row["accepted_count"] if row else 0
            declined = row["declined_count"] if row else 0
            txt = WELCOME_UK.format(rep=rep, accepted=accepted, declined=declined) if lang == "uk" else WELCOME_RU.format(rep=rep, accepted=accepted, declined=declined)
            await message.answer(txt, reply_markup=system_reply_kb(lang), parse_mode="Markdown")
            return
        if text in ("🖼️ Предложить пост", "🖼️ Запропонувати пост"):
            # simulate pressing propose
            await set_in_propose(uid, True)
            lang = row["lang"] if row else "ru"
            prompt = PROPOSE_PROMPT_UK if lang == "uk" else PROPOSE_PROMPT_RU
            await message.answer(prompt, reply_markup=cancel_kb(lang))
            return
        if text in ("📩 Поддержка", "📩 Підтримка"):
            # forward to bot2 or handle support
            # we will instruct user to press inline support or call /support
            await message.answer("Откройте поддержку: /support")
            return
        if text in ("🗣️ Сменить язык", "🗣️ Змінити мову"):
            await message.answer(LANG_PROMPT_UK if (row and row.get("lang") == "uk") else LANG_PROMPT_RU, reply_markup=make_lang_kb())
            return
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
    proposal_id = await create_proposal_entry(uid, message.chat.id, message.message_id)

    # header text (we will send header as first message)
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

    group_header_msg_id = None
    group_post_msg_id = None
    group_mod_msg_id = None

    try:
        # 1) send header message (plain, HTML)
        sent_header = await _retry(bot.send_message, PREDLOJKA_ID, header_text, parse_mode="HTML", disable_web_page_preview=True)
        group_header_msg_id = sent_header.message_id

        # 2) send/copy content as second message with appended links (no third message)
        if message.content_type == ContentType.TEXT:
            orig_text = message.text or ""
            html_text = entities_to_html(orig_text, message.entities or [])
            combined_html = f"{html_text}\n\n{APPENDED_LINKS_HTML}" if html_text else APPENDED_LINKS_HTML
            sent_post = await _retry(bot.send_message, PREDLOJKA_ID, combined_html, parse_mode="HTML", disable_web_page_preview=True)
            group_post_msg_id = sent_post.message_id
        else:
            # copy media, then edit caption to append header + links (but header already in first message)
            copied = await _retry(bot.copy_message, PREDLOJKA_ID, from_chat_id=message.chat.id, message_id=message.message_id)
            group_post_msg_id = copied.message_id
            # build new caption
            existing_caption = getattr(message, "caption", None)
            existing_text = getattr(message, "text", None)
            base = existing_caption if existing_caption is not None else (existing_text or "")
            base_html = entities_to_html(base, getattr(message, "caption_entities", None) or [])
            combined_html = f"{base_html}\n\n{APPENDED_LINKS_HTML}" if base_html else APPENDED_LINKS_HTML
            # Try to edit caption, if fails edit text
            try:
                await _retry(bot.edit_message_caption, chat_id=PREDLOJKA_ID, message_id=group_post_msg_id, caption=combined_html, parse_mode="HTML")
            except Exception:
                try:
                    await _retry(bot.edit_message_text, chat_id=PREDLOJKA_ID, message_id=group_post_msg_id, text=combined_html, parse_mode="HTML")
                except Exception:
                    pass

        # 3) send moderation message with buttons (no standalone links-only message)
        mod_msg = await _retry(bot.send_message, PREDLOJKA_ID, APPENDED_LINKS_HTML, parse_mode="HTML", reply_markup=mod_buttons_vertical(proposal_id))
        group_mod_msg_id = mod_msg.message_id

    except Exception as e:
        # if anything failed, notify user and abort gracefully and mark in_propose False
        await message.reply("Ошибка при отправке в предложку. Попробуйте позже.")
        await set_in_propose(uid, False)
        return

    # update proposal record
    await update_proposal_ids(proposal_id, header_msg_id=group_header_msg_id, post_msg_id=group_post_msg_id, mod_msg_id=group_mod_msg_id)

    # notify user immediately that post is under review
    confirm_text = CONFIRM_SENT_UK if lang == "uk" else CONFIRM_SENT_RU
    try:
        await message.reply(confirm_text)
    except Exception:
        try:
            await bot.send_message(uid, confirm_text)
        except Exception:
            pass

    # exit propose mode
    await set_in_propose(uid, False)

    # after 1 second send main menu again
    await asyncio.sleep(1)
    row2 = await get_user(uid)
    rep2 = row2["reputation"] if row2 else 0
    accepted2 = row2["accepted_count"] if row2 else 0
    declined2 = row2["declined_count"] if row2 else 0
    welcome = WELCOME_UK.format(rep=rep2, accepted=accepted2, declined=declined2) if lang == "uk" else WELCOME_RU.format(rep=rep2, accepted=accepted2, declined=declined2)
    try:
        await bot.send_message(uid, welcome, reply_markup=system_reply_kb(lang), parse_mode="Markdown")
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

    user_id = prop["user_id"]
    user_chat_id = prop["user_chat_id"]
    user_msg_id = prop["user_msg_id"]

    # Accept => change mod message to rep awarding buttons (vertical)
    if action == "accept":
        # copy the group's post message (bot's message) to CHANNEL_ID (so it will include appended links)
        if CHANNEL_ID and prop.get("group_post_msg_id"):
            try:
                await _retry(bot.copy_message, CHANNEL_ID, from_chat_id=PREDLOJKA_ID, message_id=prop["group_post_msg_id"])
            except Exception:
                pass
        # set status accepted (mod_id empty until final rep chosen)
        await set_proposal_status_and_mod(proposal_id, "accepted", None, "accept", None)
        # replace mod message (the one with links) with rep buttons
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=rep_buttons_vertical(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

    # Decline => show penalty options first
    if action == "decline":
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=decline_penalty_kb(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

    # Ban => show ban duration keyboard
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
        # go back to mod buttons
        try:
            await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=mod_buttons_vertical(proposal_id), parse_mode="HTML")
        except Exception:
            pass
        return

    # penalty value: 0 or 1
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

    # apply decline and penalty
    if penalty == 0:
        # no reputation change
        await set_proposal_status_and_mod(proposal_id, "declined", mod_id, "decline", "0")
        await increment_declined(user_id, 1)
        # notify user
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
        # subtract 1 reputation
        await add_reputation(user_id, -1)
        await set_proposal_status_and_mod(proposal_id, "declined", mod_id, "decline", "-1")
        await increment_declined(user_id, 1)
        # notify user about decline and penalty
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

    # notify user about ban
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

    # edit mod message to final choice button
    final_label = f"🚫 Бан"
    try:
        await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=final_choice_kb(final_label, proposal_id), parse_mode="HTML")
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

    # add reputation and mark published
    await add_reputation(user_id, rep_amount)
    await increment_accepted(user_id, 1)
    await set_proposal_status_and_mod(proposal_id, "published", call.from_user.id, "accept", str(rep_amount))

    # notify author
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

    # change mod message to final info button
    final_label = f"✅ Принять +{rep_amount}"
    try:
        await bot.edit_message_text(call.message.text or APPENDED_LINKS_HTML, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=final_choice_kb(final_label, proposal_id), parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data and F.data.startswith("info:"))
async def cb_info(call: types.CallbackQuery):
    # show an alert with info about proposal and moderator
    parts = call.data.split(":")
    proposal_id = int(parts[1]) if len(parts) > 1 else None
    if proposal_id is None:
        await call.answer("Ошибка: нет id.", show_alert=True)
        return
    prop = await get_proposal(proposal_id)
    if not prop:
        await call.answer("Не найдена заявка.", show_alert=True)
        return

    # get proposer and moderator info
    proposer_id = prop["user_id"]
    mod_id = prop["mod_id"]
    # fetch chat/user data via API to get usernames/nicks (best-effort)
    proposer = None
    moderator = None
    try:
        proposer = await bot.get_chat(proposer_id)
    except Exception:
        proposer = None
    if mod_id:
        try:
            moderator = await bot.get_chat(mod_id)
        except Exception:
            moderator = None

    def name_and_username(u: Optional[types.User]):
        if not u:
            return ("нет юзернейма", "нет юзернейма")
        nick = u.full_name or str(u.id)
        uname = f"@{u.username}" if getattr(u, "username", None) else "нет юзернейма"
        return (nick, uname)

    p_nick, p_uname = name_and_username(proposer)
    m_nick, m_uname = name_and_username(moderator)

    action = prop["mod_action"] or "—"
    param = prop["mod_action_param"] or "—"

    info_text = (
        f"📩 Предложил: {p_nick} • {p_uname} • {proposer_id}\n"
        f"😎 Обработал: {m_nick} • {m_uname} • {mod_id or '—'}\n"
        f"❓ Выбранное действие: {action} • {param}"
    )
    await call.answer(info_text, show_alert=True)


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
                        lang = r["lang"] or "ru"
                        text = UNBANNED_NOTICE_UK if lang == "uk" else UNBANNED_NOTICE_RU
                        try:
                            await bot.send_message(user_id, text)
                        except Exception:
                            pass
        except Exception:
            pass
        await asyncio.sleep(CHECK_UNBAN_SECONDS)


# ---------- Health server (for Render Web Service) ----------
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
def escape_html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def entities_to_html(text: str, entities: Optional[List[MessageEntity]]) -> str:
    """
    Convert message text + entities -> HTML string.
    Supports common entity types: bold, italic, code, pre, underline, strikethrough, text_link, text_mention, url.
    """
    if not text:
        return ""
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
            lang = getattr(e, "language", "")
            if lang:
                parts.append(f"<pre><code class=\"language-{escape_html(lang)}\">{seg_escaped}</code></pre>")
            else:
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


# ---------- START ----------
async def main():
    await init_db_pool()
    # start health server so Render sees an open port
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
        if DB_POOL:
            await DB_POOL.close()

if __name__ == "__main__":
    asyncio.run(main())
