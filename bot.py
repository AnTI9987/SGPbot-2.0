import asyncio
import contextlib
import html
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, List
from zoneinfo import ZoneInfo

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# ENV
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

GROUP_ID = int(os.getenv("GROUP_ID", "0"))          # форум-группа
POST_CHAT_ID = int(os.getenv("POST_CHAT_ID", "0"))  # тема для постов
SUP_CHAT_ID = int(os.getenv("SUP_CHAT_ID", "0"))    # тема поддержки
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))      # канал

# Может быть:
# 1) обычным chat_id;
# 2) ID темы внутри GROUP_ID.
PRIKOL_CHAT_ID = int(os.getenv("PRIKOL_CHAT_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))


if (
    not BOT_TOKEN
    or not GROUP_ID
    or not POST_CHAT_ID
    or not SUP_CHAT_ID
    or not CHANNEL_ID
    or not PRIKOL_CHAT_ID
):
    raise RuntimeError(
        "Missing one of required env vars: "
        "BOT_TOKEN, GROUP_ID, POST_CHAT_ID, SUP_CHAT_ID, "
        "CHANNEL_ID, PRIKOL_CHAT_ID"
    )


router = Router()

TZ = ZoneInfo("Europe/Zaporozhye")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Сколько ждать после ПОСЛЕДНЕГО сообщения альбома.
ALBUM_COLLECT_DELAY = 2.0


# ============================================================
# TEXT
# ============================================================

MAIN_TEXT = (
    "<b>👋 Добро пожаловать в бота «СГП»!</b>\n"
    "Здесь Вы можете предложить пост или обратиться в поддержку канала."
)

POST_PROMPT = (
    "🖼️ Предложите свой пост для канала. "
    "Это может быть видео, картинка или надпись."
)

SUPPORT_PROMPT = (
    "📥 Пожалуйста, подробно опишите вашу проблему "
    "и вскоре Вы получите ответ в порядке очереди."
)


# ============================================================
# KEYBOARDS
# ============================================================

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🖼️ Предложить пост"),
            KeyboardButton(text="📥 Поддержка"),
        ]
    ],
    resize_keyboard=True,
)

CANCEL_KB = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="❌ Отменить")
        ]
    ],
    resize_keyboard=True,
)


# ============================================================
# CHANNEL
# ============================================================

CHANNEL_FOOTER = (
    '<a href="http://t.me/predlojka_gp_bot">Предложить пост</a> | '
    '<a href="https://t.me/comments_gp_plavni">Чат</a> | '
    '<a href="https://t.me/boost/channel_gp_plavni">Буст</a>'
)


# ============================================================
# BAN
# ============================================================

BAN_OPTIONS = [
    ("12ч", 12 * 60 * 60),
    ("24ч", 24 * 60 * 60),
    ("3д", 3 * 24 * 60 * 60),
    ("1 нед", 7 * 24 * 60 * 60),
    ("2 нед", 14 * 24 * 60 * 60),
    ("1 мес", 30 * 24 * 60 * 60),
    ("3 мес", 90 * 24 * 60 * 60),
]

BAN_LABEL_BY_SECONDS = {
    seconds: label
    for label, seconds in BAN_OPTIONS
}


# ============================================================
# IN-MEMORY STATE
# ============================================================

user_mode: Dict[int, str] = {}
user_bans: Dict[int, int] = {}
support_banned_users: set[int] = set()

# ID сообщения с кнопками -> данные поста
pending_posts: Dict[int, Dict[str, Any]] = {}

# ID сообщения поддержки в теме -> ID пользователя
support_message_to_user: Dict[int, int] = {}

# (chat_id, media_group_id) -> buffer
media_group_buffers: Dict[
    Tuple[int, str],
    Dict[str, Any]
] = {}


# ============================================================
# PRIKOL DESTINATION
# ============================================================

# True = PRIKOL_CHAT_ID оказался ID темы внутри GROUP_ID.
# False = PRIKOL_CHAT_ID является обычным chat_id.
prikol_is_topic = False


async def detect_prikol_destination(bot: Bot) -> None:
    """
    Определяет, что именно находится в PRIKOL_CHAT_ID.

    Сначала пробуем использовать его как настоящий chat_id.

    Если Telegram отвечает "chat not found", считаем его ID темы
    внутри GROUP_ID.

    Таким образом поддерживаются оба варианта.
    """

    global prikol_is_topic

    try:
        chat = await bot.get_chat(
            PRIKOL_CHAT_ID
        )

        prikol_is_topic = False

        logger.info(
            "PRIKOL_CHAT_ID=%s detected as chat_id. "
            "type=%s title=%s",
            PRIKOL_CHAT_ID,
            chat.type,
            getattr(chat, "title", None),
        )

        return

    except TelegramBadRequest as e:
        logger.warning(
            "PRIKOL_CHAT_ID=%s is not a direct chat_id. "
            "Telegram: %s. "
            "Trying it as topic_id inside GROUP_ID=%s.",
            PRIKOL_CHAT_ID,
            e,
            GROUP_ID,
        )

    except Exception:
        logger.exception(
            "Unexpected error while checking PRIKOL_CHAT_ID=%s",
            PRIKOL_CHAT_ID,
        )

    # Проверяем, что указанный topic действительно существует
    # внутри GROUP_ID.
    try:
        await bot.get_forum_topic(
            chat_id=GROUP_ID,
            message_thread_id=PRIKOL_CHAT_ID,
        )

        prikol_is_topic = True

        logger.info(
            "PRIKOL_CHAT_ID=%s detected as topic_id "
            "inside GROUP_ID=%s.",
            PRIKOL_CHAT_ID,
            GROUP_ID,
        )

    except Exception as e:
        prikol_is_topic = False

        logger.error(
            "PRIKOL destination could not be validated. "
            "PRIKOL_CHAT_ID=%s. "
            "As direct chat it failed, and as topic inside "
            "GROUP_ID=%s it also failed: %s",
            PRIKOL_CHAT_ID,
            GROUP_ID,
            e,
        )


