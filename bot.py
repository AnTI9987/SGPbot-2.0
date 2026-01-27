import os
import asyncio
from datetime import datetime, timezone, timedelta
import html
import logging
from typing import Optional

from dotenv import load_dotenv
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode

# load env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PREDLOJKA_ID = int(os.getenv("PREDLOJKA_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

if not BOT_TOKEN or not DATABASE_URL or not PREDLOJKA_ID or not CHANNEL_ID:
    raise SystemExit("Please set BOT_TOKEN, DATABASE_URL, PREDLOJKA_ID and CHANNEL_ID in .env")

# timezone for timestamps
# Developer instruction: default timezone is Europe/Zaporozhye (UTC+2/UTC+3 DST)
import pytz
TZ = pytz.timezone("Europe/Zaporozhye")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(bot)

# ---------- Database helpers ----------
db_pool: Optional[asyncpg.pool.Pool] = None

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    lang TEXT DEFAULT 'ru',
    reputation INTEGER DEFAULT 0,
    in_proposal_mode BOOLEAN DEFAULT FALSE,
    last_proposal_message_id BIGINT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    user_message_id BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status TEXT DEFAULT 'pending', -- pending, accepted, rejected
    group_message_id BIGINT,      -- message id of appended message in group (where buttons are)
    forwarded_group_media_id BIGINT, -- message id of forwarded media/text in group
    channel_message_id BIGINT
);

