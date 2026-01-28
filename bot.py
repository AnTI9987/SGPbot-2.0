# bot.py
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("predlojka_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PREDLOJKA_ID = os.getenv("PREDLOJKA_ID")  # must be set
CHANNEL_ID = os.getenv("CHANNEL_ID")      # must be set
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
if not PREDLOJKA_ID:
    raise RuntimeError("PREDLOJKA_ID is not set")
if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID is not set")

PREDLOJKA_ID = int(PREDLOJKA_ID)
CHANNEL_ID = int(CHANNEL_ID)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# --- DB helpers --------------------------------------------------------------
_pool: asyncpg.Pool | None = None

async def db_connect():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
        async with _pool.acquire() as conn:
            # create tables if not exists
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                lang TEXT DEFAULT 'ru',
                reputation INT DEFAULT 0,
                in_predlojka BOOLEAN DEFAULT FALSE,
                banned_until TIMESTAMP WITH TIME ZONE
            );
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                status TEXT NOT NULL DEFAULT 'pending',
                user_message_id INT,  -- message id in user chat (to reply)
                group_message_id INT, -- message id in mod group (first metadata message)
                group_post_copy_message_id INT -- message id of copied post in group (if any)
            );
            """)

async def db_get_user(user_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        return row

async def db_ensure_user(user_id: int):
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id)
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        return row

async def db_set_lang(user_id: int, lang: str):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET lang=$1 WHERE user_id=$2", lang, user_id)

async def db_set_in_predlojka(user_id: int, value: bool):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET in_predlojka=$1 WHERE user_id=$2", value, user_id)

async def db_set_ban(user_id: int, until_ts):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET banned_until=$1 WHERE user_id=$2", until_ts, user_id)

async def db_add_reputation(user_id: int, amount: int):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE users SET reputation = reputation + $1 WHERE user_id=$2", amount, user_id)
        row = await conn.fetchrow("SELECT reputation FROM users WHERE user_id=$1", user_id)
        return row['reputation']

async def db_create_post(user_id: int, user_message_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO posts (user_id, user_message_id) VALUES ($1, $2)
            RETURNING id, created_at
        """, user_id, user_message_id)
        return row