# ============================================================
# HELPERS
# ============================================================

def now_local() -> datetime:
    return datetime.now(TZ)


def extract_message_id(result: Any) -> int:
    return int(
        getattr(
            result,
            "message_id",
            result
        )
    )


def mention_html(
    user_id: int,
    full_name: str,
    username: Optional[str] = None,
) -> str:

    safe_name = html.escape(
        full_name or "Пользователь"
    )

    if username:
        safe_username = html.escape(
            username,
            quote=True,
        )

        return (
            f'<a href="https://t.me/{safe_username}">'
            f'{safe_name}</a>'
        )

    return (
        f'<a href="tg://user?id={user_id}">'
        f'{safe_name}</a>'
    )


def user_mention_html(user) -> str:
    return mention_html(
        user.id,
        user.full_name,
        user.username,
    )


def admin_mention_html(user) -> str:
    return mention_html(
        user.id,
        user.full_name,
        user.username,
    )


def format_remaining(
    seconds_left: int
) -> str:

    if seconds_left <= 0:
        return "0м"

    total_minutes = (
        seconds_left + 59
    ) // 60

    days, rem_minutes = divmod(
        total_minutes,
        24 * 60,
    )

    hours, minutes = divmod(
        rem_minutes,
        60,
    )

    parts = []

    if days:
        parts.append(
            f"{days}д"
        )

    if hours:
        parts.append(
            f"{hours}ч"
        )

    if minutes or not parts:
        parts.append(
            f"{minutes}м"
        )

    return " ".join(parts)


def get_post_body(
    message: Message
) -> str:

    if message.text:
        return message.text

    if message.caption:
        return message.caption

    return ""


def get_message_kind(
    message: Message
) -> str:

    if message.text:
        return "text"

    if message.photo:
        return "photo"

    if message.video:
        return "video"

    return "other"


def build_status_text(
    body: str,
    status_line: str,
    include_body: bool = True,
) -> str:

    body = (
        body or ""
    ).strip()

    if include_body and body:
        return (
            f"{html.escape(body)}\n\n"
            f"{status_line}"
        )

    return status_line


def build_channel_text(
    body: str
) -> str:

    body = (
        body or ""
    ).strip()

    parts = []

    if body:
        parts.append(
            html.escape(body)
        )

    parts.append(
        CHANNEL_FOOTER
    )

    text = "\n\n".join(parts)

    if len(text) <= 4096:
        return text

    return text[:4093] + "..."


def build_channel_caption(
    body: str
) -> str:

    body = (
        body or ""
    ).strip()

    parts = []

    if body:
        parts.append(
            html.escape(body)
        )

    parts.append(
        CHANNEL_FOOTER
    )

    caption = "\n\n".join(parts)

    if len(caption) <= 1024:
        return caption

    return caption[:1021] + "..."


# ============================================================
# MODERATION KEYBOARDS
# ============================================================

def post_action_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data="post:accept",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data="post:reject",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Заблокировать",
                    callback_data="post:ban_menu",
                )
            ],
        ]
    )


def ban_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    row = []

    for label, seconds in BAN_OPTIONS:
        row.append(
            InlineKeyboardButton(
                text=f"🚫 {label}",
                callback_data=f"ban:{seconds}",
            )
        )

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="post:back",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# MEDIA BUILDERS
# ============================================================

def build_media_group_for_channel(
    media_items: List[Dict[str, str]],
    body: str,
):

    caption = build_channel_caption(
        body
    )

    result = []

    for index, item in enumerate(
        media_items
    ):
        media_type = item["type"]
        file_id = item["file_id"]

        if media_type == "photo":

            result.append(
                InputMediaPhoto(
                    media=file_id,
                    caption=(
                        caption
                        if index == 0
                        else None
                    ),
                    parse_mode=(
                        ParseMode.HTML
                        if index == 0
                        else None
                    ),
                )
            )

        elif media_type == "video":

            result.append(
                InputMediaVideo(
                    media=file_id,
                    caption=(
                        caption
                        if index == 0
                        else None
                    ),
                    parse_mode=(
                        ParseMode.HTML
                        if index == 0
                        else None
                    ),
                )
            )

    return result


def build_media_group_for_topic(
    bundle_messages: List[Message],
):

    result = []
    body = ""

    for message in bundle_messages:
        if message.caption:
            body = message.caption
            break

    escaped_caption = (
        html.escape(body)
        if body
        else None
    )

    for index, message in enumerate(
        bundle_messages
    ):

        caption = (
            escaped_caption
            if index == 0
            else None
        )

        parse_mode = (
            ParseMode.HTML
            if caption
            else None
        )

        if message.photo:

            result.append(
                InputMediaPhoto(
                    media=message.photo[-1].file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                )
            )

        elif message.video:

            result.append(
                InputMediaVideo(
                    media=message.video.file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                )
            )

    return result, body


