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
    ReplyParameters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID", "0"))          # форум-группа
POST_CHAT_ID = int(os.getenv("POST_CHAT_ID", "0"))  # тема для постов
SUP_CHAT_ID = int(os.getenv("SUP_CHAT_ID", "0"))    # тема для поддержки
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))      # канал
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN or not GROUP_ID or not POST_CHAT_ID or not SUP_CHAT_ID or not CHANNEL_ID:
    raise RuntimeError(
        "Missing one of required env vars: BOT_TOKEN, GROUP_ID, POST_CHAT_ID, SUP_CHAT_ID, CHANNEL_ID"
    )

TEST_CHANNEL_ID = int(os.getenv("TEST_CHANNEL_ID", "0"))  # тестовый канал для рекламы

# --- Реклама: настройки ------------------------------------------------------
AD_CHANNEL_ID = TEST_CHANNEL_ID                         # куда публикуется принятая реклама (для боевого режима: CHANNEL_ID)
AD_ALLOWED_USERNAMES: Optional[set[str]] = {"anti998"}  # юзернеймы в нижнем регистре; None = доступно всем
AD_ADD_CHANNEL_FOOTER = True                            # добавлять к рекламе строку «Предложить пост | Чат | Буст»
AD_PRICE_BASE = 15
AD_PRICE_LINK = 25
AD_PRICE_PIN_1D = 15
AD_PRICE_PIN_3D = 30
AD_CARDS = {
    "mono": ("monobank", os.getenv("CARD_MONOBANK", "4441 1110 4722 3866")),
    "alliance": ("alliance bank", os.getenv("CARD_ALLIANCE", "5232 4410 2178 9701")),
    "sense": ("sense bank", os.getenv("CARD_SENSE", "4102 3251 0278 4766")),
}

AD_BUTTON_TEXT = "📣 Выложить свою рекламу"
AD_UNAVAILABLE_TEXT = "❌ Эта функция временно недоступна."
AD_HEADER_HTML = "<b>Реклама</b>"

AD_INTRO_TEXT = (
    "<b>📢 Реклама в канале</b>\n\n"
    "📝 Отправьте свой рекламный пост, который Вы хотели бы разместить в канале. "
    "Вы можете прикрепить текст, изображение или видео."
)

AD_RULES_TEXT = (
    "<b>📋 Правила размещения рекламы</b>\n\n"
    "Перед отправкой рекламного поста ознакомьтесь с правилами размещения.\n\n"
    "<blockquote expandable>"
    "🚫 Запрещённое содержание:\n\n"
    "- Реклама казино, азартных игр и букмекерских контор.\n"
    "- Реклама наркотических и психотропных веществ.\n"
    "- Призывы к употреблению или распространению запрещённых веществ.\n"
    "- Мошенничество, финансовые пирамиды и обман пользователей.\n"
    "- Вредоносные ссылки, фишинг и распространение вирусов.\n"
    "- Материалы, содержащие угрозы, призывы к насилию, экстремизму или буллингу.\n"
    "- Реклама, вводящая пользователей в заблуждение относительно её источника.\n\n"
    "💰 Стоимость размещения:\n\n"
    f"- Обычный рекламный пост — {AD_PRICE_BASE} грн.\n"
    f"- Наличие ссылки, инструкции по переходу на ссылку или юзернейма канала — +{AD_PRICE_LINK} грн.\n"
    f"- Закрепить рекламный пост в канале на 1 день — +{AD_PRICE_PIN_1D} грн.\n"
    f"- Закрепить рекламный пост в канале на 3 дня — +{AD_PRICE_PIN_3D} грн.\n\n"
    f"🔗 Что считается обязательным дополнительным пунктом (+{AD_PRICE_LINK} грн):\n\n"
    "- Любая ссылка в посте, включая некликабельную.\n"
    "- Инструкция по переходу на какой-либо ресурс.\n"
    "- Юзернейм Telegram-канала (не личного аккаунта пользователя).\n"
    "- Юзернейм человека с инструкцией перейти через его аккаунт в канал, указанный в профиле.\n\n"
    "❗ Указание собственного юзернейма в рекламе дополнительной оплаты НЕ требует.\n\n"
    "Если рекламный пост содержит что-либо из перечисленного, об этом необходимо сообщить до публикации "
    "при помощи специальных кнопок настроек, отображающихся в боте после написания поста. "
    "Сокрытие данного пункта приведёт к отклонению поста.\n\n"
    "📌 Дополнительные условия:\n\n"
    "- Рекламный пост не должен выдавать себя за официальную публикацию, рекомендацию или одобрение "
    "от имени администрации канала.\n"
    "- Пользовательская реклама должна быть явно обозначена как реклама, а не как официальная позиция канала.\n"
    "- Администрация оставляет за собой право отклонить рекламный пост, даже если он формально не нарушает "
    "перечисленные запреты, но содержит явные попытки обойти установленные правила или замаскировать "
    "запрещённое содержание.\n\n"
    "⚠️ Важно!\n\n"
    "Нарушение любого из перечисленных правил является основанием для отклонения рекламного поста.\n\n"
    "Если оплата за такой пост уже была произведена, возврат денежных средств не предусмотрен.\n\n"
    "Отправляя рекламный пост, Вы подтверждаете своё согласие с данными правилами."
    "</blockquote>"
)

router = Router()
TZ = ZoneInfo("Europe/Zaporozhye")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

MAIN_TEXT = (
    "<b>👋 Добро пожаловать в бота «СГП»!</b>\n"
    "Здесь Вы можете предложить пост или обратиться в поддержку канала."
)

POST_PROMPT = "🖼️ Предложите свой пост для канала. Это может быть видео, картинка или надпись."
SUPPORT_PROMPT = "📥 Пожалуйста, подробно опишите вашу проблему и вскоре Вы получите ответ в порядке очереди."

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🖼️ Предложить пост"), KeyboardButton(text="📥 Поддержка")],
        [KeyboardButton(text=AD_BUTTON_TEXT)],
    ],
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
user_mode: Dict[int, str] = {}                  # user_id -> "post" | "support" | "ad"
user_bans: Dict[int, int] = {}                  # user_id -> unix timestamp
support_banned_users: set[int] = set()          # users blocked from support
pending_posts: Dict[int, Dict[str, Any]] = {}   # moderation/control message_id -> record
support_message_to_user: Dict[int, int] = {}    # support topic forwarded message_id -> user_id
media_group_buffers: Dict[Tuple[int, str], Dict[str, Any]] = {}  # (chat_id, media_group_id) -> buffer
ad_drafts: Dict[int, Dict[str, Any]] = {}       # user_id -> черновик рекламного поста
ad_rules_agreed: set[int] = set()               # кто уже согласился с правилами рекламы
pending_ads: Dict[int, Dict[str, Any]] = {}     # admin message_id -> рекламный пост на проверке
pending_support_replies: Dict[int, Dict[str, Any]] = {}  # confirm message_id -> ответ поддержки на подтверждении
background_tasks: set = set()                   # ссылки на фоновые задачи (открепление рекламы)


def now_local() -> datetime:
    return datetime.now(TZ)


def extract_message_id(result: Any) -> int:
    return int(getattr(result, "message_id", result))


