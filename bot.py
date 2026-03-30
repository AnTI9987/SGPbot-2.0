# bot.py
import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID", "0"))
POST_CHAT_ID = int(os.getenv("POST_CHAT_ID", "0"))   # topic id for post submissions
SUP_CHAT_ID = int(os.getenv("SUP_CHAT_ID", "0"))     # topic id for support
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

if not BOT_TOKEN or not GROUP_ID or not POST_CHAT_ID or not SUP_CHAT_ID or not CHANNEL_ID:
    raise RuntimeError("Missing one of required env vars: BOT_TOKEN, GROUP_ID, POST_CHAT_ID, SUP_CHAT_ID, CHANNEL_ID")

router = Router()

TZ = ZoneInfo("Europe/Zaporozhye")

MAIN_TEXT = (
    "<b>👋 Добро пожаловать в бота «СГП»!</b>\n"
    "Здесь Вы можете предложить пост или обратиться в поддержку канала."
)

POST_PROMPT = "🫡 Предложите свой пост и мы проверим его в ближайшее время."
SUPPORT_PROMPT = "📥 Напишите ваше сообщение в поддержку."

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🖼️ Предложить пост"), KeyboardButton(text="📥 Поддержка")]],
    resize_keyboard=True,
)

CANCEL_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отменить")]],
    resize_keyboard=True,
)

CHANNEL_FOOTER = (
    '<a href="http://t.me/predlojka_gp_bot">Предложить пост</a> | '
    '<a href="https://t.me/comments_gp_plavni">Чат</a> | '
    '<a href="https://t.me/boost/channel_gp_plavni">Буст</a>'
)

BAN_OPTIONS = [
    ("12ч", 12 * 60 * 60),
    ("24ч", 24 * 60 * 60),
    ("3д", 3 * 24 * 60 * 60),
    ("1 нед", 7 * 24 * 60 * 60),
    ("2 нед", 14 * 24 * 60 * 60),
    ("1 мес", 30 * 24 * 60 * 60),
    ("3 мес", 90 * 24 * 60 * 60),
]

BAN_LABEL_BY_SECONDS = {seconds: label for label, seconds in BAN_OPTIONS}

# In-memory state
user_mode: Dict[int, str] = {}           # "post" | "support"
user_bans: Dict[int, int] = {}           # user_id -> unix timestamp
pending_posts: Dict[int, Dict[str, Any]] = {}  # moderation message_id -> record


def now_local() -> datetime:
    return datetime.now(TZ)


def mention_html(user_id: int, full_name: str, username: Optional[str] = None) -> str:
    safe_name = html.escape(full_name or "Пользователь")
    if username:
        safe_username = html.escape(username, quote=True)
        return f'<a href="https://t.me/{safe_username}">{safe_name}</a>'
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def admin_mention_html(user) -> str:
    return mention_html(user.id, user.full_name, user.username)


def user_mention_html(user) -> str:
    return mention_html(user.id, user.full_name, user.username)


def format_remaining(seconds_left: int) -> str:
    if seconds_left <= 0:
        return "0м"

    total_minutes = (seconds_left + 59) // 60
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)

    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes or not parts:
        parts.append(f"{minutes}м")
    return " ".join(parts)


def get_post_body(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    return "📎 Медиа"


def build_status_text(body: str, status_line: str) -> str:
    return f"{html.escape(body)}\n\n{status_line}"


def build_channel_text(body: str) -> str:
    footer = CHANNEL_FOOTER
    if body:
        text = html.escape(body) + "\n\n" + footer
    else:
        text = footer

    if len(text) > 4096:
        text = text[:4093] + "..."
    return text


def build_channel_caption(body: str) -> str:
    footer = CHANNEL_FOOTER
    if body:
        caption = html.escape(body) + "\n\n" + footer
    else:
        caption = footer

    if len(caption) > 1024:
        caption = caption[:1021] + "..."
    return caption


def main_menu_text() -> str:
    return MAIN_TEXT


def post_action_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data="post:accept"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data="post:reject"),
            ],
            [
                InlineKeyboardButton(text="🚫 Заблокировать", callback_data="post:ban_menu"),
            ],
        ]
    )