def build_media_group_for_prikol(
    bundle_messages: List[Message],
):

    result = []
    body = ""

    for message in bundle_messages:
        if message.caption:
            body = message.caption
            break

    escaped_caption = (
        html.escape(body)
        if body
        else None
    )

    for index, message in enumerate(
        bundle_messages
    ):

        caption = (
            escaped_caption
            if index == 0
            else None
        )

        parse_mode = (
            ParseMode.HTML
            if caption
            else None
        )

        if message.photo:

            result.append(
                InputMediaPhoto(
                    media=message.photo[-1].file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                )
            )

        elif message.video:

            result.append(
                InputMediaVideo(
                    media=message.video.file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                )
            )

    return result


# ============================================================
# BASIC UI
# ============================================================

async def send_main_menu(
    message: Message
) -> None:

    await message.answer(
        MAIN_TEXT,
        reply_markup=MAIN_KB,
    )


async def send_post_prompt(
    message: Message
) -> None:

    await message.answer(
        POST_PROMPT,
        reply_markup=CANCEL_KB,
    )


async def send_support_prompt(
    message: Message
) -> None:

    await message.answer(
        SUPPORT_PROMPT,
        reply_markup=CANCEL_KB,
    )


# ============================================================
# WEB SERVER
# ============================================================

async def start_web_server() -> web.AppRunner:

    app = web.Application()

    async def healthcheck(
        request: web.Request
    ) -> web.Response:

        return web.Response(
            text="OK"
        )

    app.router.add_get(
        "/",
        healthcheck
    )

    app.router.add_get(
        "/health",
        healthcheck
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT,
    )

    await site.start()

    logger.info(
        "Web server started on 0.0.0.0:%s",
        PORT
    )

    return runner


# ============================================================
# POST -> TOPIC: SINGLE
# ============================================================

async def send_submission_single_to_topic(
    bot: Bot,
    topic_id: int,
    user,
    source_message: Message,
):

    author_line = (
        f"От {user_mention_html(user)} "
        f"в {now_local().strftime('%H:%M')}"
    )

    header_msg = await bot.send_message(
        chat_id=GROUP_ID,
        message_thread_id=topic_id,
        text=author_line,
        link_preview_options=NO_PREVIEW,
    )

    try:

        kind = get_message_kind(
            source_message
        )

        if kind == "text":

            sent = await bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                text=html.escape(
                    source_message.text or ""
                ),
                link_preview_options=NO_PREVIEW,
            )

            sent_id = extract_message_id(
                sent
            )

            return sent_id, {
                "kind": "single",
                "content_type": "text",
                "body": get_post_body(
                    source_message
                ),
                "topic_message_ids": [
                    sent_id
                ],
                "source_message_ids": [
                    source_message.message_id
                ],
                "user_id": user.id,
            }

        if kind in {
            "photo",
            "video"
        }:

            copied = await bot.copy_message(
                chat_id=GROUP_ID,
                from_chat_id=source_message.chat.id,
                message_id=source_message.message_id,
                message_thread_id=topic_id,
            )

            copied_id = extract_message_id(
                copied
            )

            media_type = (
                "photo"
                if source_message.photo
                else "video"
            )

            file_id = (
                source_message.photo[-1].file_id
                if source_message.photo
                else source_message.video.file_id
            )

            return copied_id, {
                "kind": "single",
                "content_type": media_type,
                "body": get_post_body(
                    source_message
                ),
                "file_id": file_id,
                "topic_message_ids": [
                    copied_id
                ],
                "source_message_ids": [
                    source_message.message_id
                ],
                "user_id": user.id,
            }

        raise ValueError(
            "Unsupported message type"
        )

    except Exception:

        with contextlib.suppress(Exception):
            await bot.delete_message(
                chat_id=GROUP_ID,
                message_id=header_msg.message_id
            )

        raise


# ============================================================
# POST -> TOPIC: ALBUM
# ============================================================

async def send_submission_album_to_topic(
    bot: Bot,
    topic_id: int,
    user,
    bundle_messages: List[Message],
):

    bundle_messages = sorted(
        bundle_messages,
        key=lambda m: m.message_id
    )

    # Дубликаты исключаем.
    unique_messages = []
    seen_ids = set()

    for message in bundle_messages:

        if message.message_id in seen_ids:
            continue

        seen_ids.add(
            message.message_id
        )

        unique_messages.append(
            message
        )

    bundle_messages = unique_messages

    if len(bundle_messages) < 2:
        return await send_submission_single_to_topic(
            bot=bot,
            topic_id=topic_id,
            user=user,
            source_message=bundle_messages[0],
        )

    # Telegram ограничивает media group 10 элементами.
    if len(bundle_messages) > 10:
        bundle_messages = bundle_messages[:10]

    author_line = (
        f"От {user_mention_html(user)} "
        f"в {now_local().strftime('%H:%M')}"
    )

    header_msg = await bot.send_message(
        chat_id=GROUP_ID,
        message_thread_id=topic_id,
        text=author_line,
        link_preview_options=NO_PREVIEW,
    )

    try:

        media, body = build_media_group_for_topic(
            bundle_messages
        )

        if len(media) < 2:
            raise ValueError(
                "Album contains less than 2 supported media messages"
            )

        # ----------------------------------------------------
        # 1. Отправляем альбом.
        # ----------------------------------------------------

        sent_messages = await bot.send_media_group(
            chat_id=GROUP_ID,
            message_thread_id=topic_id,
            media=media,
        )

        if len(sent_messages) < 2:
            raise ValueError(
                "Telegram returned less than 2 messages for album"
            )

        topic_message_ids = [
            extract_message_id(
                message
            )
            for message in sent_messages
        ]

        # ----------------------------------------------------
        # 2. Создаём ОТДЕЛЬНОЕ сообщение с кнопками.
        #
        # БЕЗ reply_parameters.
        # Это обычное сообщение в той же теме.
        # ----------------------------------------------------

        control_message = await bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=topic_id,
            text="ㅤ",
            reply_markup=post_action_kb(),
            link_preview_options=NO_PREVIEW,
        )

        control_message_id = extract_message_id(
            control_message
        )

        # ----------------------------------------------------
        # 3. Формируем данные модерации.
        # ----------------------------------------------------

        media_items = []

        for message in bundle_messages:

            if message.photo:

                media_items.append(
                    {
                        "type": "photo",
                        "file_id": message.photo[-1].file_id,
                    }
                )

            elif message.video:

                media_items.append(
                    {
                        "type": "video",
                        "file_id": message.video.file_id,
                    }
                )

        record = {
            "kind": "album",
            "content_type": "album",
            "body": body,
            "media_items": media_items,
            "topic_message_ids": topic_message_ids,
            "source_message_ids": [
                message.message_id
                for message in bundle_messages
            ],
            "user_id": user.id,

            # ВАЖНО:
            # callback привязан к ЭТОМУ сообщению.
            "control_message_id": control_message_id,
        }

        return (
            control_message_id,
            record
        )

    except Exception:

        logger.exception(
            "Failed to send album to topic. "
            "GROUP_ID=%s POST_CHAT_ID=%s",
            GROUP_ID,
            topic_id,
        )

        with contextlib.suppress(Exception):
            await bot.delete_message(
                chat_id=GROUP_ID,
                message_id=header_msg.message_id
            )

        raise