def mention_html(user_id: int, full_name: str, username: Optional[str] = None) -> str:
    safe_name = html.escape(full_name or "Пользователь")
    if username:
        safe_username = html.escape(username, quote=True)
        return f'<a href="https://t.me/{safe_username}">{safe_name}</a>'
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def user_mention_html(user) -> str:
    return mention_html(user.id, user.full_name, user.username)


def admin_mention_html(user) -> str:
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
    return ""


def get_message_kind(message: Message) -> str:
    if message.text:
        return "text"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    return "other"


def build_status_text(body: str, status_line: str, include_body: bool = True) -> str:
    body = (body or "").strip()
    if include_body and body:
        return f"{html.escape(body)}\n\n{status_line}"
    return status_line


def build_channel_text(body: str) -> str:
    body = (body or "").strip()
    parts = []
    if body:
        parts.append(html.escape(body))
    parts.append(CHANNEL_FOOTER)
    text = "\n\n".join(parts)
    return text[:4096] if len(text) <= 4096 else text[:4093] + "..."


def build_channel_caption(body: str) -> str:
    body = (body or "").strip()
    parts = []
    if body:
        parts.append(html.escape(body))
    parts.append(CHANNEL_FOOTER)
    caption = "\n\n".join(parts)
    return caption[:1024] if len(caption) <= 1024 else caption[:1021] + "..."


def post_action_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data="post:accept"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data="post:reject"),
            ],
            [InlineKeyboardButton(text="🚫 Заблокировать", callback_data="post:ban_menu")],
        ]
    )