def ban_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for label, seconds in BAN_OPTIONS:
        row.append(InlineKeyboardButton(text=f"🚫 {label}", callback_data=f"ban:{seconds}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="post:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def is_group_admin(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(GROUP_ID, user_id)
        return member.status in {"administrator", "creator"}
    except Exception:
        return False


async def send_main_menu(message: Message) -> None:
    await message.answer(main_menu_text(), reply_markup=MAIN_KB)


async def send_post_prompt(message: Message) -> None:
    await message.answer(POST_PROMPT, reply_markup=CANCEL_KB)


async def send_support_prompt(message: Message) -> None:
    await message.answer(SUPPORT_PROMPT, reply_markup=CANCEL_KB)


async def publish_post_to_channel(bot: Bot, record: Dict[str, Any]) -> None:
    content_type = record["content_type"]
    body = record["body"]

    if content_type == "text":
        await bot.send_message(CHANNEL_ID, build_channel_text(body))
        return

    if content_type == "photo":
        caption = build_channel_caption(body)
        await bot.send_photo(
            CHANNEL_ID,
            photo=record["file_id"],
            caption=caption,
        )
        return

    if content_type == "video":
        caption = build_channel_caption(body)
        await bot.send_video(
            CHANNEL_ID,
            video=record["file_id"],
            caption=caption,
        )
        return

    # Fallback for any other content types
    await bot.send_message(CHANNEL_ID, build_channel_text(body))


async def edit_topic_message_with_status(
    bot: Bot,
    msg: Message,
    status_line: str,
    body: str,
) -> None:
    new_text = build_status_text(body, status_line)
    try:
        if msg.text is not None:
            await bot.edit_message_text(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                text=new_text,
                reply_markup=None,
            )
        else:
            await bot.edit_message_caption(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                caption=new_text,
                reply_markup=None,
            )
    except TelegramBadRequest:
        # If editing fails for any reason, try to at least remove the keyboard.
        with contextlib.suppress(Exception):
            await bot.edit_message_reply_markup(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                reply_markup=None,
            )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_mode.pop(message.from_user.id, None)
    await message.answer(main_menu_text(), reply_markup=MAIN_KB)


@router.message(F.text == "🖼️ Предложить пост")
async def enter_post_mode(message: Message) -> None:
    user_id = message.from_user.id
    banned_until = user_bans.get(user_id)

    if banned_until:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if banned_until > now_ts:
            remaining = banned_until - now_ts
            await message.answer(
                f"🚫 Вы были заблокированы в предложке. Вы будете разблокированы через {format_remaining(remaining)}"
            )
            return
        else:
            user_bans.pop(user_id, None)

    user_mode[user_id] = "post"
    await send_post_prompt(message)


@router.message(F.text == "📥 Поддержка")
async def enter_support_mode(message: Message) -> None:
    user_mode[message.from_user.id] = "support"
    await send_support_prompt(message)


@router.message(F.text == "❌ Отменить")
async def cancel_mode(message: Message) -> None:
    user_mode.pop(message.from_user.id, None)
    await message.answer(main_menu_text(), reply_markup=MAIN_KB)


@router.message()
async def handle_user_content(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    mode = user_mode.get(user_id)

    if mode not in {"post", "support"}:
        return

    # Post mode
    if mode == "post":
        banned_until = user_bans.get(user_id)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if banned_until and banned_until > now_ts:
            remaining = banned_until - now_ts
            await message.answer(
                f"🚫 Вы были заблокированы в предложке. Вы будете разблокированы через {format_remaining(remaining)}",
                reply_markup=MAIN_KB,
            )
            user_mode.pop(user_id, None)
            return

        author = user_mention_html(message.from_user)
        timestamp = now_local().strftime("%H:%M")
        author_line = f"От {author} в {timestamp}"

        author_msg = await bot.send_message(
            GROUP_ID,
            author_line,
            message_thread_id=POST_CHAT_ID,
            disable_web_page_preview=True,
        )

        copied = await bot.copy_message(
            chat_id=GROUP_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=POST_CHAT_ID,
        )
        copied_message_id = getattr(copied, "message_id", copied)

        pending_posts[copied_message_id] = {
            "user_id": user_id,
            "author_message_id": author_msg.message_id,
            "content_type": message.content_type,
            "body": get_post_body(message),
            "file_id": message.photo[-1].file_id if message.photo else None,
            "video_file_id": message.video.file_id if message.video else None,
            "source_chat_id": message.chat.id,
            "source_message_id": message.message_id,
        }

        try:
            await bot.edit_message_reply_markup(
                chat_id=GROUP_ID,
                message_id=copied_message_id,
                reply_markup=post_action_kb(),
            )
        except TelegramBadRequest:
            pass

        user_mode.pop(user_id, None)
        await message.answer("✅ Ваш пост принят на рассмотрение", reply_markup=MAIN_KB)
        return

    # Support mode
    if mode == "support":
        author = user_mention_html(message.from_user)
        timestamp = now_local().strftime("%H:%M")
        author_line = f"От {author} в {timestamp}"

        await bot.send_message(
            GROUP_ID,
            author_line,
            message_thread_id=SUP_CHAT_ID,
            disable_web_page_preview=True,
        )

        await bot.copy_message(
            chat_id=GROUP_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=SUP_CHAT_ID,
        )

        user_mode.pop(user_id, None)
        await message.answer("✅ Ваше обращение отправлено в поддержку", reply_markup=MAIN_KB)
        return


@router.callback_query(F.data == "post:accept")
async def cb_post_accept(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_group_admin(bot, callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    msg = callback.message
    if not msg:
        await callback.answer()
        return

    record = pending_posts.pop(msg.message_id, None)
    if not record:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    body = record["body"]
    admin_link = admin_mention_html(callback.from_user)
    status_line = f"✅ Принято: {admin_link}"

    try:
        await edit_topic_message_with_status(bot, msg, status_line, body)
    except Exception:
        pass

    await publish_post_to_channel(bot, record)
    await callback.answer("Принято")


@router.callback_query(F.data == "post:reject")
async def cb_post_reject(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_group_admin(bot, callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    msg = callback.message
    if not msg:
        await callback.answer()
        return

    record = pending_posts.pop(msg.message_id, None)
    if not record:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    body = record["body"]
    admin_link = admin_mention_html(callback.from_user)
    status_line = f"❌ Отклонено: {admin_link}"

    try:
        await edit_topic_message_with_status(bot, msg, status_line, body)
    except Exception:
        pass

    await callback.answer("Отклонено")


@router.callback_query(F.data == "post:ban_menu")
async def cb_post_ban_menu(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_group_admin(bot, callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    msg = callback.message
    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_posts:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    try:
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=ban_menu_kb(),
        )
    except TelegramBadRequest:
        pass

    await callback.answer()


@router.callback_query(F.data == "post:back")
async def cb_post_back(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_group_admin(bot, callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    msg = callback.message
    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_posts:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    try:
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=post_action_kb(),
        )
    except TelegramBadRequest:
        pass

    await callback.answer()


@router.callback_query(F.data.startswith("ban:"))
async def cb_post_ban_duration(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_group_admin(bot, callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    msg = callback.message
    if not msg:
        await callback.answer()
        return

    record = pending_posts.pop(msg.message_id, None)
    if not record:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    try:
        seconds = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Ошибка", show_alert=True)
        return

    user_id = record["user_id"]
    banned_until = int(datetime.now(timezone.utc).timestamp()) + seconds
    user_bans[user_id] = banned_until

    label = BAN_LABEL_BY_SECONDS.get(seconds, "время")
    admin_link = admin_mention_html(callback.from_user)
    status_line = f"🚫 Бан на {label}: {admin_link}"

    body = record["body"]
    try:
        await edit_topic_message_with_status(bot, msg, status_line, body)
    except Exception:
        pass

    await callback.answer(f"Пользователь заблокирован на {label}")


async def main() -> None:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