async def send_submission_to_topic(
    bot: Bot,
    topic_id: int,
    user,
    source_message: Message,
    bundle_messages: Optional[List[Message]] = None,
):

    if (
        bundle_messages
        and len(bundle_messages) > 1
    ):

        return await send_submission_album_to_topic(
            bot=bot,
            topic_id=topic_id,
            user=user,
            bundle_messages=bundle_messages,
        )

    return await send_submission_single_to_topic(
        bot=bot,
        topic_id=topic_id,
        user=user,
        source_message=source_message,
    )


# ============================================================
# SUPPORT
# ============================================================

async def send_support_submission_to_topic(
    bot: Bot,
    topic_id: int,
    user,
    source_message: Message,
    bundle_messages: Optional[List[Message]] = None,
) -> List[int]:

    author_line = (
        f"От {user_mention_html(user)} "
        f"в {now_local().strftime('%H:%M')}"
    )

    header_msg = await bot.send_message(
        chat_id=GROUP_ID,
        message_thread_id=topic_id,
        text=author_line,
        link_preview_options=NO_PREVIEW,
    )

    try:

        copied_ids = []

        if (
            bundle_messages
            and len(bundle_messages) > 1
        ):

            bundle_messages = sorted(
                bundle_messages,
                key=lambda m: m.message_id
            )

            for message in bundle_messages:

                forwarded = await bot.forward_message(
                    chat_id=GROUP_ID,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=topic_id,
                )

                copied_ids.append(
                    extract_message_id(
                        forwarded
                    )
                )

        else:

            forwarded = await bot.forward_message(
                chat_id=GROUP_ID,
                from_chat_id=source_message.chat.id,
                message_id=source_message.message_id,
                message_thread_id=topic_id,
            )

            copied_ids.append(
                extract_message_id(
                    forwarded
                )
            )

        return copied_ids

    except Exception:

        with contextlib.suppress(Exception):
            await bot.delete_message(
                chat_id=GROUP_ID,
                message_id=header_msg.message_id
            )

        raise


# ============================================================
# PRIKOL
# ============================================================

async def prikol_copy_message(
    bot: Bot,
    message: Message,
) -> None:
    """
    Молча отправляет сообщение в PRIKOL.

    Если PRIKOL_CHAT_ID оказался обычным chat_id:
        copy_message(chat_id=PRIKOL_CHAT_ID)

    Если оказался topic_id:
        copy_message(
            chat_id=GROUP_ID,
            message_thread_id=PRIKOL_CHAT_ID
        )
    """

    try:

        if prikol_is_topic:

            await bot.copy_message(
                chat_id=GROUP_ID,
                message_thread_id=PRIKOL_CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )

        else:

            await bot.copy_message(
                chat_id=PRIKOL_CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )

    except TelegramBadRequest as e:

        logger.error(
            "Failed to silently copy message. "
            "PRIKOL_CHAT_ID=%s, prikol_is_topic=%s, "
            "GROUP_ID=%s, source_chat_id=%s, "
            "message_id=%s, Telegram error: %s",
            PRIKOL_CHAT_ID,
            prikol_is_topic,
            GROUP_ID,
            message.chat.id,
            message.message_id,
            e,
        )

    except Exception:

        logger.exception(
            "Unexpected error while silently copying "
            "message %s",
            message.message_id
        )


async def prikol_send_album(
    bot: Bot,
    bundle_messages: List[Message],
) -> None:

    try:

        bundle_messages = sorted(
            bundle_messages,
            key=lambda m: m.message_id
        )

        unique_messages = []
        seen_ids = set()

        for message in bundle_messages:

            if message.message_id in seen_ids:
                continue

            seen_ids.add(
                message.message_id
            )

            unique_messages.append(
                message
            )

        bundle_messages = unique_messages

        if len(bundle_messages) < 2:

            if bundle_messages:
                await prikol_copy_message(
                    bot,
                    bundle_messages[0]
                )

            return

        if len(bundle_messages) > 10:
            bundle_messages = bundle_messages[:10]

        media = build_media_group_for_prikol(
            bundle_messages
        )

        if len(media) < 2:
            return

        if prikol_is_topic:

            await bot.send_media_group(
                chat_id=GROUP_ID,
                message_thread_id=PRIKOL_CHAT_ID,
                media=media,
            )

        else:

            await bot.send_media_group(
                chat_id=PRIKOL_CHAT_ID,
                media=media,
            )

    except TelegramBadRequest as e:

        logger.error(
            "Failed to silently copy album. "
            "PRIKOL_CHAT_ID=%s, prikol_is_topic=%s, "
            "GROUP_ID=%s, source_message_ids=%s, "
            "Telegram error: %s",
            PRIKOL_CHAT_ID,
            prikol_is_topic,
            GROUP_ID,
            [
                message.message_id
                for message in bundle_messages
            ],
            e,
        )

    except Exception:

        logger.exception(
            "Unexpected error while silently copying album"
        )