def ban_menu_kb(prefix: str = "ban:", back_data: str = "post:back") -> InlineKeyboardMarkup:
    rows = []
    row = []
    for label, seconds in BAN_OPTIONS:
        row.append(InlineKeyboardButton(text=f"🚫 {label}", callback_data=f"{prefix}{seconds}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_media_group_for_channel(media_items: List[Dict[str, str]], body: str):
    caption = build_channel_caption(body)
    result = []

    for i, item in enumerate(media_items):
        mtype = item["type"]
        file_id = item["file_id"]

        if mtype == "photo":
            result.append(
                InputMediaPhoto(
                    media=file_id,
                    caption=caption if i == 0 else None,
                    parse_mode=ParseMode.HTML,
                )
            )
        elif mtype == "video":
            result.append(
                InputMediaVideo(
                    media=file_id,
                    caption=caption if i == 0 else None,
                    parse_mode=ParseMode.HTML,
                )
            )
    return result


async def send_main_menu(message: Message) -> None:
    await message.answer(MAIN_TEXT, reply_markup=MAIN_KB)


async def send_post_prompt(message: Message) -> None:
    await message.answer(POST_PROMPT, reply_markup=CANCEL_KB)


async def send_support_prompt(message: Message) -> None:
    await message.answer(SUPPORT_PROMPT, reply_markup=CANCEL_KB)


async def start_web_server() -> web.AppRunner:
    app = web.Application()

    async def healthcheck(request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app.router.add_get("/", healthcheck)
    app.router.add_get("/health", healthcheck)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info("Web server started on 0.0.0.0:%s", PORT)
    return runner


async def send_moderation_control_message(bot: Bot, topic_id: int) -> int:
    # Must be real, visible, non-empty text - Telegram rejects a message
    # whose text is only an invisible/placeholder character as empty.
    control = await bot.send_message(
        chat_id=GROUP_ID,
        message_thread_id=topic_id,
        text="📋 Управление постом",
        reply_markup=post_action_kb(),
        link_preview_options=NO_PREVIEW,
    )
    return extract_message_id(control)


async def send_submission_single_to_topic(
    bot: Bot,
    topic_id: int,
    user,
    source_message: Message,
):
    author_line = f"От {user_mention_html(user)} в {now_local().strftime('%H:%M')}"
    header_msg = await bot.send_message(
        chat_id=GROUP_ID,
        message_thread_id=topic_id,
        text=author_line,
        link_preview_options=NO_PREVIEW,
    )

    try:
        kind = get_message_kind(source_message)

        if kind == "text":
            sent = await bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                text=html.escape(source_message.text or ""),
                link_preview_options=NO_PREVIEW,
            )
            sent_id = extract_message_id(sent)

            return sent_id, {
                "kind": "single",
                "content_type": "text",
                "body": get_post_body(source_message),
                "topic_message_ids": [sent_id],
                "source_message_ids": [source_message.message_id],
                "user_id": user.id,
            }

        if kind in {"photo", "video"}:
            copied = await bot.copy_message(
                chat_id=GROUP_ID,
                from_chat_id=source_message.chat.id,
                message_id=source_message.message_id,
                message_thread_id=topic_id,
            )
            copied_id = extract_message_id(copied)

            media_type = "photo" if source_message.photo else "video"
            file_id = (
                source_message.photo[-1].file_id
                if source_message.photo
                else source_message.video.file_id
            )

            return copied_id, {
                "kind": "single",
                "content_type": media_type,
                "body": get_post_body(source_message),
                "file_id": file_id,
                "topic_message_ids": [copied_id],
                "source_message_ids": [source_message.message_id],
                "user_id": user.id,
            }

        raise ValueError("Unsupported message type")

    except Exception:
        with contextlib.suppress(Exception):
            await bot.delete_message(chat_id=GROUP_ID, message_id=header_msg.message_id)
        raise


async def send_submission_album_to_topic(
    bot: Bot,
    topic_id: int,
    user,
    bundle_messages: List[Message],
):
    bundle_messages = sorted(bundle_messages, key=lambda m: m.message_id)
    source_ids = [m.message_id for m in bundle_messages]

    author_line = f"От {user_mention_html(user)} в {now_local().strftime('%H:%M')}"
    header_msg = await bot.send_message(
        chat_id=GROUP_ID,
        message_thread_id=topic_id,
        text=author_line,
        link_preview_options=NO_PREVIEW,
    )

    try:
        copied = await bot.copy_messages(
            chat_id=GROUP_ID,
            from_chat_id=bundle_messages[0].chat.id,
            message_ids=source_ids,
            message_thread_id=topic_id,
        )
        copied_ids = [extract_message_id(x) for x in copied]

        media_items = []
        for m in bundle_messages:
            if m.photo:
                media_items.append({"type": "photo", "file_id": m.photo[-1].file_id})
            elif m.video:
                media_items.append({"type": "video", "file_id": m.video.file_id})

        body = ""
        for m in bundle_messages:
            if m.caption:
                body = m.caption
                break

        # Telegram does not attach/render inline keyboards on individual
        # items of a media group/album - editMessageReplyMarkup on one of
        # the copied album items is accepted without error but the keyboard
        # simply never shows up. So the moderation buttons must live on a
        # separate, standalone message outside the album.
        #
        # (An earlier version of this used a message whose text was only an
        # invisible placeholder character, which Telegram rejects outright
        # with "Bad Request: message text is empty" - that's a different bug
        # this control message must also avoid by using real, visible text.)
        control_message_id = await send_moderation_control_message(bot, topic_id)

        record = {
            "kind": "album",
            "content_type": "album",
            "body": body,
            "media_items": media_items,
            "topic_message_ids": copied_ids,
            "source_message_ids": source_ids,
            "user_id": user.id,
            "control_message_id": control_message_id,
        }
        return control_message_id, record

    except Exception:
        with contextlib.suppress(Exception):
            await bot.delete_message(chat_id=GROUP_ID, message_id=header_msg.message_id)
        raise


async def send_submission_to_topic(
    bot: Bot,
    topic_id: int,
    user,
    source_message: Message,
    bundle_messages: Optional[List[Message]] = None,
):
    if bundle_messages and len(bundle_messages) > 1:
        return await send_submission_album_to_topic(bot, topic_id, user, bundle_messages)
    return await send_submission_single_to_topic(bot, topic_id, user, source_message)


async def send_support_submission_to_topic(
    bot: Bot,
    topic_id: int,
    user,
    source_message: Message,
    bundle_messages: Optional[List[Message]] = None,
) -> List[int]:
    author_line = f"От {user_mention_html(user)} в {now_local().strftime('%H:%M')}"
    header_msg = await bot.send_message(
        chat_id=GROUP_ID,
        message_thread_id=topic_id,
        text=author_line,
        link_preview_options=NO_PREVIEW,
    )

    try:
        copied_ids: List[int] = []

        if bundle_messages and len(bundle_messages) > 1:
            bundle_messages = sorted(bundle_messages, key=lambda m: m.message_id)
            for msg in bundle_messages:
                forwarded = await bot.forward_message(
                    chat_id=GROUP_ID,
                    from_chat_id=msg.chat.id,
                    message_id=msg.message_id,
                    message_thread_id=topic_id,
                )
                copied_ids.append(extract_message_id(forwarded))
        else:
            forwarded = await bot.forward_message(
                chat_id=GROUP_ID,
                from_chat_id=source_message.chat.id,
                message_id=source_message.message_id,
                message_thread_id=topic_id,
            )
            copied_ids.append(extract_message_id(forwarded))

        return copied_ids

    except Exception:
        with contextlib.suppress(Exception):
            await bot.delete_message(chat_id=GROUP_ID, message_id=header_msg.message_id)
        raise


async def edit_topic_message_with_status(
    bot: Bot,
    msg: Message,
    status_line: str,
    body: str,
    include_body: bool = True,
) -> None:
    new_text = build_status_text(body, status_line, include_body=include_body)
    if msg.text is not None:
        await bot.edit_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=new_text,
            reply_markup=None,
            link_preview_options=NO_PREVIEW,
        )
    else:
        await bot.edit_message_caption(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            caption=new_text,
            reply_markup=None,
        )


async def publish_post_to_channel(bot: Bot, record: Dict[str, Any]) -> None:
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
        media = build_media_group_for_channel(record["media_items"], body)
        if media:
            await bot.send_media_group(CHANNEL_ID, media=media)
        return

    await bot.send_message(
        CHANNEL_ID,
        build_channel_text(body),
        link_preview_options=NO_PREVIEW,
    )


# ---------------------------------------------------------------------------
# Реклама: вспомогательные функции
# ---------------------------------------------------------------------------

NUM_EMOJI = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣"}


def ad_access_allowed(user) -> bool:
    if AD_ALLOWED_USERNAMES is None:
        return True
    username = (getattr(user, "username", None) or "").lower()
    return username in AD_ALLOWED_USERNAMES


def ban_remaining(user_id: int) -> int:
    banned_until = user_bans.get(user_id)
    if not banned_until:
        return 0
    left = banned_until - int(datetime.now(timezone.utc).timestamp())
    if left <= 0:
        user_bans.pop(user_id, None)
        return 0
    return left


def fit_html(body: str, reserved: int, limit: int) -> str:
    """Экранирует body и обрезает так, чтобы вместе с reserved символами влезть в limit."""
    body = (body or "").strip()
    if not body:
        return ""
    avail = max(limit - reserved, 0)
    if len(html.escape(body)) <= avail:
        return html.escape(body)
    if avail <= 1:
        return ""

    def cut(n: int) -> str:
        return html.escape(body[:n].rstrip())

    lo, hi = 0, len(body)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(cut(mid)) + 1 <= avail:
            lo = mid
        else:
            hi = mid - 1
    return cut(lo) + "…"


def ad_sender_label(data: Dict[str, Any]) -> str:
    username = data.get("username")
    if username:
        return f"@{username}"
    return data.get("full_name") or "Пользователь"


def ad_sender_html(data: Dict[str, Any]) -> str:
    label = html.escape(ad_sender_label(data))
    username = data.get("username")
    if username:
        return f'<a href="https://t.me/{html.escape(username, quote=True)}">{label}</a>'
    return f'<a href="tg://user?id={data["user_id"]}">{label}</a>'


def ad_price(data: Dict[str, Any]) -> int:
    total = AD_PRICE_BASE
    if data.get("link"):
        total += AD_PRICE_LINK
    pin_days = data.get("pin_days", 0)
    if pin_days == 1:
        total += AD_PRICE_PIN_1D
    elif pin_days == 3:
        total += AD_PRICE_PIN_3D
    return total


def ad_pin_label(pin_days: int) -> str:
    if pin_days == 1:
        return "Закреп на 1 день"
    if pin_days == 3:
        return "Закреп на 3 дня"
    return "Нет закрепа"


def ad_info_html(record: Dict[str, Any]) -> str:
    return f"{ad_sender_html(record)}, {record['price']} грн\n{ad_pin_label(record.get('pin_days', 0))}"


def build_ad_admin_text(record: Dict[str, Any], status_line: Optional[str] = None) -> str:
    tail = f"\n\n──────────\n{ad_info_html(record)}"
    if status_line:
        tail += f"\n\n{status_line}"
    limit = 1024 if record["content_type"] in {"photo", "video"} else 4096
    body_html = fit_html(record.get("body", ""), len(tail), limit)
    return f"{body_html}{tail}" if body_html else tail.lstrip("\n")


def build_ad_channel_html(record: Dict[str, Any], limit: int) -> str:
    footer = CHANNEL_FOOTER if AD_ADD_CHANNEL_FOOTER else ""
    reserved = len(AD_HEADER_HTML) + len(footer) + 4
    body_html = fit_html(record.get("body", ""), reserved, limit)
    return "\n\n".join(part for part in (AD_HEADER_HTML, body_html, footer) if part)


def ad_messages_have_link(messages: List[Message]) -> bool:
    for m in messages:
        text = get_post_body(m).lower()
        if "https://" in text or "http://" in text:
            return True
    return False


# --- клавиатуры -------------------------------------------------------------

def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def ad_rules_button_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("📋 Правила размещения рекламы", "adu:rules")]])


def ad_step_rules_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Ознакомлен(а) и согласен(на)", "adu:agree")],
            [_btn("📋 Правила размещения рекламы", "adu:rules")],
            [_btn("📝 Вернуться к написанию поста", "adu:restart")],
        ]
    )


def ad_options_kb(d: Dict[str, Any]) -> InlineKeyboardMarkup:
    link_on = bool(d.get("link"))
    pin = d.get("pin_days", 0)

    def toggle(on: bool, title: str, data: str) -> InlineKeyboardButton:
        return _btn(f"{'✅' if on else '❌'} {title}: {'да' if on else 'нет'}", data)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📝 Вернуться к написанию поста", "adu:restart")],
            [toggle(link_on, "Ваш пост содержит ссылку", "adu:link")],
            [toggle(pin == 1, "Закрепить пост на 1 день", "adu:pin1")],
            [toggle(pin == 3, "Закрепить пост на 3 дня", "adu:pin3")],
            [_btn("✅ Подтвердить отправку", "adu:confirm")],
        ]
    )