CREATE TABLE IF NOT EXISTS bans (
    user_id BIGINT PRIMARY KEY,
    until_ts TIMESTAMP WITH TIME ZONE
);
"""

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    async with db_pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)
    logger.info("DB initialized")

async def get_user(user_id: int):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
        return row

async def ensure_user(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id
        )

async def set_lang(user_id: int, lang: str):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (user_id, lang) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET lang=$2", user_id, lang)

async def set_in_proposal(user_id: int, val: bool, last_msg_id: Optional[int] = None):
    async with db_pool.acquire() as conn:
        if last_msg_id:
            await conn.execute("UPDATE users SET in_proposal_mode=$2, last_proposal_message_id=$3 WHERE user_id=$1", user_id, val, last_msg_id)
        else:
            await conn.execute("UPDATE users SET in_proposal_mode=$2 WHERE user_id=$1", user_id, val)

async def create_proposal(user_id: int, user_message_id: int):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO proposals (user_id, user_message_id, created_at) VALUES ($1, $2, $3) RETURNING id",
            user_id, user_message_id, datetime.now(timezone.utc)
        )
        return row["id"]

async def set_proposal_group_message(proposal_id: int, group_msg_id: int, forwarded_group_media_id: Optional[int]=None):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE proposals SET group_message_id=$2, forwarded_group_media_id=$3 WHERE id=$1",
            proposal_id, group_msg_id, forwarded_group_media_id
        )

async def set_proposal_status(proposal_id: int, status: str):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE proposals SET status=$2 WHERE id=$1", proposal_id, status)

async def set_proposal_channel_message(proposal_id: int, chan_msg_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE proposals SET channel_message_id=$2 WHERE id=$1", proposal_id, chan_msg_id)

async def get_proposal_by_group_msg(group_msg_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM proposals WHERE group_message_id=$1", group_msg_id)

async def get_proposal(proposal_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM proposals WHERE id=$1", proposal_id)

async def add_reputation(user_id: int, delta: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET reputation = reputation + $2 WHERE user_id=$1", user_id, delta)

async def get_users_with_expired_bans():
    async with db_pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        rows = await conn.fetch("SELECT * FROM bans WHERE until_ts <= $1", now)
        return rows

async def set_ban(user_id: int, until_ts: Optional[datetime]):
    async with db_pool.acquire() as conn:
        if until_ts:
            await conn.execute("INSERT INTO bans (user_id, until_ts) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET until_ts=$2", user_id, until_ts)
        else:
            await conn.execute("DELETE FROM bans WHERE user_id=$1", user_id)

async def get_ban(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM bans WHERE user_id=$1", user_id)

async def get_user_reputation(user_id: int) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT reputation FROM users WHERE user_id=$1", user_id)
        return row["reputation"] if row else 0

# ---------- Utilities ----------
def format_user_link(user: types.User) -> str:
    # if username exists -> @username; else a link using tg://user?id=
    if user.username:
        return f"@{html.escape(user.username)}"
    else:
        name = html.escape(user.full_name)
        return f'<a href="tg://user?id={user.id}">{name}</a>'

def human_readable_date(dt: datetime) -> str:
    # format: HH:MM and "1 апреля"
    local = dt.astimezone(TZ)
    time_str = local.strftime("%H:%M")
    day = local.day
    month_name = local.strftime("%-d %B") if False else local.strftime("%B")  # we will format manually
    # russian/ukrainian month names require localization; provide simple mapping for russian and ukrainian
    months_ru = {
        1:"января",2:"февраля",3:"марта",4:"апреля",5:"мая",6:"июня",
        7:"июля",8:"августа",9:"сентября",10:"октября",11:"ноября",12:"декабря"
    }
    months_ua = {
        1:"січня",2:"лютого",3:"березня",4:"квітня",5:"травня",6:"червня",
        7:"липня",8:"серпня",9:"вересня",10:"жовтня",11:"листопада",12:"грудня"
    }
    # default to russian formatting; caller can replace if needed
    return time_str, f"{local.day} {months_ru[local.month]}"

def format_remaining(ts: datetime) -> str:
    # returns "0д, 0ч, 0м"
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = ts - now
    if delta.total_seconds() <= 0:
        return "0д, 0ч, 0м"
    days = delta.days
    hours = (delta.seconds // 3600)
    minutes = (delta.seconds % 3600) // 60
    return f"{days}д, {hours}ч, {minutes}м"

# ---------- Keyboards ----------
def lang_selection_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
        InlineKeyboardButton("🇺🇦 UK", callback_data="lang:uk")
    )
    return kb

def main_menu_kb(lang: str):
    # lang: 'ru' or 'uk'
    if lang == "uk":
        return types.ReplyKeyboardMarkup(resize_keyboard=True).add(
            types.KeyboardButton("🖼️ Запропонувати пост"),
            types.KeyboardButton("📩 Підтримка"),
            types.KeyboardButton("🗣️ Змінити мову"),
            types.KeyboardButton("📋 Політика конфіденційності")
        )
    else:
        return types.ReplyKeyboardMarkup(resize_keyboard=True).add(
            types.KeyboardButton("🖼️ Предложить пост"),
            types.KeyboardButton("📩 Поддержка"),
            types.KeyboardButton("🗣️ Сменить язык"),
            types.KeyboardButton("📋 Политика конфиденциальности")
        )

def cancel_kb(lang: str):
    txt = "❌ Скасувати" if lang == "uk" else "❌ Отменить"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(txt, callback_data="proposal:cancel"))
    return kb

def group_action_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("✅ Принять", callback_data="group:accept"),
        InlineKeyboardButton("❌ Отклонить", callback_data="group:reject"),
        InlineKeyboardButton("🚫 Бан пользователя", callback_data="group:ban")
    )
    return kb

def group_ban_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🚫 12 часов", callback_data="ban:12h"),
        InlineKeyboardButton("🚫 24 часов", callback_data="ban:24h"),
        InlineKeyboardButton("🚫 3 дня", callback_data="ban:3d"),
        InlineKeyboardButton("🚫 1 неделя", callback_data="ban:7d"),
        InlineKeyboardButton("🚫 Навсегда", callback_data="ban:perm"),
        InlineKeyboardButton("◀️ Назад", callback_data="ban:back")
    )
    return kb

def reputation_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🆙 +3 репутации", callback_data="rep:3"),
        InlineKeyboardButton("🆙 +2 репутации", callback_data="rep:2"),
        InlineKeyboardButton("🆙 +1 репутация", callback_data="rep:1"),
    )
    return kb

# ---------- Message texts ----------
WELCOME_RU = """<b>👋 Добро пожаловать в бота «Сущности Горишних Плавней»!</b>
Здесь Вы можете предложить пост или обратиться в поддержку канала.