# ============================================================
# MODERATION MESSAGE EDIT
# ============================================================

async def edit_moderation_message(
    bot: Bot,
    msg: Message,
    status_line: str,
    body: str,
    is_album: bool,
) -> None:
    """
    Меняет ТОЛЬКО управляющее сообщение.

    Для album это обычное текстовое сообщение.
    Для single это может быть текст/фото/видео.
    """

    if is_album:

        await bot.edit_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=status_line,
            reply_markup=None,
            link_preview_options=NO_PREVIEW,
        )

        return

    new_text = build_status_text(
        body,
        status_line,
        include_body=True,
    )

    if msg.text is not None:

        await bot.edit_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=new_text,
            reply_markup=None,
            link_preview_options=NO_PREVIEW,
        )

        return

    await bot.edit_message_caption(
        chat_id=msg.chat.id,
        message_id=msg.message_id,
        caption=new_text,
        reply_markup=None,
    )


# ============================================================
# PUBLISH
# ============================================================

async def publish_post_to_channel(
    bot: Bot,
    record: Dict[str, Any],
) -> None:

    content_type = record["content_type"]
    body = record["body"]

    if content_type == "text":

        await bot.send_message(
            CHANNEL_ID,
            build_channel_text(body),
            link_preview_options=NO_PREVIEW,
        )

        return

    if content_type == "photo":

        await bot.send_photo(
            CHANNEL_ID,
            photo=record["file_id"],
            caption=build_channel_caption(body),
        )

        return

    if content_type == "video":

        await bot.send_video(
            CHANNEL_ID,
            video=record["file_id"],
            caption=build_channel_caption(body),
        )

        return

    if content_type == "album":

        media = build_media_group_for_channel(
            record["media_items"],
            body,
        )

        if media:

            await bot.send_media_group(
                CHANNEL_ID,
                media=media,
            )

        return

    await bot.send_message(
        CHANNEL_ID,
        build_channel_text(body),
        link_preview_options=NO_PREVIEW,
    )


# ============================================================
# PROCESS SUBMISSION
# ============================================================

async def process_submission_bundle(
    primary_message: Message,
    bot: Bot,
    bundle_messages: Optional[List[Message]] = None,
) -> None:

    user_id = primary_message.from_user.id
    mode = user_mode.get(
        user_id
    )

    if mode not in {
        "post",
        "support"
    }:
        return

    # ========================================================
    # POST
    # ========================================================

    if mode == "post":

        banned_until = user_bans.get(
            user_id
        )

        now_ts = int(
            datetime.now(timezone.utc).timestamp()
        )

        if (
            banned_until
            and banned_until > now_ts
        ):

            remaining = (
                banned_until - now_ts
            )

            user_mode.pop(
                user_id,
                None
            )

            await primary_message.answer(
                "🚫 Вы были заблокированы в предложке. "
                f"Вы будете разблокированы через "
                f"{format_remaining(remaining)}",
                reply_markup=MAIN_KB,
            )

            return

        try:

            moderation_message_id, record = (
                await send_submission_to_topic(
                    bot=bot,
                    topic_id=POST_CHAT_ID,
                    user=primary_message.from_user,
                    source_message=primary_message,
                    bundle_messages=bundle_messages,
                )
            )

        except TelegramBadRequest as e:

            logger.error(
                "Failed to send post. "
                "GROUP_ID=%s, POST_CHAT_ID=%s, "
                "Telegram error: %s",
                GROUP_ID,
                POST_CHAT_ID,
                e,
            )

            await primary_message.answer(
                "❌ Не удалось отправить пост в тему."
            )

            return

        except Exception:

            logger.exception(
                "Unexpected error while sending post"
            )

            await primary_message.answer(
                "❌ Произошла ошибка при отправке поста.",
                reply_markup=MAIN_KB,
            )

            return

        # Сначала сохраняем запись.
        pending_posts[
            moderation_message_id
        ] = record

        # ----------------------------------------------------
        # ОЧЕНЬ ВАЖНО:
        #
        # SINGLE:
        # кнопки добавляем к самому посту.
        #
        # ALBUM:
        # кнопки уже находятся на control-message,
        # поэтому ничего дополнительно редактировать не нужно.
        # ----------------------------------------------------

        if record.get("kind") == "single":

            try:

                await bot.edit_message_reply_markup(
                    chat_id=GROUP_ID,
                    message_id=moderation_message_id,
                    reply_markup=post_action_kb(),
                )

            except TelegramBadRequest as e:

                pending_posts.pop(
                    moderation_message_id,
                    None
                )

                logger.error(
                    "Failed to add buttons to single post. "
                    "message_id=%s, Telegram error: %s",
                    moderation_message_id,
                    e,
                )

                await primary_message.answer(
                    "❌ Не удалось добавить кнопки управления "
                    "к предложенному посту.",
                    reply_markup=MAIN_KB,
                )

                return

            except Exception:

                pending_posts.pop(
                    moderation_message_id,
                    None
                )

                logger.exception(
                    "Unexpected error while adding buttons "
                    "to single post"
                )

                await primary_message.answer(
                    "❌ Не удалось добавить кнопки управления "
                    "к предложенному посту.",
                    reply_markup=MAIN_KB,
                )

                return

        # Альбом уже содержит отдельный control-message.
        user_mode.pop(
            user_id,
            None
        )

        await primary_message.answer(
            "<b>✅ Ваш пост принят на рассмотрение</b>\n\n"
            "Хотите повторно отправить пост или обратиться "
            "в поддержку канала? Выберите необходимую опцию "
            "при помощи кнопок ниже.",
            reply_markup=MAIN_KB,
        )

        return

    # ========================================================
    # SUPPORT
    # ========================================================

    if mode == "support":

        if user_id in support_banned_users:

            await primary_message.answer(
                "🚫 Вы заблокированы в поддержке."
            )

            return

        try:

            copied_ids = (
                await send_support_submission_to_topic(
                    bot=bot,
                    topic_id=SUP_CHAT_ID,
                    user=primary_message.from_user,
                    source_message=primary_message,
                    bundle_messages=bundle_messages,
                )
            )

        except TelegramBadRequest as e:

            logger.error(
                "Failed to send support. "
                "GROUP_ID=%s, SUP_CHAT_ID=%s, "
                "Telegram error: %s",
                GROUP_ID,
                SUP_CHAT_ID,
                e,
            )

            await primary_message.answer(
                "❌ Не удалось отправить сообщение в поддержку."
            )

            return

        except Exception:

            logger.exception(
                "Unexpected error while sending support"
            )

            await primary_message.answer(
                "❌ Произошла ошибка при отправке сообщения.",
                reply_markup=CANCEL_KB,
            )

            return

        for copied_id in copied_ids:

            support_message_to_user[
                copied_id
            ] = user_id

        await primary_message.answer(
            "✅ Ваше обращение было передано в поддержку."
        )

        return