def ad_pay_kb() -> InlineKeyboardMarkup:
    rows = [[_btn("⬅️ Назад", "adu:back_opts")]]
    for key, (bank_name, _card) in AD_CARDS.items():
        rows.append([_btn(f"💳 {bank_name}", f"adu:bank:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ad_bank_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("⬅️ Назад", "adu:back_pay")],
            [_btn("✅ Я совершил(а) перевод", "adu:paid")],
        ]
    )


def ad_action_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Принять", "adm:accept")],
            [_btn("❌ Отклонить", "adm:reject_menu")],
            [_btn("🚫 Заблокировать", "adm:ban_menu")],
        ]
    )


def ad_reject_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("⬅️ Назад", "adm:back")],
            [_btn("❌ Нарушение содержания рекламы", "adm:rej:content")],
            [_btn("❌ Реклама не оплачена", "adm:rej:unpaid")],
            [_btn("❌ Оплата содержит не всю сумму", "adm:rej:partial")],
        ]
    )


# --- тексты этапов ----------------------------------------------------------

def ad_step_title(d: Dict[str, Any], step: int) -> str:
    # step: 1 = правила, 2 = параметры, 3 = оплата
    if d.get("skipped_rules"):
        n, total = step - 1, 2
    else:
        n, total = step, 3
    return f"<b>{NUM_EMOJI[n]} {n}/{total} этап отправки</b>"


def ad_rules_step_text(d: Dict[str, Any]) -> str:
    return (
        f"{ad_step_title(d, 1)}\n\n"
        "Подтвердите, что Вы ознакомились с правилами размещения рекламы и согласны "
        "соблюдать все перечисленные условия нажав кнопку ниже"
    )


def ad_options_text(d: Dict[str, Any]) -> str:
    return (
        f"{ad_step_title(d, 2)}\n\n"
        "Укажите дополнительные параметры Вашего рекламного поста при помощи кнопок ниже.\n\n"
        "1. Содержит ли Ваш рекламный пост ссылку, юзернейм или инструкцию по переходу "
        "на какой-либо ресурс?\n\n"
        "2. Хотите ли Вы закрепить рекламный пост в канале?\n\n"
        f"Текущая стоимость публикации: {ad_price(d)} грн"
    )


def ad_pay_text(d: Dict[str, Any]) -> str:
    label = html.escape(ad_sender_label(d))
    if d.get("username"):
        ident = f"<b>прикрепив к оплате свой тг юзернейм</b> (Ваш юз: {label})"
    else:
        ident = f"<b>прикрепив к оплате свой тг никнейм</b> (Ваш ник: {label})"

    lines = [f"- Размещение рекламы {AD_PRICE_BASE} грн"]
    if d.get("link"):
        lines.append(f"- Прикрепление ссылки {AD_PRICE_LINK} грн")
    if d.get("pin_days") == 1:
        lines.append(f"- Закрепление на 1 день {AD_PRICE_PIN_1D} грн")
    elif d.get("pin_days") == 3:
        lines.append(f"- Закрепление на 3 дня {AD_PRICE_PIN_3D} грн")
    included = "\n".join(f"<i>{line}</i>" for line in lines)

    return (
        f"{ad_step_title(d, 3)}\n\n"
        f"Для размещения вашего рекламного поста осталось оплатить <b>{ad_price(d)} грн</b> "
        f"выбрав банк ниже и {ident} для идентификации.\n\n"
        f"В стоимость рекламы входит:\n{included}"
    )


def ad_bank_text(d: Dict[str, Any], bank_key: str) -> str:
    bank_name, card = AD_CARDS[bank_key]
    label = html.escape(ad_sender_label(d))
    if d.get("username"):
        ident = f"юзернейм (Ваш юз: {label})"
    else:
        ident = f"никнейм (Ваш ник: {label})"
    return (
        f"<b>💳 Оплата через {bank_name}</b>\n\n"
        "Для продолжения отправки рекламного поста, откройте банковское приложение "
        f"и переведите <b>{ad_price(d)} грн</b> на указанную ниже карту:\n\n"
        f"💳 Номер карты: <code>{html.escape(card)}</code>\n\n"
        f"⚠️ Важно! При переводе обязательно <b>укажите свой {ident}</b> в комментарии "
        "к платежу, чтобы администрация могла идентифицировать Вашу оплату.\n\n"
        "После совершения перевода нажмите кнопку ниже для подтверждения оплаты."
    )


AD_SENT_TEXT = (
    "<b>✅ Ваш рекламный пост отправлен</b>\n\n"
    "Ожидайте, пока администрация проверит Ваш рекламный пост и выставит его в канал."
)

AD_REJECT_REASONS = {
    "content": "нарушение содержания",
    "unpaid": "не оплачено",
    "partial": "оплачена не вся сумма",
}


def ad_reject_user_text(reason: str, record: Dict[str, Any]) -> str:
    head = "<b>❌ Ваш рекламный пост отклонён</b>\n\n"
    price = record["price"]
    if reason == "content":
        return head + (
            "Причина: нарушение правил размещения рекламы. Содержание Вашего поста не "
            "соответствует установленным требованиям (запрещённое содержание, введение "
            "пользователей в заблуждение, попытка выдать рекламу за официальную публикацию "
            "канала или обойти правила).\n\n"
            "Согласно правилам, при отклонении по этой причине возврат денежных средств "
            "не предусмотрен."
        )
    if reason == "unpaid":
        return head + (
            f"Причина: оплата не найдена. Администрация не обнаружила платёж за размещение "
            f"Вашего рекламного поста (к оплате: {price} грн).\n\n"
            "Если Вы уверены, что совершили перевод, обратитесь в поддержку через кнопку "
            "«📥 Поддержка» и приложите подтверждение оплаты. Также Вы можете отправить "
            "рекламный пост заново."
        )
    return head + (
        f"Причина: оплачена не вся сумма. Стоимость размещения Вашего рекламного поста — "
        f"{price} грн, но поступившая сумма оказалась меньше.\n\n"
        "Чтобы пост был опубликован, обратитесь в поддержку через кнопку «📥 Поддержка», "
        "приложите подтверждение оплаты и уточните доплату. Также Вы можете отправить "
        "рекламный пост заново."
    )


# --- логика пользовательского флоу ------------------------------------------