async def db_set_post_group_message(post_id: int, group_msg_id: int, group_post_copy_message_id: int | None = None):
    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE posts SET group_message_id=$1, group_post_copy_message_id=$2 WHERE id=$3
        """, group_msg_id, group_post_copy_message_id, post_id)

async def db_set_post_status(post_id: int, status: str):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE posts SET status=$1 WHERE id=$2", status, post_id)

async def db_get_post(post_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM posts WHERE id=$1", post_id)

# --- Utilities ---------------------------------------------------------------
RU_MONTHS = ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
UK_MONTHS = ["січня","лютого","березня","квітня","травня","червня","липня","серпня","вересня","жовтня","листопада","грудня"]

def format_time_and_date(dt: datetime, lang: str):
    dt_local = dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc)  # store as UTC
    hhmm = dt_local.strftime("%H:%M")
    day = dt_local.day
    mname = RU_MONTHS[dt_local.month - 1] if lang == "ru" else UK_MONTHS[dt_local.month - 1]
    date_text = f"{day} {mname}"
    return hhmm, date_text

def mention_for_user(user: types.User):
    if user.username:
        return f"@{user.username}"
    else:
        # HTML mention by id
        return f'<a href="tg://user?id={user.id}">{(user.full_name or "User")}</a>'

def human_timedelta_seconds(seconds: int, lang: str):
    # format as "0д, 0ч, 0м"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    return f"{d}д, {h}ч, {m}м"

# --- Keyboards ---------------------------------------------------------------
def lang_choice_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 RU", callback_data="setlang:ru"),
         InlineKeyboardButton(text="🇺🇦 UK", callback_data="setlang:uk")]
    ])
    return kb

def main_menu_kb(lang: str):
    if lang == "uk":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("🖼️ Запропонувати пост", callback_data="menu:predlojka")],
            [InlineKeyboardButton("📩 Підтримка", callback_data="menu:support")],
            [InlineKeyboardButton("🗣️ Змінити мову", callback_data="menu:lang")],
            [InlineKeyboardButton("📋 Політика конфіденційності", callback_data="menu:privacy")]
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("🖼️ Предложить пост", callback_data="menu:predlojka")],
            [InlineKeyboardButton("📩 Поддержка", callback_data="menu:support")],
            [InlineKeyboardButton("🗣️ Сменить язык", callback_data="menu:lang")],
            [InlineKeyboardButton("📋 Политика конфиденциальности", callback_data="menu:privacy")]
        ])
    return kb

def cancel_kb(lang: str):
    text = "❌ Скасувати" if lang == "uk" else "❌ Отменить"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text, callback_data="predlojka:cancel")]])

def group_moderation_kb(post_id: int, lang: str):
    # for pending posts
    if lang == "uk":
        buttons = [
            [InlineKeyboardButton("✅ Прийняти", callback_data=f"mod:accept:{post_id}"),
             InlineKeyboardButton("❌ Відхилити", callback_data=f"mod:reject:{post_id}")],
            [InlineKeyboardButton("🚫 Бан користувача", callback_data=f"mod:banmenu:{post_id}")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton("✅ Принять", callback_data=f"mod:accept:{post_id}"),
             InlineKeyboardButton("❌Отклонить", callback_data=f"mod:reject:{post_id}")],
            [InlineKeyboardButton("🚫 Бан пользователя", callback_data=f"mod:banmenu:{post_id}")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def group_ban_options_kb(post_id: int, lang: str):
    if lang == "uk":
        buttons = [
            [InlineKeyboardButton("🚫 12 год", callback_data=f"mod:ban:12h:{post_id}"),
             InlineKeyboardButton("🚫 24 год", callback_data=f"mod:ban:24h:{post_id}")],
            [InlineKeyboardButton("🚫 3 дні", callback_data=f"mod:ban:3d:{post_id}"),
             InlineKeyboardButton("🚫 1 тиждень", callback_data=f"mod:ban:7d:{post_id}")],
            [InlineKeyboardButton("🚫 Назавжди", callback_data=f"mod:ban:perm:{post_id}"),
             InlineKeyboardButton("◀️ Назад", callback_data=f"mod:back:{post_id}")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton("🚫 12 часов", callback_data=f"mod:ban:12h:{post_id}"),
             InlineKeyboardButton("🚫 24 часов", callback_data=f"mod:ban:24h:{post_id}")],
            [InlineKeyboardButton("🚫 3 дня", callback_data=f"mod:ban:3d:{post_id}"),
             InlineKeyboardButton("🚫 1 неделя", callback_data=f"mod:ban:7d:{post_id}")],
            [InlineKeyboardButton("🚫 Навсегда", callback_data=f"mod:ban:perm:{post_id}"),
             InlineKeyboardButton("◀️ Назад", callback_data=f"mod:back:{post_id}")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def reputation_kb(post_id: int, lang: str):
    # after accepting: +3, +2, +1 buttons
    if lang == "uk":
        buttons = [
            [InlineKeyboardButton("🆙 +3 репутації", callback_data=f"rep:3:{post_id}"),
             InlineKeyboardButton("🆙 +2 репутації", callback_data=f"rep:2:{post_id}"),
             InlineKeyboardButton("🆙 +1 репутація", callback_data=f"rep:1:{post_id}")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton("🆙 +3 репутации", callback_data=f"rep:3:{post_id}"),
             InlineKeyboardButton("🆙 +2 репутации", callback_data=f"rep:2:{post_id}"),
             InlineKeyboardButton("🆙 +1 репутация", callback_data=f"rep:1:{post_id}")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Handlers ---------------------------------------------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await db_ensure_user(message.from_user.id)
    # send language chooser (if later they reopen it and choose uk, the chooser itself should use ukrainian)
    await message.answer("🗣️ Выберите язык", reply_markup=lang_choice_kb())

@dp.callback_query(Text(startswith="setlang:"))
async def cb_set_lang(c: CallbackQuery):
    await db_ensure_user(c.from_user.id)
    _, lang = c.data.split(":", 1)
    await db_set_lang(c.from_user.id, lang)
    # delete language selection message
    try:
        await c.message.delete()
    except Exception:
        pass

    # send welcome message in chosen language
    user_row = await db_get_user(c.from_user.id)
    reputation = user_row['reputation'] if user_row else 0
    if lang == "uk":
        text = (
            "👋 Ласкаво просимо до бота «Сущности Горишних Плавней»!\n"
            "Тут ви можете запропонувати пост або звернутися до підтримки каналу.\n\n"
            "🆙 Ваша репутація\n"
            f"{reputation}\n\n"
            "Репутацію можна підвищити, запропонувавши пост, який в результаті буде схвалений. Чим цікавіший Ваш пост, тим більше репутації Ви заробите."
        )
    else:
        text = (
            "👋 Добро пожаловать в бота «Сущности Горишних Плавней»!\n"
            "Здесь Вы можете предложить пост или обратиться в поддержку канала.\n\n"
            "🆙 Ваша репутация\n"
            f"{reputation}\n\n"
            "Репутацию можно повысить предложив пост, который в следствии будет одобрен. Чем интереснее Ваш пост, тем больше репутации вы заработаете."
        )
    await c.message.answer(text, reply_markup=main_menu_kb(lang))

@dp.callback_query(Text(startswith="menu:"))
async def cb_menu(c: CallbackQuery):
    action = c.data.split(":", 1)[1]
    user = c.from_user
    await db_ensure_user(user.id)
    user_row = await db_get_user(user.id)
    lang = user_row['lang'] if user_row else 'ru'

    if action == "predlojka":
        # check ban
        now = datetime.now(timezone.utc)
        ban_until = user_row['banned_until']
        if ban_until and ban_until > now:
            secs = int((ban_until - now).total_seconds())
            text = f"🚫 Ви забанені у пропозиціях на {human_timedelta_seconds(secs, lang)}" if lang == "uk" else f"🚫 Вы забанены в предложках на {human_timedelta_seconds(secs, lang)}"
            await c.answer(text, show_alert=True)
            return
        # set in_predlojka true and ask for post
        await db_set_in_predlojka(user.id, True)
        if lang == "uk":
            await c.message.answer("🖼️ Надішліть свій пост. Це може бути відео, зображення або напис. Пам'ятайте: пост повинен відповідати нашій політиці конфіденційності.", reply_markup=cancel_kb(lang))
        else:
            await c.message.answer("🖼️ Пришлите свой пост. Это может быть видео, картинка или надпись. Помните: пост должен соответствовать нашей политикой конфиденциальности.", reply_markup=cancel_kb(lang))
    elif action == "support":
        # Not implemented (as requested keep others without functionality)
        await c.answer("В разработке...", show_alert=True)
    elif action == "lang":
        await c.message.answer("🗣️ Выберите язык", reply_markup=lang_choice_kb())
    elif action == "privacy":
        await c.answer("Политика конфиденциальности — в разработке...", show_alert=True)

@dp.callback_query(Text(startswith="predlojka:cancel"))
async def cb_predlojka_cancel(c: CallbackQuery):
    user_id = c.from_user.id
    await db_set_in_predlojka(user_id, False)
    user_row = await db_get_user(user_id)
    lang = user_row['lang'] if user_row else 'ru'
    # delete cancel message
    try:
        await c.message.delete()
    except Exception:
        pass
    # send main menu again
    reputation = user_row['reputation'] if user_row else 0
    if lang == "uk":
        text = (
            "👋 Ласкаво просимо до бота «Сущности Горишних Плавней»!\n"
            "Тут ви можете запропонувати пост або звернутися до підтримки каналу.\n\n"
            "🆙 Ваша репутація\n"
            f"{reputation}\n\n"
            "Репутацію можна підвищити, запропонувавши пост, який в результаті буде схвалений. Чим цікавіший Ваш пост, тим більше репутації Ви заробите."
        )
    else:
        text = (
            "👋 Добро пожаловать в бота «Сущности Горишних Плавней»!\n"
            "Здесь Вы можете предложить пост или обратиться в поддержку канала.\n\n"
            "🆙 Ваша репутация\n"
            f"{reputation}\n\n"
            "Репутацию можно повысить предложив пост, который в следствии будет одобрен. Чем интереснее Ваш пост, тем больше репутации вы заработаете."
        )
    await c.message.answer(text, reply_markup=main_menu_kb(lang))

@dp.message()
async def catch_predlojka_message(message: types.Message):
    # If user is in predlojka mode, treat any incoming message as the post
    await db_ensure_user(message.from_user.id)
    user_row = await db_get_user(message.from_user.id)
    if not user_row:
        return
    if not user_row['in_predlojka']:
        return  # ignore (no other functionality requested)

    # check ban again (safety)
    now = datetime.now(timezone.utc)
    ban_until = user_row['banned_until']
    lang = user_row['lang'] or 'ru'
    if ban_until and ban_until > now:
        secs = int((ban_until - now).total_seconds())
        await message.answer(("🚫 Ви забанені у пропозиціях на " + human_timedelta_seconds(secs,lang)) if lang=="uk" else ("🚫 Вы забанены в предложках на " + human_timedelta_seconds(secs,lang)))
        await db_set_in_predlojka(message.from_user.id, False)
        return

    # Save post row
    post_row = await db_create_post(message.from_user.id, message.message_id)
    post_id = post_row['id']
    created_at = post_row['created_at']

    # prepare metadata message
    hhmm, date_text = format_time_and_date(created_at, lang)
    author_mention = mention_for_user(message.from_user)
    meta = f"От {author_mention} • {hhmm} • {date_text}"

    # prepare appended links text
    links_line = (
        '<a href="https://t.me/predlojka_gp_bot">Предложить пост</a>  •  '
        '<a href="https://t.me/comments_gp_plavni">Чат</a>  •  '
        '<a href="https://t.me/boost/channel_gp_plavni">Буст</a>'
    )

    # Send metadata message to group
    mod_kb = group_moderation_kb(post_id, lang)
    group_meta = await bot.send_message(PREDLOJKA_ID, meta, reply_markup=mod_kb)
    group_post_copy_message_id = None

    # Attempt to copy the user's message into the group, trying to add the links to caption/text if possible.
    try:
        # for media groups (albums), messages in same media_group_id will have separate entries;
        # we'll simply forward/copy this single message and then (if necessary) send the links as separate message.
        # Use copy_message to preserve author and media (and allow new caption)
        original_text = message.caption or message.text or ""
        new_caption = (original_text + "\n\n" + links_line).strip()
        copied = await bot.copy_message(chat_id=PREDLOJKA_ID, from_chat_id=message.chat.id, message_id=message.message_id, caption=new_caption)
        group_post_copy_message_id = copied.message_id
    except Exception as e:
        # fallback: forward original and then send links as separate message
        try:
            await bot.forward_message(PREDLOJKA_ID, from_chat_id=message.chat.id, message_id=message.message_id)
            await bot.send_message(PREDLOJKA_ID, links_line)
        except Exception as ee:
            log.exception("Failed to forward/copy user post to group: %s %s", e, ee)

    # record group message ids
    await db_set_post_group_message(post_id, group_meta.message_id if group_meta else None, group_post_copy_message_id)

    # Respond to user that post is submitted
    if lang == "uk":
        await message.answer("✅ Ваш пост відправлений на розгляд. Зачекайте, поки його перевірять.")
    else:
        await message.answer("✅ Ваш пост отправлен на рассмотрение. Дождитесь, пока его проверят.")

    # exit predlojka mode for user
    await db_set_in_predlojka(message.from_user.id, False)

    # after 1 second, send main menu again
    await asyncio.sleep(1)
    reputation = user_row['reputation'] or 0
    if lang == "uk":
        text = (
            "👋 Ласкаво просимо до бота «Сущности Горишних Плавней»!\n"
            "Тут ви можете запропонувати пост або звернутися до підтримки каналу.\n\n"
            "🆙 Ваша репутація\n"
            f"{reputation}\n\n"
            "Репутацію можна підвищити, запропонувавши пост, який в результаті буде схвалений. Чим цікавіший Ваш пост, тим більше репутації Ви заробите."
        )
    else:
        text = (
            "👋 Добро пожаловать в бота «Сущности Горишних Плавней»!\n"
            "Здесь Вы можете предложить пост или обратиться в поддержку канала.\n\n"
            "🆙 Ваша репутация\n"
            f"{reputation}\n\n"
            "Репутацию можно повысить предложив пост, который в следствии будет одобрен. Чем интереснее Ваш пост, тем больше репутации вы заработаете."
        )
    await message.answer(text, reply_markup=main_menu_kb(lang))

# --- Moderation callbacks in group ------------------------------------------
@dp.callback_query(Text(startswith="mod:"))
async def cb_mod_actions(c: CallbackQuery):
    parts = c.data.split(":")
    action = parts[1]
    # actions: accept, reject, banmenu, ban:<duration>, back
    if action == "accept":
        post_id = int(parts[2])
        await handle_accept(c, post_id)
    elif action == "reject":
        post_id = int(parts[2])
        await handle_reject(c, post_id)
    elif action == "banmenu":
        post_id = int(parts[2])
        # fetch post to determine language by post author
        post = await db_get_post(post_id)
        if not post:
            await c.answer("Post not found", show_alert=True)
            return
        user_row = await db_get_user(post['user_id'])
        lang = user_row['lang'] if user_row else 'ru'
        await c.message.edit_reply_markup(reply_markup=group_ban_options_kb(post_id, lang))
    elif action == "back":
        post_id = int(parts[2])
        post = await db_get_post(post_id)
        user_row = await db_get_user(post['user_id'])
        lang = user_row['lang'] if user_row else 'ru'
        await c.message.edit_reply_markup(reply_markup=group_moderation_kb(post_id, lang))
    elif action == "ban":
        duration = parts[2]
        post_id = int(parts[3])
        await handle_ban_action(c, post_id, duration)
    # answer callback to avoid 'clock'
    await c.answer()

async def handle_accept(cq: CallbackQuery, post_id: int):
    post = await db_get_post(post_id)
    if not post:
        await cq.answer("Пост не найден", show_alert=True)
        return
    if post['status'] != 'pending':
        await cq.answer("Уже обработан", show_alert=True)
        return
    # copy the post to channel
    try:
        # If we have stored group_post_copy_message_id we can forward/copy that msg to channel
        if post['group_post_copy_message_id']:
            await bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=PREDLOJKA_ID, message_id=post['group_post_copy_message_id'])
        else:
            # fallback: copy the user message from the user's chat if we have user_message_id
            await bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=post['user_id'], message_id=post['user_message_id'])
    except Exception as e:
        log.exception("Failed to copy to channel: %s", e)

    # mark accepted
    await db_set_post_status(post_id, "accepted")

    # edit mod message buttons in group to reputation options
    user_row = await db_get_user(post['user_id'])
    lang = user_row['lang'] if user_row else 'ru'
    try:
        # switch the buttons of the moderation meta message to reputation options
        await bot.edit_message_reply_markup(chat_id=PREDLOJKA_ID, message_id=post['group_message_id'], reply_markup=reputation_kb(post_id, lang))
    except Exception:
        pass

async def handle_reject(cq: CallbackQuery, post_id: int):
    post = await db_get_post(post_id)
    if not post:
        await cq.answer("Пост не найден", show_alert=True)
        return
    if post['status'] != 'pending':
        await cq.answer("Уже обработан", show_alert=True)
        return
    await db_set_post_status(post_id, "rejected")
    # notify author in bot chat, in reply to their original message (if still exists)
    try:
        user_id = post['user_id']
        user_msg_id = post['user_message_id']
        user_row = await db_get_user(user_id)
        lang = user_row['lang'] if user_row else 'ru'
        text = "🙁 Ваш пост був відхилен" if lang=="uk" else "🙁 Ваш пост был отклонён."
        # try reply to the original message in the bot chat
        await bot.send_message(chat_id=user_id, text=text, reply_to_message_id=user_msg_id)
    except Exception:
        pass
    # edit mod message to remove buttons
    try:
        await bot.edit_message_reply_markup(chat_id=PREDLOJKA_ID, message_id=post['group_message_id'], reply_markup=None)
    except Exception:
        pass

async def handle_ban_action(cq: CallbackQuery, post_id: int, duration_key: str):
    post = await db_get_post(post_id)
    if not post:
        await cq.answer("Пост не найден", show_alert=True)
        return
    user_id = post['user_id']
    user_row = await db_get_user(user_id)
    lang = user_row['lang'] if user_row else 'ru'

    # durations mapping
    if duration_key == "12h":
        until = datetime.now(timezone.utc) + timedelta(hours=12)
    elif duration_key == "24h":
        until = datetime.now(timezone.utc) + timedelta(hours=24)
    elif duration_key == "3d":
        until = datetime.now(timezone.utc) + timedelta(days=3)
    elif duration_key == "7d":
        until = datetime.now(timezone.utc) + timedelta(days=7)
    elif duration_key == "perm":
        until = datetime(2100,1,1,tzinfo=timezone.utc)
    else:
        await cq.answer("Unknown duration", show_alert=True)
        return

    await db_set_ban(user_id, until)
    # notify in group
    text = ("Пользователь забанен" if lang=="ru" else "Користувач забанений")
    await cq.message.answer(f"🚫 {text} до {until.isoformat()}")
    # send notice to user
    try:
        if until.year >= 2099:
            ban_text = "🚫 Вы были забанены в опции предложения постов навсегда." if lang=="ru" else "🚫 Ви були забанені у опції пропозиції постів назавжди."
        else:
            secs = int((until - datetime.now(timezone.utc)).total_seconds())
            human = human_timedelta_seconds(secs, lang)
            ban_text = (f"🚫 Вы были забанены в опции предложения постов на {human}" if lang=="ru"
                        else f"🚫 Ви були забанені у опції пропозиції постів на {human}")
        await bot.send_message(chat_id=user_id, text=ban_text)
    except Exception:
        pass

    # schedule unban notification in background
    asyncio.create_task(schedule_unban_notification(user_id, until, lang))

    # edit group message back to moderation keyboard
    try:
        await cq.message.edit_reply_markup(reply_markup=group_moderation_kb(post_id, lang))
    except Exception:
        pass

async def schedule_unban_notification(user_id: int, until: datetime, lang: str):
    # If until is far in future (perm), skip scheduling
    if until.year >= 2099:
        return
    now = datetime.now(timezone.utc)
    delay = (until - now).total_seconds()
    if delay <= 0:
        # already expired
        await db_set_ban(user_id, None)
        try:
            await bot.send_message(user_id, "🔓 Срок Вашего бана в опции предложения постов был окончен! Вы снова можете предлагать свои посты." if lang=="ru" else "🔓 Термін Вашого бану в опції пропозиції постів закінчився! Ви знову можете пропонувати свої пости.")
        except Exception:
            pass
        return
    await asyncio.sleep(delay)
    # unban
    await db_set_ban(user_id, None)
    try:
        await bot.send_message(user_id, "🔓 Срок Вашего бана в опции предложения постов был окончен! Вы снова можете предлагать свои посты." if lang=="ru" else "🔓 Термін Вашого бану в опції пропозиції постів закінчився! Ви знову можете пропонувати свої пости.")
    except Exception:
        pass

# --- Reputation callbacks ---------------------------------------------------
@dp.callback_query(Text(startswith="rep:"))
async def cb_rep(c: CallbackQuery):
    _, amount_s, post_id_s = c.data.split(":")
    amount = int(amount_s)
    post_id = int(post_id_s)
    post = await db_get_post(post_id)
    if not post:
        await c.answer("Post not found", show_alert=True)
        return
    if post['status'] != 'accepted':
        await c.answer("Post not accepted", show_alert=True)
        return
    # add reputation
    new_rep = await db_add_reputation(post['user_id'], amount)
    # notify author in their bot chat, in reply to their original message if possible
    try:
        user_row = await db_get_user(post['user_id'])
        lang = user_row['lang'] if user_row else 'ru'
        if lang == "uk":
            text = f"🆙 Ваш пост був прийнятий! Ви заробили +{amount} репутації."
        else:
            text = f"🆙 Ваш пост был принят! Вы заработали +{amount} репутации."
        await bot.send_message(chat_id=post['user_id'], text=text, reply_to_message_id=post['user_message_id'])
    except Exception:
        pass
    # acknowledge to moderator
    await c.answer(f"+{amount}")

# --- Startup / Shutdown -----------------------------------------------------
async def on_startup():
    await db_connect()
    log.info("DB connected")
    if ADMIN_ID:
        try:
            await bot.send_message(int(ADMIN_ID), "Бот запущен")
        except Exception:
            pass

async def on_shutdown():
    if _pool:
        await _pool.close()
    try:
        await bot.session.close()
    except Exception:
        pass

if __name__ == "__main__":
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(on_startup())
    try:
        dp.run_polling(bot)
    finally:
        loop.run_until_complete(on_shutdown())