# ============================================================
# MEDIA GROUP COLLECTOR
# ============================================================

async def process_media_group_after_delay(
    key: Tuple[int, str],
    bot: Bot,
) -> None:

    try:

        await asyncio.sleep(
            ALBUM_COLLECT_DELAY
        )

        buffer = media_group_buffers.get(
            key
        )

        if not buffer:
            return

        messages = sorted(
            buffer["messages"],
            key=lambda m: m.message_id
        )

        if not messages:
            return

        media_group_buffers.pop(
            key,
            None
        )

        primary_message = messages[0]

        mode = user_mode.get(
            primary_message.from_user.id
        )

        # Нет активного режима.
        if mode not in {
            "post",
            "support"
        }:

            await prikol_send_album(
                bot,
                messages
            )

            return

        await process_submission_bundle(
            primary_message,
            bot,
            bundle_messages=messages,
        )

    except asyncio.CancelledError:
        raise

    except Exception:

        logger.exception(
            "Unexpected error while processing media group %s",
            key
        )


# ============================================================
# START
# ============================================================

@router.message(
    CommandStart()
)
async def cmd_start(
    message: Message
) -> None:

    user_mode.pop(
        message.from_user.id,
        None
    )

    await send_main_menu(
        message
    )


# ============================================================
# POST MODE
# ============================================================

@router.message(
    F.text == "🖼️ Предложить пост"
)
async def enter_post_mode(
    message: Message
) -> None:

    user_id = message.from_user.id

    banned_until = user_bans.get(
        user_id
    )

    if banned_until:

        now_ts = int(
            datetime.now(timezone.utc).timestamp()
        )

        if banned_until > now_ts:

            remaining = (
                banned_until - now_ts
            )

            await message.answer(
                "🚫 Вы были заблокированы в предложке. "
                f"Вы будете разблокированы через "
                f"{format_remaining(remaining)}"
            )

            return

        user_bans.pop(
            user_id,
            None
        )

    user_mode[
        user_id
    ] = "post"

    await send_post_prompt(
        message
    )


# ============================================================
# SUPPORT MODE
# ============================================================

@router.message(
    F.text == "📥 Поддержка"
)
async def enter_support_mode(
    message: Message
) -> None:

    user_mode[
        message.from_user.id
    ] = "support"

    await send_support_prompt(
        message
    )


# ============================================================
# CANCEL
# ============================================================

@router.message(
    F.text == "❌ Отменить"
)
async def cancel_mode(
    message: Message
) -> None:

    user_mode.pop(
        message.from_user.id,
        None
    )

    await send_main_menu(
        message
    )


# ============================================================
# SUPPORT REPLY HANDLER
# ============================================================

@router.message(
    F.chat.id == GROUP_ID,
    F.message_thread_id == SUP_CHAT_ID
)
async def handle_support_commands_and_replies(
    message: Message,
    bot: Bot,
) -> None:

    if not message.reply_to_message:
        return

    replied_id = (
        message.reply_to_message.message_id
    )

    user_id = support_message_to_user.get(
        replied_id
    )

    if not user_id:
        return

    text = (
        message.text
        or message.caption
        or ""
    ).strip()

    if not text:
        return

    if text.casefold() == "блок":

        support_banned_users.add(
            user_id
        )

        with contextlib.suppress(Exception):
            await message.answer(
                "Пользователь заблокирован в поддержке."
            )

        return

    escaped = html.escape(
        text
    )

    try:

        await bot.send_message(
            chat_id=user_id,
            text=(
                "<b>💬 Вы получили ответ от поддержки</b>\n\n"
                f"<i>{escaped}</i>"
            ),
        )

    except Exception:

        logger.exception(
            "Failed to send support reply "
            "to user_id=%s",
            user_id
        )