async def edit_ad_message(bot: Bot, msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await bot.edit_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=text,
            reply_markup=kb,
            link_preview_options=NO_PREVIEW,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def begin_ad_flow(message: Message, user) -> None:
    ad_drafts.pop(user.id, None)

    remaining = ban_remaining(user.id)
    if remaining > 0:
        user_mode.pop(user.id, None)
        await message.answer(
            f"🚫 Вы были заблокированы в предложке. Вы будете разблокированы через {format_remaining(remaining)}"
        )
        return

    user_mode[user.id] = "ad"
    await message.answer(AD_INTRO_TEXT, reply_markup=ad_rules_button_kb())


async def process_ad_submission(
    primary_message: Message,
    bot: Bot,
    bundle_messages: Optional[List[Message]] = None,
) -> None:
    user = primary_message.from_user
    user_id = user.id

    if not ad_access_allowed(user):
        user_mode.pop(user_id, None)
        await primary_message.answer(AD_UNAVAILABLE_TEXT, reply_markup=MAIN_KB)
        return

    remaining = ban_remaining(user_id)
    if remaining > 0:
        user_mode.pop(user_id, None)
        await primary_message.answer(
            f"🚫 Вы были заблокированы в предложке. Вы будете разблокированы через {format_remaining(remaining)}",
            reply_markup=MAIN_KB,
        )
        return

    if bundle_messages and len(bundle_messages) > 1:
        messages = sorted(bundle_messages, key=lambda m: m.message_id)
    else:
        messages = [primary_message]

    if any(get_message_kind(m) not in {"text", "photo", "video"} for m in messages):
        await primary_message.answer("Отправьте текст, фото или видео.", reply_markup=MAIN_KB)
        return

    draft: Dict[str, Any] = {
        "user_id": user_id,
        "username": user.username,
        "full_name": user.full_name,
        "link": ad_messages_have_link(messages),
        "pin_days": 0,
        "skipped_rules": user_id in ad_rules_agreed,
        "step_msg_id": None,
    }

    if len(messages) > 1:
        media_items = []
        body = ""
        for m in messages:
            if m.photo:
                media_items.append({"type": "photo", "file_id": m.photo[-1].file_id})
            elif m.video:
                media_items.append({"type": "video", "file_id": m.video.file_id})
            if not body and m.caption:
                body = m.caption
        draft.update({"kind": "album", "content_type": "album", "body": body, "media_items": media_items})
    else:
        m = messages[0]
        content_type = get_message_kind(m)
        draft.update({"kind": "single", "content_type": content_type, "body": get_post_body(m)})
        if content_type == "photo":
            draft["file_id"] = m.photo[-1].file_id
        elif content_type == "video":
            draft["file_id"] = m.video.file_id

    ad_drafts[user_id] = draft
    user_mode.pop(user_id, None)

    if draft["skipped_rules"]:
        sent = await primary_message.answer(ad_options_text(draft), reply_markup=ad_options_kb(draft))
    else:
        sent = await primary_message.answer(ad_rules_step_text(draft), reply_markup=ad_step_rules_kb())
    draft["step_msg_id"] = sent.message_id


async def send_ad_to_topic(bot: Bot, draft: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    record = dict(draft)
    record["price"] = ad_price(draft)
    record.pop("step_msg_id", None)

    content_type = record["content_type"]
    kb = ad_action_kb()

    if content_type == "text":
        sent = await bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=POST_CHAT_ID,
            text=build_ad_admin_text(record),
            reply_markup=kb,
            link_preview_options=NO_PREVIEW,
        )
        admin_message_id = extract_message_id(sent)
    elif content_type == "photo":
        sent = await bot.send_photo(
            chat_id=GROUP_ID,
            message_thread_id=POST_CHAT_ID,
            photo=record["file_id"],
            caption=build_ad_admin_text(record),
            reply_markup=kb,
        )
        admin_message_id = extract_message_id(sent)
    elif content_type == "video":
        sent = await bot.send_video(
            chat_id=GROUP_ID,
            message_thread_id=POST_CHAT_ID,
            video=record["file_id"],
            caption=build_ad_admin_text(record),
            reply_markup=kb,
        )
        admin_message_id = extract_message_id(sent)
    elif content_type == "album":
        media = []
        for item in record["media_items"]:
            if item["type"] == "photo":
                media.append(InputMediaPhoto(media=item["file_id"]))
            else:
                media.append(InputMediaVideo(media=item["file_id"]))
        # У альбома кнопки не работают на самих элементах, поэтому
        # информация и кнопки живут в отдельном управляющем сообщении.
        await bot.send_media_group(chat_id=GROUP_ID, message_thread_id=POST_CHAT_ID, media=media)
        control = await bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=POST_CHAT_ID,
            text=build_ad_admin_text(record),
            reply_markup=kb,
            link_preview_options=NO_PREVIEW,
        )
        admin_message_id = extract_message_id(control)
    else:
        raise ValueError("Unsupported ad content type")

    record["admin_message_id"] = admin_message_id
    return admin_message_id, record


async def notify_admins_about_ad(bot: Bot, reply_to_message_id: int) -> None:
    try:
        admins = await bot.get_chat_administrators(GROUP_ID)
    except Exception:
        logger.exception("Failed to fetch chat administrators for GROUP_ID=%s", GROUP_ID)
        return

    mentions: List[str] = []
    for admin in admins:
        admin_user = admin.user
        if admin_user.is_bot:
            continue
        if admin_user.username:
            mentions.append(f"@{html.escape(admin_user.username)}")
        else:
            mentions.append(f'<a href="tg://user?id={admin_user.id}">{html.escape(admin_user.full_name)}</a>')

    for i in range(0, len(mentions), 30):
        chunk = mentions[i:i + 30]
        try:
            await bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=POST_CHAT_ID,
                text="📢 <b>Новый рекламный пост на проверку!</b>\n\n" + " ".join(chunk),
                reply_parameters=ReplyParameters(message_id=reply_to_message_id),
                link_preview_options=NO_PREVIEW,
            )
        except Exception:
            logger.exception("Failed to send admin mention message")


async def publish_ad_to_channel(bot: Bot, record: Dict[str, Any]) -> int:
    content_type = record["content_type"]

    if content_type == "text":
        sent = await bot.send_message(
            AD_CHANNEL_ID,
            build_ad_channel_html(record, 4096),
            link_preview_options=NO_PREVIEW,
        )
        return extract_message_id(sent)

    if content_type == "photo":
        sent = await bot.send_photo(
            AD_CHANNEL_ID,
            photo=record["file_id"],
            caption=build_ad_channel_html(record, 1024),
        )
        return extract_message_id(sent)

    if content_type == "video":
        sent = await bot.send_video(
            AD_CHANNEL_ID,
            video=record["file_id"],
            caption=build_ad_channel_html(record, 1024),
        )
        return extract_message_id(sent)

    if content_type == "album":
        caption = build_ad_channel_html(record, 1024)
        media = []
        for i, item in enumerate(record["media_items"]):
            cap = caption if i == 0 else None
            if item["type"] == "photo":
                media.append(InputMediaPhoto(media=item["file_id"], caption=cap, parse_mode=ParseMode.HTML))
            else:
                media.append(InputMediaVideo(media=item["file_id"], caption=cap, parse_mode=ParseMode.HTML))
        sent_list = await bot.send_media_group(AD_CHANNEL_ID, media=media)
        return extract_message_id(sent_list[0])

    raise ValueError("Unsupported ad content type")


def schedule_unpin(bot: Bot, chat_id: int, message_id: int, days: int) -> None:
    async def runner() -> None:
        await asyncio.sleep(days * 24 * 60 * 60)
        try:
            await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            logger.exception("Failed to unpin ad message_id=%s in chat_id=%s", message_id, chat_id)

    task = asyncio.create_task(runner())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