<b>🆙 Ваша репутация</b>
{reputation}

Репутацию можно повысить предложив пост, который в следствии будет одобрен. Чем интереснее Ваш пост, тем больше репутации вы заработаете.
"""

WELCOME_UK = """<b>👋 Ласкаво просимо до бота «Сущности Горишних Плавней»!</b>
Тут ви можете запропонувати пост або звернутися до підтримки каналу.

<b>🆙 Ваша репутація</b>
{reputation}

Репутацію можна підвищити, запропонувавши пост, який в результаті буде схвалений. Чим цікавіший Ваш пост, тим більше репутації Ви заробите.
"""

PROMPT_RU = "🖼️ Пришлите свой пост. Это может быть видео, картинка или надпись. Помните: пост должен соответствовать нашей политике конфиденциальности."
PROMPT_UK = "🖼️ Надішліть свій пост. Це може бути відео, зображення або напис. Пам'ятайте: пост повинен відповідати нашій політиці конфіденційності."

CONFIRM_RU = "✅ Ваш пост отправлен на рассмотрение. Дождитесь, пока его проверят."
CONFIRM_UK = "✅ Ваш пост відправлений на розгляд. Зачекайте, поки його перевірять."

REJECTED_RU = "🙁 Ваш пост был отклонён."
REJECTED_UK = "🙁 Ваш пост був відхилений."

ACCEPTED_RU = "🆙 Ваш пост был принят! Вы заработали +{n} репутации."
ACCEPTED_UK = "🆙 Ваш пост був прийнятий! Ви заробили +{n} репутації."

BANNED_MSG_RU = "🚫 Вы были забанены в опции предложения постов на {time}."
BANNED_MSG_UK = "🚫 Ви були забанені у опції пропозиції постів на {time}."

UNBAN_NOTIFY_RU = "🔓 Срок Вашего бана в опции предложения постов был окончен! Вы снова можете предлагать свои посты."
UNBAN_NOTIFY_UK = "🔓 Термін Вашого бана в опції пропозиції постів закінчився! Ви знову можете пропонувати свої пости."

# ---------- Handlers ----------
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await ensure_user(message.from_user.id)
    # send language selection (default text in RU per user request)
    msg = await message.answer("🗣️ Выберите язык", reply_markup=lang_selection_kb())
    # store nothing else — language will be saved after choice

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("lang:"))
async def lang_choice_cb(query: types.CallbackQuery):
    lang = query.data.split(":", 1)[1]
    user = query.from_user
    await ensure_user(user.id)
    await set_lang(user.id, lang)
    # remove the language selection message
    try:
        await bot.delete_message(query.message.chat.id, query.message.message_id)
    except:
        pass

    # send welcome message in chosen language
    rep = await get_user_reputation(user.id)
    if lang == "uk":
        text = WELCOME_UK.format(reputation=rep)
    else:
        text = WELCOME_RU.format(reputation=rep)
    # send with reply keyboard
    kb = main_menu_kb(lang)
    sent = await bot.send_message(user.id, text, reply_markup=kb)
    await query.answer()

@dp.message_handler(lambda m: m.text in ["🗣️ Сменить язык", "🗣️ Змінити мову"])
async def change_language_request(message: types.Message):
    # open language selection; choose UI language according to user's current choice
    u = await get_user(message.from_user.id)
    lang_ui = u["lang"] if u else "ru"
    prompt = "🗣️ Выберите язык" if lang_ui == "ru" else "🗣️ Виберіть мову"
    await message.answer(prompt, reply_markup=lang_selection_kb())

@dp.message_handler(lambda m: m.text in ["🖼️ Предложить пост", "🖼️ Запропонувати пост"])
async def enter_proposal_mode(message: types.Message):
    await ensure_user(message.from_user.id)
    u = await get_user(message.from_user.id)
    lang = u["lang"] if u else "ru"
    # check ban
    ban = await get_ban(message.from_user.id)
    if ban:
        until = ban["until_ts"]
        if until and until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until and until > datetime.now(timezone.utc):
            # still banned
            rem = format_remaining(until)
            reply = f"🚫 Вы забанены в предложке. До разбана: {rem}" if lang=="ru" else f"🚫 Ви забанені в пропозиціях. До розблокування: {rem}"
            await message.answer(reply)
            return
        else:
            # ban expired; remove and notify user
            await set_ban(message.from_user.id, None)
            notify = UNBAN_NOTIFY_RU if lang=="ru" else UNBAN_NOTIFY_UK
            # send notification
            try:
                await bot.send_message(message.from_user.id, notify)
            except:
                pass

    # enter mode
    if lang == "uk":
        prompt = PROMPT_UK
    else:
        prompt = PROMPT_RU
    sent = await message.answer(prompt, reply_markup=cancel_kb(lang))
    await set_in_proposal(message.from_user.id, True, sent.message_id)

@dp.callback_query_handler(lambda c: c.data == "proposal:cancel")
async def proposal_cancel_cb(query: types.CallbackQuery):
    user = query.from_user
    u = await get_user(user.id)
    lang = u["lang"] if u else "ru"
    # turn off mode and delete the prompt message (the one with cancel button)
    await set_in_proposal(user.id, False)
    try:
        await bot.delete_message(query.message.chat.id, query.message.message_id)
    except:
        pass
    # return to main welcome message
    rep = await get_user_reputation(user.id)
    txt = WELCOME_UK.format(reputation=rep) if lang=="uk" else WELCOME_RU.format(reputation=rep)
    kb = main_menu_kb(lang)
    await bot.send_message(user.id, txt, reply_markup=kb)
    await query.answer()

@dp.message_handler(content_types=types.ContentTypes.ANY)
async def catch_all(message: types.Message):
    # This handler will:
    # - If user in proposal mode: treat their message as a post
    # - Else: ignore or possibly handle main menu buttons (support, privacy), but user asked to leave other buttons without functionality
    await ensure_user(message.from_user.id)
    u = await get_user(message.from_user.id)
    lang = u["lang"] if u else "ru"
    if u and u["in_proposal_mode"]:
        # accept this message as the post
        user = message.from_user
        # create proposal record
        proposal_id = await create_proposal(user.id, message.message_id)
        # reply to user with confirmation
        confirm = CONFIRM_UK if lang=="uk" else CONFIRM_RU
        await message.reply(confirm)
        # reset in_proposal_mode
        await set_in_proposal(user.id, False)
        # delete the prompt message in user's chat if exists
        if u["last_proposal_message_id"]:
            try:
                await bot.delete_message(user.id, u["last_proposal_message_id"])
            except:
                pass

        # forward the content to the PREDLOJKA_ID group as the user's content
        try:
            forwarded = await message.forward(chat_id=PREDLOJKA_ID)
            forwarded_media_id = forwarded.message_id
        except Exception as e:
            logger.exception("Failed to forward user content to group")
            forwarded = None
            forwarded_media_id = None

        # compose header text: "От <username_or_link> • HH:MM • <date like 1 апреля>"
        time_str, date_str = human_readable_date(datetime.now(timezone.utc))
        user_link = format_user_link(user)
        header = f"От {user_link} • {time_str} • {date_str}"
        # send header
        header_msg = await bot.send_message(PREDLOJKA_ID, header, parse_mode=ParseMode.HTML)
        # send appended message with three links and action buttons under it
        appended_text = (
            '<a href="https://t.me/predlojka_gp_bot">Предложить пост</a>  •  '
            '<a href="https://t.me/comments_gp_plavni">Чат</a>  •  '
            '<a href="https://t.me/boost/channel_gp_plavni">Буст</a>'
        )
        appended = await bot.send_message(PREDLOJKA_ID, appended_text, parse_mode=ParseMode.HTML, reply_markup=group_action_kb())
        # store proposal mapping: group message id (appended.message_id), forwarded_group_media_id
        await set_proposal_group_message(proposal_id, appended.message_id, forwarded_media_id)
        # small pause then send welcome back to user
        await asyncio.sleep(1)
        rep = await get_user_reputation(user.id)
        txt = WELCOME_UK.format(reputation=rep) if lang=="uk" else WELCOME_RU.format(reputation=rep)
        kb = main_menu_kb(lang)
        await bot.send_message(user.id, txt, reply_markup=kb)
        return

    # If not in proposal mode, we check other buttons (support, privacy) - leave unimplemented as requested
    # If user presses support or privacy text, we can respond with a placeholder
    if message.text in ["📩 Поддержка", "📩 Підтримка"]:
        await message.reply("Опция поддержки пока не реализована.")
    elif message.text in ["📋 Политика конфиденциальности", "📋 Політика конфіденційності"]:
        await message.reply("Политика конфиденциальности: (здесь будет текст политики).")
    # otherwise ignore

# ---------- Callback handlers for group action buttons ----------
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("group:"))
async def group_action_cb(query: types.CallbackQuery):
    action = query.data.split(":", 1)[1]
    group_msg = query.message  # this is the appended message in group
    # find proposal by group_message_id
    prop = await get_proposal_by_group_msg(group_msg.message_id)
    if not prop:
        await query.answer("Произошла ошибка: предложение не найдено.")
        return
    proposal_id = prop["id"]
    proposal = await get_proposal(proposal_id)
    author_id = proposal["user_id"]
    # fetch the forwarded media message id in group
    forwarded_group_media_id = proposal["forwarded_group_media_id"]
    if action == "accept":
        # forward the forwarded_group_media_id to channel (if exists), else forward the appended message text
        try:
            if forwarded_group_media_id:
                # forward the forwarded media message (that currently resides in group) to channel
                await bot.forward_message(CHANNEL_ID, PREDLOJKA_ID, forwarded_group_media_id)
                # mark status and store channel message id not available via forward (can't get new msg id easily) -> skip storing
            else:
                # nothing to forward; forward the appended text as fallback
                # forward the group message (appended) to channel
                await bot.forward_message(CHANNEL_ID, PREDLOJKA_ID, group_msg.message_id)
        except Exception as e:
            logger.exception("Error forwarding to channel")
        # change buttons under group appended message to reputation options
        try:
            await bot.edit_message_reply_markup(PREDLOJKA_ID, group_msg.message_id, reply_markup=reputation_kb())
        except Exception:
            pass
        # set proposal status to accepted
        await set_proposal_status(proposal_id, "accepted")
        await query.answer("Принято")
        return

    if action == "reject":
        # notify author in bot chat by replying to their original message
        try:
            await bot.send_message(author_id, REJECTED_RU if (await get_user(author_id))["lang"]=="ru" else REJECTED_UK, reply_to_message_id=proposal["user_message_id"])
        except Exception as e:
            logger.exception("Failed to notify author about rejection")
        await set_proposal_status(proposal_id, "rejected")
        # disable buttons
        try:
            await bot.edit_message_reply_markup(PREDLOJKA_ID, group_msg.message_id, reply_markup=None)
        except:
            pass
        await query.answer("Отклонено")
        return

    if action == "ban":
        # show ban durations
        try:
            await bot.edit_message_reply_markup(PREDLOJKA_ID, group_msg.message_id, reply_markup=group_ban_kb())
        except:
            pass
        await query.answer()
        return

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("ban:"))
async def ban_choice_cb(query: types.CallbackQuery):
    data = query.data.split(":",1)[1]
    group_msg = query.message
    prop = await get_proposal_by_group_msg(group_msg.message_id)
    if not prop:
        await query.answer("Ошибка: предложение не найдено")
        return
    proposal_id = prop["id"]
    author_id = prop["user_id"]

    if data == "back":
        # go back to accept/reject/ban
        try:
            await bot.edit_message_reply_markup(PREDLOJKA_ID, group_msg.message_id, reply_markup=group_action_kb())
        except:
            pass
        await query.answer()
        return

    # determine ban duration
    now = datetime.now(timezone.utc)
    if data == "12h":
        until = now + timedelta(hours=12)
        ban_text = "12 часов"
    elif data == "24h":
        until = now + timedelta(hours=24)
        ban_text = "24 часов"
    elif data == "3d":
        until = now + timedelta(days=3)
        ban_text = "3 дня"
    elif data == "7d":
        until = now + timedelta(days=7)
        ban_text = "1 неделя"
    elif data == "perm":
        # represent perm as far future date
        until = now + timedelta(days=3650)
        ban_text = "Навсегда"
    else:
        await query.answer()
        return

    # set ban in DB
    await set_ban(author_id, until)

    # notify the author in bot chat
    user_row = await get_user(author_id)
    lang = user_row["lang"] if user_row else "ru"
    text = BANNED_MSG_RU.format(time=ban_text) if lang=="ru" else BANNED_MSG_UK.format(time=ban_text)
    try:
        await bot.send_message(author_id, text)
    except Exception as e:
        logger.exception("Failed to send ban message to user")

    # return the group's appended message keyboard back to accept/reject/ban (or optionally keep as is)
    try:
        await bot.edit_message_reply_markup(PREDLOJKA_ID, group_msg.message_id, reply_markup=group_action_kb())
    except:
        pass

    await query.answer(f"Пользователь забанен на {ban_text}")

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("rep:"))
async def rep_choice_cb(query: types.CallbackQuery):
    # reputation awarding from group after acceptance
    val = int(query.data.split(":",1)[1])
    group_msg = query.message
    prop = await get_proposal_by_group_msg(group_msg.message_id)
    if not prop:
        await query.answer("Ошибка")
        return
    author_id = prop["user_id"]
    proposal_id = prop["id"]
    # award reputation
    await add_reputation(author_id, val)
    # notify author in bot chat replying to their original message
    user_row = await get_user(author_id)
    lang = user_row["lang"] if user_row else "ru"
    txt = ACCEPTED_RU.format(n=val) if lang=="ru" else ACCEPTED_UK.format(n=val)
    try:
        await bot.send_message(author_id, txt, reply_to_message_id=prop["user_message_id"])
    except Exception as e:
        logger.exception("Failed to notify author about rep")
    # disable reputation buttons after click
    try:
        await bot.edit_message_reply_markup(PREDLOJKA_ID, group_msg.message_id, reply_markup=None)
    except:
        pass
    await set_proposal_status(proposal_id, "accepted_and_rated")
    await query.answer("Репутация начислена")

# ---------- Background task: unban expired users and notify ----------
async def bans_watcher():
    await dp.wait_until_ready()  # aiogram2 helper to wait for startup
    while True:
        try:
            rows = await get_users_with_expired_bans()
            for r in rows:
                user_id = r["user_id"]
                # ban expired -> delete and notify user
                await set_ban(user_id, None)
                # notify user
                user_row = await get_user(user_id)
                lang = user_row["lang"] if user_row else "ru"
                notify = UNBAN_NOTIFY_RU if lang=="ru" else UNBAN_NOTIFY_UK
                try:
                    await bot.send_message(user_id, notify)
                except:
                    pass
            await asyncio.sleep(60)  # check every minute
        except Exception as e:
            logger.exception("Error in bans_watcher")
            await asyncio.sleep(60)

# ---------- Startup ----------
async def on_startup(dp):
    await init_db()
    loop = asyncio.get_event_loop()
    loop.create_task(bans_watcher())
    logger.info("Bot started")

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