# ============================================================
# MEDIA GROUP HANDLER
# ============================================================

@router.message(
    F.media_group_id
)
async def handle_media_group_item(
    message: Message,
    bot: Bot,
) -> None:

    if get_message_kind(message) not in {
        "photo",
        "video",
    }:
        return

    key = (
        message.chat.id,
        message.media_group_id,
    )

    buffer = media_group_buffers.get(
        key
    )

    if buffer is None:

        buffer = {
            "messages": [],
            "task": None,
        }

        media_group_buffers[
            key
        ] = buffer

    existing_ids = {
        msg.message_id
        for msg in buffer["messages"]
    }

    if message.message_id not in existing_ids:

        buffer["messages"].append(
            message
        )

    # Каждый новый элемент сбрасывает таймер.
    old_task = buffer.get(
        "task"
    )

    if (
        old_task is not None
        and not old_task.done()
    ):

        old_task.cancel()

    buffer["task"] = asyncio.create_task(
        process_media_group_after_delay(
            key,
            bot,
        )
    )


# ============================================================
# PRIVATE FALLBACK
# ============================================================

@router.message()
async def handle_private_fallback(
    message: Message,
    bot: Bot,
) -> None:

    if message.chat.type != "private":
        return

    # Альбом обрабатывается отдельным handler.
    if message.media_group_id:
        return

    user_id = message.from_user.id

    mode = user_mode.get(
        user_id
    )

    # ========================================================
    # POST
    # ========================================================

    if mode == "post":

        banned_until = user_bans.get(
            user_id
        )

        now_ts = int(
            datetime.now(timezone.utc).timestamp()
        )

        if (
            banned_until
            and banned_until > now_ts
        ):

            remaining = (
                banned_until - now_ts
            )

            user_mode.pop(
                user_id,
                None
            )

            await message.answer(
                "🚫 Вы были заблокированы в предложке. "
                f"Вы будете разблокированы через "
                f"{format_remaining(remaining)}",
                reply_markup=MAIN_KB,
            )

            return

        kind = get_message_kind(
            message
        )

        if kind not in {
            "text",
            "photo",
            "video",
        }:

            await message.answer(
                "Отправьте текст, фото или видео.",
                reply_markup=CANCEL_KB,
            )

            return

        try:

            moderation_message_id, record = (
                await send_submission_to_topic(
                    bot=bot,
                    topic_id=POST_CHAT_ID,
                    user=message.from_user,
                    source_message=message,
                    bundle_messages=None,
                )
            )

        except TelegramBadRequest as e:

            logger.error(
                "Failed to send post. "
                "Telegram error: %s",
                e
            )

            await message.answer(
                "❌ Не удалось отправить пост в тему."
            )

            return

        except Exception:

            logger.exception(
                "Unexpected error while sending post"
            )

            await message.answer(
                "❌ Произошла ошибка при отправке поста.",
                reply_markup=MAIN_KB,
            )

            return

        pending_posts[
            moderation_message_id
        ] = record

        # Для обычного поста кнопки добавляем
        # прямо к сообщению.
        try:

            await bot.edit_message_reply_markup(
                chat_id=GROUP_ID,
                message_id=moderation_message_id,
                reply_markup=post_action_kb(),
            )

        except TelegramBadRequest as e:

            pending_posts.pop(
                moderation_message_id,
                None
            )

            logger.error(
                "Failed to add moderation keyboard "
                "to single post. "
                "message_id=%s Telegram error: %s",
                moderation_message_id,
                e
            )

            await message.answer(
                "❌ Не удалось добавить кнопки управления "
                "к предложенному посту.",
                reply_markup=MAIN_KB,
            )

            return

        except Exception:

            pending_posts.pop(
                moderation_message_id,
                None
            )

            logger.exception(
                "Unexpected error adding moderation keyboard"
            )

            await message.answer(
                "❌ Не удалось добавить кнопки управления "
                "к предложенному посту.",
                reply_markup=MAIN_KB,
            )

            return

        user_mode.pop(
            user_id,
            None
        )

        await message.answer(
            "<b>✅ Ваш пост принят на рассмотрение</b>\n\n"
            "Хотите повторно отправить пост или обратиться "
            "в поддержку канала? Выберите необходимую опцию "
            "при помощи кнопок ниже.",
            reply_markup=MAIN_KB,
        )

        return

    # ========================================================
    # SUPPORT
    # ========================================================

    if mode == "support":

        if user_id in support_banned_users:

            await message.answer(
                "🚫 Вы заблокированы в поддержке."
            )

            return

        kind = get_message_kind(
            message
        )

        if kind not in {
            "text",
            "photo",
            "video",
        }:

            await message.answer(
                "Отправьте текст, фото или видео.",
                reply_markup=CANCEL_KB,
            )

            return

        try:

            copied_ids = (
                await send_support_submission_to_topic(
                    bot=bot,
                    topic_id=SUP_CHAT_ID,
                    user=message.from_user,
                    source_message=message,
                    bundle_messages=None,
                )
            )

        except TelegramBadRequest as e:

            logger.error(
                "Failed to send support. "
                "Telegram error: %s",
                e
            )

            await message.answer(
                "❌ Не удалось отправить сообщение в поддержку."
            )

            return

        except Exception:

            logger.exception(
                "Unexpected error while sending support"
            )

            await message.answer(
                "❌ Произошла ошибка при отправке сообщения.",
                reply_markup=CANCEL_KB,
            )

            return

        for copied_id in copied_ids:

            support_message_to_user[
                copied_id
            ] = user_id

        await message.answer(
            "✅ Ваше обращение было передано в поддержку."
        )

        return

    # ========================================================
    # NO MODE -> PRIKOL
    # ========================================================

    # Полностью молча.
    await prikol_copy_message(
        bot,
        message,
    )