async def edit_ad_admin_message(bot: Bot, msg: Message, record: Dict[str, Any], status_line: str) -> None:
    text = build_ad_admin_text(record, status_line)
    if msg.text is not None:
        await bot.edit_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=text,
            reply_markup=None,
            link_preview_options=NO_PREVIEW,
        )
    else:
        await bot.edit_message_caption(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            caption=text,
            reply_markup=None,
        )


async def process_submission_bundle(primary_message: Message, bot: Bot, bundle_messages: Optional[List[Message]] = None) -> None:
    user_id = primary_message.from_user.id
    mode = user_mode.get(user_id)

    if mode == "ad":
        await process_ad_submission(primary_message, bot, bundle_messages)
        return

    if mode not in {"post", "support"}:
        if primary_message.chat.type == "private":
            user_mode.pop(user_id, None)
            await send_main_menu(primary_message)
        return

    if mode == "post":
        banned_until = user_bans.get(user_id)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if banned_until and banned_until > now_ts:
            remaining = banned_until - now_ts
            user_mode.pop(user_id, None)
            await primary_message.answer(
                f"🚫 Вы были заблокированы в предложке. Вы будете разблокированы через {format_remaining(remaining)}",
                reply_markup=MAIN_KB,
            )
            return

        try:
            moderation_message_id, record = await send_submission_to_topic(
                bot=bot,
                topic_id=POST_CHAT_ID,
                user=primary_message.from_user,
                source_message=primary_message,
                bundle_messages=bundle_messages,
            )
        except TelegramBadRequest:
            logger.exception(
                "Failed to send post to group/topic. Check GROUP_ID=%s, POST_CHAT_ID=%s, bot membership and topic access.",
                GROUP_ID,
                POST_CHAT_ID,
            )
            await primary_message.answer(
                "❌ Не удалось отправить пост в тему.\n"
                "Проверь, что бот добавлен в форум-группу, что GROUP_ID верный и что POST_CHAT_ID указывает именно на тему для постов."
            )
            return
        except Exception:
            logger.exception("Unexpected error while sending post to topic")
            await primary_message.answer(
                "❌ Произошла ошибка при отправке поста.",
                reply_markup=MAIN_KB,
            )
            return

        pending_posts[moderation_message_id] = record

        if record.get("kind") == "single":
            with contextlib.suppress(Exception):
                await bot.edit_message_reply_markup(
                    chat_id=GROUP_ID,
                    message_id=moderation_message_id,
                    reply_markup=post_action_kb(),
                )

        user_mode.pop(user_id, None)
        await primary_message.answer(
            "<b>✅ Ваш пост принят на рассмотрение</b>\n\n"
            "Хотите повторно отправить пост или обратиться в поддержку канала? "
            "Выберите необходимую опцию при помощи кнопок ниже.",
            reply_markup=MAIN_KB,
        )
        return

    if mode == "support":
        if user_id in support_banned_users:
            await primary_message.answer("🚫 Вы заблокированы в поддержке.")
            return

        try:
            copied_ids = await send_support_submission_to_topic(
                bot=bot,
                topic_id=SUP_CHAT_ID,
                user=primary_message.from_user,
                source_message=primary_message,
                bundle_messages=bundle_messages,
            )
        except TelegramBadRequest:
            logger.exception(
                "Failed to send support message to group/topic. Check GROUP_ID=%s, SUP_CHAT_ID=%s, bot membership and topic access.",
                GROUP_ID,
                SUP_CHAT_ID,
            )
            await primary_message.answer(
                "❌ Не удалось отправить сообщение в поддержку.\n"
                "Проверь, что бот добавлен в форум-группу, что GROUP_ID верный и что SUP_CHAT_ID указывает именно на тему поддержки."
            )
            return
        except Exception:
            logger.exception("Unexpected error while sending support message")
            await primary_message.answer("❌ Произошла ошибка при отправке сообщения.", reply_markup=CANCEL_KB)
            return

        for copied_id in copied_ids:
            support_message_to_user[copied_id] = user_id

        await primary_message.answer("✅ Ваше обращение было передано в поддержку.")
        return


async def process_media_group_after_delay(key: Tuple[int, str], bot: Bot) -> None:
    await asyncio.sleep(1.0)
    buffer = media_group_buffers.pop(key, None)
    if not buffer:
        return

    messages = sorted(buffer["messages"], key=lambda m: m.message_id)
    if not messages:
        return

    await process_submission_bundle(messages[0], bot, bundle_messages=messages)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_mode.pop(message.from_user.id, None)
    ad_drafts.pop(message.from_user.id, None)
    await send_main_menu(message)


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
        user_bans.pop(user_id, None)

    user_mode[user_id] = "post"
    await send_post_prompt(message)


@router.message(F.text == "📥 Поддержка")
async def enter_support_mode(message: Message) -> None:
    user_mode[message.from_user.id] = "support"
    await send_support_prompt(message)


@router.message(F.text == AD_BUTTON_TEXT)
async def enter_ad_mode(message: Message) -> None:
    user = message.from_user
    if not ad_access_allowed(user):
        await message.answer(AD_UNAVAILABLE_TEXT)
        return
    await begin_ad_flow(message, user)


@router.message(F.text == "❌ Отменить")
async def cancel_mode(message: Message) -> None:
    user_mode.pop(message.from_user.id, None)
    ad_drafts.pop(message.from_user.id, None)
    await send_main_menu(message)


@router.message(F.chat.id == GROUP_ID, F.message_thread_id == SUP_CHAT_ID)
async def handle_support_topic_messages(message: Message, bot: Bot) -> None:
    if not message.reply_to_message:
        return

    replied_id = message.reply_to_message.message_id
    user_id = support_message_to_user.get(replied_id)
    if not user_id:
        return

    response_text = (message.text or message.caption or "").strip()
    if not response_text:
        return

    if response_text.casefold() == "блок":
        support_banned_users.add(user_id)
        with contextlib.suppress(Exception):
            await message.answer("Пользователь заблокирован в поддержке.")
        return

    # Не отправляем сразу: сначала просим администратора подтвердить.
    try:
        confirm = await message.reply(
            "<b>❓ Вы точно хотите отправить этот ответ пользователю?</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Отправить", callback_data="sup:yes"),
                        InlineKeyboardButton(text="❌ Отмена", callback_data="sup:no"),
                    ]
                ]
            ),
        )
    except Exception:
        logger.exception("Failed to send support reply confirmation")
        return

    pending_support_replies[confirm.message_id] = {"user_id": user_id, "text": response_text}


@router.message(F.media_group_id)
async def handle_media_group_item(message: Message, bot: Bot) -> None:
    if get_message_kind(message) not in {"photo", "video"}:
        return

    key = (message.chat.id, message.media_group_id)
    buffer = media_group_buffers.get(key)

    if buffer is None:
        buffer = {"messages": [], "task": None}
        media_group_buffers[key] = buffer

    buffer["messages"].append(message)

    if buffer["task"] is None:
        buffer["task"] = asyncio.create_task(process_media_group_after_delay(key, bot))