# ============================================================
# MODERATION: ACCEPT
# ============================================================

@router.callback_query(
    F.data == "post:accept"
)
async def cb_post_accept(
    callback: CallbackQuery,
    bot: Bot,
) -> None:

    msg = callback.message

    if not msg:
        await callback.answer()
        return

    record = pending_posts.pop(
        msg.message_id,
        None
    )

    if not record:

        await callback.answer(
            "Пост уже обработан",
            show_alert=True,
        )

        return

    body = record["body"]

    admin_link = admin_mention_html(
        callback.from_user
    )

    status_line = (
        f"✅ Принято: {admin_link}"
    )

    is_album = (
        record.get("kind")
        == "album"
    )

    try:

        await edit_moderation_message(
            bot=bot,
            msg=msg,
            status_line=status_line,
            body=body,
            is_album=is_album,
        )

    except Exception:

        logger.exception(
            "Failed to edit moderation message "
            "for accepted post"
        )

    try:

        await publish_post_to_channel(
            bot,
            record
        )

    except Exception:

        logger.exception(
            "Failed to publish accepted post"
        )

    await callback.answer(
        "Принято"
    )


# ============================================================
# MODERATION: REJECT
# ============================================================

@router.callback_query(
    F.data == "post:reject"
)
async def cb_post_reject(
    callback: CallbackQuery,
    bot: Bot,
) -> None:

    msg = callback.message

    if not msg:
        await callback.answer()
        return

    record = pending_posts.pop(
        msg.message_id,
        None
    )

    if not record:

        await callback.answer(
            "Пост уже обработан",
            show_alert=True,
        )

        return

    body = record["body"]

    admin_link = admin_mention_html(
        callback.from_user
    )

    status_line = (
        f"❌ Отклонено: {admin_link}"
    )

    is_album = (
        record.get("kind")
        == "album"
    )

    with contextlib.suppress(Exception):

        await edit_moderation_message(
            bot=bot,
            msg=msg,
            status_line=status_line,
            body=body,
            is_album=is_album,
        )

    await callback.answer(
        "Отклонено"
    )


# ============================================================
# MODERATION: BAN MENU
# ============================================================

@router.callback_query(
    F.data == "post:ban_menu"
)
async def cb_post_ban_menu(
    callback: CallbackQuery,
    bot: Bot,
) -> None:

    msg = callback.message

    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_posts:

        await callback.answer(
            "Пост уже обработан",
            show_alert=True,
        )

        return

    with contextlib.suppress(Exception):

        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=ban_menu_kb(),
        )

    await callback.answer()


# ============================================================
# MODERATION: BACK
# ============================================================

@router.callback_query(
    F.data == "post:back"
)
async def cb_post_back(
    callback: CallbackQuery,
    bot: Bot,
) -> None:

    msg = callback.message

    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_posts:

        await callback.answer(
            "Пост уже обработан",
            show_alert=True,
        )

        return

    with contextlib.suppress(Exception):

        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=post_action_kb(),
        )

    await callback.answer()


# ============================================================
# MODERATION: BAN
# ============================================================

@router.callback_query(
    F.data.startswith("ban:")
)
async def cb_post_ban_duration(
    callback: CallbackQuery,
    bot: Bot,
) -> None:

    msg = callback.message

    if not msg:
        await callback.answer()
        return

    record = pending_posts.pop(
        msg.message_id,
        None
    )

    if not record:

        await callback.answer(
            "Пост уже обработан",
            show_alert=True,
        )

        return

    try:

        seconds = int(
            callback.data.split(
                ":",
                1
            )[1]
        )

    except Exception:

        await callback.answer(
            "Ошибка",
            show_alert=True
        )

        return

    user_id = record["user_id"]

    banned_until = (
        int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )
        + seconds
    )

    user_bans[
        user_id
    ] = banned_until

    label = BAN_LABEL_BY_SECONDS.get(
        seconds,
        "время"
    )

    admin_link = admin_mention_html(
        callback.from_user
    )

    status_line = (
        f"🚫 Бан на {label}: {admin_link}"
    )

    is_album = (
        record.get("kind")
        == "album"
    )

    with contextlib.suppress(Exception):

        await edit_moderation_message(
            bot=bot,
            msg=msg,
            status_line=status_line,
            body=record["body"],
            is_album=is_album,
        )

    await callback.answer(
        f"Пользователь заблокирован на {label}"
    )


# ============================================================
# MAIN
# ============================================================

async def main() -> None:

    logger.info(
        "Starting bot: "
        "GROUP_ID=%s "
        "POST_CHAT_ID=%s "
        "SUP_CHAT_ID=%s "
        "CHANNEL_ID=%s "
        "PRIKOL_CHAT_ID=%s",
        GROUP_ID,
        POST_CHAT_ID,
        SUP_CHAT_ID,
        CHANNEL_ID,
        PRIKOL_CHAT_ID,
    )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    # Определяем, является ли PRIKOL_CHAT_ID
    # настоящим чатом или topic_id внутри GROUP_ID.
    await detect_prikol_destination(
        bot
    )

    dp = Dispatcher()

    dp.include_router(
        router
    )

    runner = await start_web_server()

    try:

        await dp.start_polling(
            bot
        )

    finally:

        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(
        main()
    )