@router.message()
async def handle_private_fallback(message: Message, bot: Bot) -> None:
    if message.chat.type != "private":
        return

    if message.media_group_id:
        return

    user_id = message.from_user.id
    mode = user_mode.get(user_id)

    if mode == "ad":
        await process_ad_submission(message, bot, None)
        return

    if mode in {"post", "support"}:
        if mode == "post":
            banned_until = user_bans.get(user_id)
            now_ts = int(datetime.now(timezone.utc).timestamp())
            if banned_until and banned_until > now_ts:
                remaining = banned_until - now_ts
                user_mode.pop(user_id, None)
                await message.answer(
                    f"🚫 Вы были заблокированы в предложке. Вы будете разблокированы через {format_remaining(remaining)}",
                    reply_markup=MAIN_KB,
                )
                return

            kind = get_message_kind(message)
            if kind not in {"text", "photo", "video"}:
                await message.answer(
                    "Отправьте текст, фото или видео.",
                    reply_markup=CANCEL_KB,
                )
                return

            try:
                moderation_message_id, record = await send_submission_to_topic(
                    bot=bot,
                    topic_id=POST_CHAT_ID,
                    user=message.from_user,
                    source_message=message,
                    bundle_messages=None,
                )
            except TelegramBadRequest:
                logger.exception(
                    "Failed to send post to group/topic. Check GROUP_ID=%s, POST_CHAT_ID=%s, bot membership and topic access.",
                    GROUP_ID,
                    POST_CHAT_ID,
                )
                await message.answer(
                    "❌ Не удалось отправить пост в тему.\n"
                    "Проверь, что бот добавлен в форум-группу, что GROUP_ID верный и что POST_CHAT_ID указывает именно на тему для постов."
                )
                return
            except Exception:
                logger.exception("Unexpected error while sending post to topic")
                await message.answer(
                    "❌ Произошла ошибка при отправке поста.",
                    reply_markup=MAIN_KB,
                )
                return

            pending_posts[moderation_message_id] = record

            with contextlib.suppress(Exception):
                await bot.edit_message_reply_markup(
                    chat_id=GROUP_ID,
                    message_id=moderation_message_id,
                    reply_markup=post_action_kb(),
                )

            user_mode.pop(user_id, None)
            await message.answer(
                "<b>✅ Ваш пост принят на рассмотрение</b>\n\n"
                "Хотите повторно отправить пост или обратиться в поддержку канала? "
                "Выберите необходимую опцию при помощи кнопок ниже.",
                reply_markup=MAIN_KB,
            )
            return

        if mode == "support":
            if user_id in support_banned_users:
                await message.answer("🚫 Вы заблокированы в поддержке.")
                return

            kind = get_message_kind(message)
            if kind not in {"text", "photo", "video"}:
                await message.answer(
                    "Отправьте текст, фото или видео.",
                    reply_markup=CANCEL_KB,
                )
                return

            try:
                copied_ids = await send_support_submission_to_topic(
                    bot=bot,
                    topic_id=SUP_CHAT_ID,
                    user=message.from_user,
                    source_message=message,
                    bundle_messages=None,
                )
            except TelegramBadRequest:
                logger.exception(
                    "Failed to send support message to group/topic. Check GROUP_ID=%s, SUP_CHAT_ID=%s, bot membership and topic access.",
                    GROUP_ID,
                    SUP_CHAT_ID,
                )
                await message.answer(
                    "❌ Не удалось отправить сообщение в поддержку.\n"
                    "Проверь, что бот добавлен в форум-группу, что GROUP_ID верный и что SUP_CHAT_ID указывает именно на тему поддержки."
                )
                return
            except Exception:
                logger.exception("Unexpected error while sending support message")
                await message.answer("❌ Произошла ошибка при отправке сообщения.", reply_markup=CANCEL_KB)
                return

            for copied_id in copied_ids:
                support_message_to_user[copied_id] = user_id

            await message.answer("✅ Ваше обращение было передано в поддержку.")
            return

    # Not in any mode: behave like /start
    user_mode.pop(user_id, None)
    await send_main_menu(message)


@router.callback_query(F.data == "post:accept")
async def cb_post_accept(callback: CallbackQuery, bot: Bot) -> None:
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
    # Albums use a standalone control message that never contained the post
    # body (the body/caption lives on the album items themselves), so don't
    # duplicate it there.
    include_body = record.get("kind") != "album"

    with contextlib.suppress(Exception):
        await edit_topic_message_with_status(bot, msg, status_line, body, include_body=include_body)

    with contextlib.suppress(Exception):
        await publish_post_to_channel(bot, record)

    await callback.answer("Принято")


@router.callback_query(F.data == "post:reject")
async def cb_post_reject(callback: CallbackQuery, bot: Bot) -> None:
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
    include_body = record.get("kind") != "album"

    with contextlib.suppress(Exception):
        await edit_topic_message_with_status(bot, msg, status_line, body, include_body=include_body)

    await callback.answer("Отклонено")


@router.callback_query(F.data == "post:ban_menu")
async def cb_post_ban_menu(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_posts:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    with contextlib.suppress(Exception):
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=ban_menu_kb(),
        )

    await callback.answer()


@router.callback_query(F.data == "post:back")
async def cb_post_back(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_posts:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    with contextlib.suppress(Exception):
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=post_action_kb(),
        )

    await callback.answer()


@router.callback_query(F.data.startswith("ban:"))
async def cb_post_ban_duration(callback: CallbackQuery, bot: Bot) -> None:
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
    include_body = record.get("kind") != "album"

    with contextlib.suppress(Exception):
        await edit_topic_message_with_status(bot, msg, status_line, record["body"], include_body=include_body)

    await callback.answer(f"Пользователь заблокирован на {label}")


# ---------------------------------------------------------------------------
# Подтверждение ответов поддержки
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "sup:yes")
async def cb_support_reply_yes(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    record = pending_support_replies.pop(msg.message_id, None)
    if not record:
        await callback.answer("Уже обработано", show_alert=True)
        return

    escaped = html.escape(record["text"])
    try:
        await bot.send_message(
            chat_id=record["user_id"],
            text=f"<b>💬 Вы получили ответ от поддержки</b>\n\n<i>{escaped}</i>",
        )
    except Exception:
        logger.exception("Failed to send support reply to user_id=%s", record["user_id"])
        with contextlib.suppress(Exception):
            await bot.edit_message_text(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                text="⚠️ Не удалось доставить ответ пользователю (возможно, он заблокировал бота).",
                reply_markup=None,
            )
        await callback.answer("Не удалось отправить", show_alert=True)
        return

    admin_link = admin_mention_html(callback.from_user)
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=f"✅ Ответ отправлен пользователю. Подтвердил: {admin_link}",
            reply_markup=None,
            link_preview_options=NO_PREVIEW,
        )
    await callback.answer("Отправлено")


@router.callback_query(F.data == "sup:no")
async def cb_support_reply_no(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    record = pending_support_replies.pop(msg.message_id, None)
    if not record:
        await callback.answer("Уже обработано", show_alert=True)
        return

    admin_link = admin_mention_html(callback.from_user)
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=f"❌ Отправка ответа отменена: {admin_link}",
            reply_markup=None,
            link_preview_options=NO_PREVIEW,
        )
    await callback.answer("Отменено")


# ---------------------------------------------------------------------------
# Реклама: кнопки пользователя
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adu:"))
async def cb_ad_user(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    user = callback.from_user
    if not msg:
        await callback.answer()
        return

    if not ad_access_allowed(user):
        await callback.answer(AD_UNAVAILABLE_TEXT, show_alert=True)
        return

    action = callback.data.split(":", 1)[1]

    if action == "rules":
        await msg.answer(AD_RULES_TEXT)
        await callback.answer()
        return

    if action == "restart":
        with contextlib.suppress(Exception):
            await bot.edit_message_reply_markup(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                reply_markup=None,
            )
        await begin_ad_flow(msg, user)
        await callback.answer()
        return

    draft = ad_drafts.get(user.id)
    if not draft or draft.get("step_msg_id") != msg.message_id:
        await callback.answer(
            "Это сообщение устарело. Нажмите «📣 Выложить свою рекламу» и начните заново.",
            show_alert=True,
        )
        return

    if action == "agree":
        ad_rules_agreed.add(user.id)
        await edit_ad_message(bot, msg, ad_options_text(draft), ad_options_kb(draft))

    elif action == "link":
        draft["link"] = not draft.get("link")
        await edit_ad_message(bot, msg, ad_options_text(draft), ad_options_kb(draft))

    elif action == "pin1":
        draft["pin_days"] = 0 if draft.get("pin_days") == 1 else 1
        await edit_ad_message(bot, msg, ad_options_text(draft), ad_options_kb(draft))

    elif action == "pin3":
        draft["pin_days"] = 0 if draft.get("pin_days") == 3 else 3
        await edit_ad_message(bot, msg, ad_options_text(draft), ad_options_kb(draft))

    elif action == "confirm":
        await edit_ad_message(bot, msg, ad_pay_text(draft), ad_pay_kb())

    elif action == "back_opts":
        await edit_ad_message(bot, msg, ad_options_text(draft), ad_options_kb(draft))

    elif action.startswith("bank:"):
        bank_key = action.split(":", 1)[1]
        if bank_key not in AD_CARDS:
            await callback.answer("Ошибка", show_alert=True)
            return
        await edit_ad_message(bot, msg, ad_bank_text(draft, bank_key), ad_bank_kb())

    elif action == "back_pay":
        await edit_ad_message(bot, msg, ad_pay_text(draft), ad_pay_kb())

    elif action == "paid":
        ad_drafts.pop(user.id, None)
        try:
            admin_message_id, record = await send_ad_to_topic(bot, draft)
        except Exception:
            logger.exception(
                "Failed to send ad to moderation topic. Check GROUP_ID=%s, POST_CHAT_ID=%s",
                GROUP_ID,
                POST_CHAT_ID,
            )
            ad_drafts[user.id] = draft
            await callback.answer("❌ Не удалось отправить пост на проверку. Попробуйте ещё раз чуть позже.", show_alert=True)
            return

        pending_ads[admin_message_id] = record
        await edit_ad_message(bot, msg, AD_SENT_TEXT, None)
        await notify_admins_about_ad(bot, admin_message_id)

    await callback.answer()


# ---------------------------------------------------------------------------
# Реклама: кнопки администраторов
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:accept")
async def cb_ad_accept(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    record = pending_ads.pop(msg.message_id, None)
    if not record:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    if not AD_CHANNEL_ID:
        pending_ads[msg.message_id] = record
        await callback.answer("❌ TEST_CHANNEL_ID не задан в настройках Render.", show_alert=True)
        return

    try:
        channel_message_id = await publish_ad_to_channel(bot, record)
    except Exception:
        logger.exception("Failed to publish ad to AD_CHANNEL_ID=%s", AD_CHANNEL_ID)
        pending_ads[msg.message_id] = record
        await callback.answer(
            "❌ Не удалось опубликовать в канал. Проверьте TEST_CHANNEL_ID и права бота.",
            show_alert=True,
        )
        return

    pin_days = record.get("pin_days", 0)
    pinned = False
    pin_failed = False
    if pin_days:
        try:
            await bot.pin_chat_message(
                chat_id=AD_CHANNEL_ID,
                message_id=channel_message_id,
                disable_notification=True,
            )
            schedule_unpin(bot, AD_CHANNEL_ID, channel_message_id, pin_days)
            pinned = True
        except Exception:
            logger.exception("Failed to pin ad message_id=%s in AD_CHANNEL_ID=%s", channel_message_id, AD_CHANNEL_ID)
            pin_failed = True

    status_line = f"✅ Принято: {admin_mention_html(callback.from_user)}"
    if pin_failed:
        status_line += "\n⚠️ Не удалось закрепить пост (проверьте право бота «Закреплять сообщения»)."
    with contextlib.suppress(Exception):
        await edit_ad_admin_message(bot, msg, record, status_line)

    user_text = "<b>✅ Ваш рекламный пост принят</b>\n\nОн опубликован в канале."
    if pinned:
        user_text += f"\n📌 Пост закреплён на {pin_days} {'день' if pin_days == 1 else 'дня'}."
    with contextlib.suppress(Exception):
        await bot.send_message(chat_id=record["user_id"], text=user_text)

    await callback.answer("Принято")


@router.callback_query(F.data == "adm:reject_menu")
async def cb_ad_reject_menu(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_ads:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    with contextlib.suppress(Exception):
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=ad_reject_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "adm:back")
async def cb_ad_back(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_ads:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    with contextlib.suppress(Exception):
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=ad_action_kb(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:rej:"))
async def cb_ad_reject_reason(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    reason = callback.data.split(":", 2)[2]
    if reason not in AD_REJECT_REASONS:
        await callback.answer("Ошибка", show_alert=True)
        return

    record = pending_ads.pop(msg.message_id, None)
    if not record:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    status_line = f"❌ Отклонено ({AD_REJECT_REASONS[reason]}): {admin_mention_html(callback.from_user)}"
    with contextlib.suppress(Exception):
        await edit_ad_admin_message(bot, msg, record, status_line)

    with contextlib.suppress(Exception):
        await bot.send_message(chat_id=record["user_id"], text=ad_reject_user_text(reason, record))

    await callback.answer("Отклонено")


@router.callback_query(F.data == "adm:ban_menu")
async def cb_ad_ban_menu(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    if msg.message_id not in pending_ads:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    with contextlib.suppress(Exception):
        await bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reply_markup=ban_menu_kb(prefix="adm:ban:", back_data="adm:back"),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:ban:"))
async def cb_ad_ban_duration(callback: CallbackQuery, bot: Bot) -> None:
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    try:
        seconds = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Ошибка", show_alert=True)
        return

    record = pending_ads.pop(msg.message_id, None)
    if not record:
        await callback.answer("Пост уже обработан", show_alert=True)
        return

    user_bans[record["user_id"]] = int(datetime.now(timezone.utc).timestamp()) + seconds

    label = BAN_LABEL_BY_SECONDS.get(seconds, "время")
    status_line = f"🚫 Бан на {label}: {admin_mention_html(callback.from_user)}"
    with contextlib.suppress(Exception):
        await edit_ad_admin_message(bot, msg, record, status_line)

    await callback.answer(f"Пользователь заблокирован на {label}")


async def main() -> None:
    logger.info(
        "Starting bot with GROUP_ID=%s POST_CHAT_ID=%s SUP_CHAT_ID=%s CHANNEL_ID=%s",
        GROUP_ID,
        POST_CHAT_ID,
        SUP_CHAT_ID,
        CHANNEL_ID,
    )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    dp.include_router(router)

    runner = await start_web_server()
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
