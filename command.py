# command.py
# Handlers for /info and reputation display toggle, plus unban command handling inside the PREDLOJKA group.
# Requires same DATABASE_URL as bot_new.py. Uses its own DB pool.

import os
import asyncpg
import asyncio
from typing import Optional

from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

DATABASE_URL = os.getenv("DATABASE_URL")
CHAT_ID_RAW = os.getenv("CHAT_ID")
PREDLOJKA_ID_RAW = os.getenv("PREDLOJKA_ID")
try:
    CHAT_ID = int(CHAT_ID_RAW) if CHAT_ID_RAW is not None else None
except Exception:
    CHAT_ID = None
try:
    PREDLOJKA_ID = int(PREDLOJKA_ID_RAW) if PREDLOJKA_ID_RAW is not None else None
except Exception:
    PREDLOJKA_ID = None

_db_pool: Optional[asyncpg.pool.Pool] = None
_db_pool_lock = asyncio.Lock()

async def _get_db_pool():
    global _db_pool
    if _db_pool is None:
        async with _db_pool_lock:
            if _db_pool is None:
                if not DATABASE_URL:
                    raise RuntimeError("DATABASE_URL is not set for command.py")
                _db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
                # ensure column exists
                async with _db_pool.acquire() as conn:
                    try:
                        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS show_rep BOOLEAN DEFAULT FALSE")
                    except Exception:
                        pass
    return _db_pool

# DB helpers
async def _ensure_user_row_db(user_id: int):
    pool = await _get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )

async def _get_user_from_db(user_id: int) -> Optional[dict]:
    pool = await _get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, lang, reputation, accepted_count, show_rep, banned_until FROM users WHERE user_id = $1",
            user_id,
        )
        if not row:
            return None
        return {
            "user_id": row["user_id"],
            "lang": row["lang"] or "ru",
            "reputation": int(row["reputation"] or 0),
            "accepted_count": int(row["accepted_count"] or 0),
            "show_rep": bool(row["show_rep"]),
            "banned_until": int(row["banned_until"] or 0),
        }

async def _set_show_rep_db(user_id: int, value: bool):
    pool = await _get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET show_rep = $1 WHERE user_id = $2", value, user_id)

async def _set_banned_until_db(user_id: int, until_ts: int):
    pool = await _get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET banned_until = $1 WHERE user_id = $2", until_ts, user_id)

# Utilities
def _escape_html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def _user_link_html(user_id: int, full_name: str) -> str:
    return f'<a href="tg://openmessage?user_id={user_id}">{_escape_html(full_name)}</a>'

def _make_toggle_kb(showing: bool, lang: str, target_user_id: int) -> InlineKeyboardMarkup:
    if lang == "uk":
        if not showing:
            b_label = "👀 Відобразити репутацію"
        else:
            b_label = "👀 Сховати репутацію"
    else:
        if not showing:
            b_label = "👀 Отобразить репутацию"
        else:
            b_label = "👀 Скрыть репутацию"

    action = "hide" if showing else "show"
    cb = f"rep_toggle:{action}:{target_user_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b_label, callback_data=cb)]
    ])
    return kb

# Public API
async def info_cmd(message: types.Message):
    """
    Call this when user executes /info or text variants.
    Sends localized stats and a button. Button is only valid for the shown user.
    """
    user = message.from_user
    uid = user.id

    try:
        await _ensure_user_row_db(uid)
    except Exception:
        pass

    urow = await _get_user_from_db(uid) or {"lang": "ru", "reputation": 0, "accepted_count": 0, "show_rep": False}
    lang = urow.get("lang", "ru")
    reputation = urow.get("reputation", 0)
    accepted = urow.get("accepted_count", 0)
    show_rep = bool(urow.get("show_rep", False))

    full_name = user.full_name or str(uid)
    user_link = _user_link_html(uid, full_name)

    if lang == "uk":
        text = (
            f"**📊 Статистика по постам {user_link}**\n\n"
            f"🆙 Ваша репутація: {reputation}\n"
            f"✅ Прийнятих постів: {accepted}\n\n"
            "Натисніть кнопку нижче, щоб встановити відображення своєї репутації поруч з нікнеймом"
        )
    else:
        text = (
            f"**📊 Статистика по постам {user_link}**\n\n"
            f"🆙 Ваша репутация: {reputation}\n"
            f"✅ Принятых постов: {accepted}\n\n"
            "Нажмите кнопку ниже, чтобы установить отображение своей репутации рядом с никнеймом"
        )

    kb = _make_toggle_kb(showing=show_rep, lang=lang, target_user_id=uid)

    try:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        try:
            await message.reply(text)
        except Exception:
            pass

async def toggle_rep_cb(call: CallbackQuery):
    """
    Handles callback_data rep_toggle:<show|hide>:<target_user_id>
    Only the target_user_id can press the button; others get an alert.
    """
    data = (call.data or "")
    parts = data.split(":")
    if len(parts) != 3:
        await call.answer("Неверные данные.", show_alert=True)
        return
    _, action, target_raw = parts
    try:
        target_id = int(target_raw)
    except Exception:
        await call.answer("Неверные данные.", show_alert=True)
        return

    pressing_id = call.from_user.id
    # disallow others pressing
    if pressing_id != target_id:
        # localized message: choose RU if unknown
        try:
            target_row = await _get_user_from_db(target_id)
            target_lang = target_row["lang"] if target_row else "ru"
        except Exception:
            target_lang = "ru"
        if target_lang == "uk":
            await call.answer("Це не ваша кнопка.", show_alert=True)
        else:
            await call.answer("Это не ваша кнопка.", show_alert=True)
        return

    # proceed for the owner
    await _ensure_user_row_db(target_id)
    urow = await _get_user_from_db(target_id) or {"lang": "ru", "reputation": 0, "show_rep": False}
    lang = urow.get("lang", "ru")
    reputation = urow.get("reputation", 0)

    if action == "show":
        # threshold
        if reputation < 25:
            if lang == "uk":
                await call.answer("❌ Ви не можете відобразити свою репутацію, якщо у Вас менше 25 балів репутації", show_alert=True)
            else:
                await call.answer("❌ Вы не можете отобразить свою репутацию если у Вас меньше 25-ти балов репутации", show_alert=True)
            return

        # set DB flag
        try:
            await _set_show_rep_db(target_id, True)
        except Exception:
            pass

        # attempt to promote (best-effort) and set custom title
        try:
            if CHAT_ID is not None:
                await call.bot.promote_chat_member(
                    chat_id=CHAT_ID,
                    user_id=target_id,
                    can_change_info=False,
                    can_post_messages=False,
                    can_edit_messages=False,
                    can_delete_messages=False,
                    can_invite_users=False,
                    can_restrict_members=False,
                    can_pin_messages=False,
                    can_promote_members=False,
                    can_manage_voice_chats=False,
                    is_anonymous=False,
                )
                title = f"Репутация: {reputation}" if lang != "uk" else f"Репутація: {reputation}"
                try:
                    await call.bot.set_chat_administrator_custom_title(chat_id=CHAT_ID, user_id=target_id, custom_title=title)
                except Exception:
                    pass
        except Exception:
            pass

        if lang == "uk":
            await call.answer("➕ Ви встановили відображення репутації поруч зі своїм нікнеймом.", show_alert=True)
        else:
            await call.answer("➕ Вы установили отображение репутации рядом со своим никнеймом.", show_alert=True)

        # update inline keyboard on the info message to "hide"
        try:
            kb = _make_toggle_kb(showing=True, lang=lang, target_user_id=target_id)
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    elif action == "hide":
        try:
            await _set_show_rep_db(target_id, False)
        except Exception:
            pass

        # try to remove custom title and demote (best-effort)
        if CHAT_ID is not None:
            try:
                try:
                    await call.bot.set_chat_administrator_custom_title(chat_id=CHAT_ID, user_id=target_id, custom_title="")
                except Exception:
                    pass
                try:
                    await call.bot.promote_chat_member(
                        chat_id=CHAT_ID,
                        user_id=target_id,
                        can_change_info=False,
                        can_post_messages=False,
                        can_edit_messages=False,
                        can_delete_messages=False,
                        can_invite_users=False,
                        can_restrict_members=False,
                        can_pin_messages=False,
                        can_promote_members=False,
                        can_manage_voice_chats=False,
                        is_anonymous=False,
                    )
                except Exception:
                    pass
            except Exception:
                pass

        if lang == "uk":
            await call.answer("➖ Приписка з вашою репутацією була видалена з відображення поруч із вашим нікнеймом.", show_alert=True)
        else:
            await call.answer("➖ Преписка с вашей репутацией была убрана из отображения рядом с вашим никнеймом.", show_alert=True)

        # update inline keyboard to "show"
        try:
            kb = _make_toggle_kb(showing=False, lang=lang, target_user_id=target_id)
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    else:
        await call.answer("Неизвестное действие.", show_alert=True)
        return

# ensure_update_custom_title: to be called by bot_new.py after reputation changes
async def ensure_update_custom_title(bot: types.Bot, user_id: int):
    """
    Call this after reputation changed in DB.
    If user has show_rep==True, attempts to set/update custom admin title in CHAT_ID to "Репутация: N" / "Репутація: N".
    """
    try:
        urow = await _get_user_from_db(user_id)
    except Exception:
        urow = None

    if not urow:
        return

    if not urow.get("show_rep", False):
        return

    if CHAT_ID is None:
        return

    lang = urow.get("lang", "ru")
    reputation = int(urow.get("reputation", 0) or 0)
    title = f"Репутация: {reputation}" if lang != "uk" else f"Репутація: {reputation}"

    try:
        try:
            await bot.promote_chat_member(
                chat_id=CHAT_ID,
                user_id=user_id,
                can_change_info=False,
                can_post_messages=False,
                can_edit_messages=False,
                can_delete_messages=False,
                can_invite_users=False,
                can_restrict_members=False,
                can_pin_messages=False,
                can_promote_members=False,
                can_manage_voice_chats=False,
                is_anonymous=False,
            )
        except Exception:
            pass
        try:
            await bot.set_chat_administrator_custom_title(chat_id=CHAT_ID, user_id=user_id, custom_title=title)
        except Exception:
            pass
    except Exception:
        pass

# handle unban message in group (this function will be called by bot_new.py router)
async def handle_unban_in_group(message: types.Message):
    # This handler only processes commands like "разбан <id_or_username>" in the PREDLOJKA_ID group
    if message.chat is None or PREDLOJKA_ID is None:
        return
    if message.chat.id != PREDLOJKA_ID:
        return
    if not message.text:
        return
    text = message.text.strip()
    # accept both "разбан 123" and "/разбан 123" and latin "razban"
    if not (text.startswith("разбан ") or text.startswith("/разбан ") or text.startswith("razban ") or text.startswith("/razban ")):
        return
    parts = text.split(None, 1)
    if len(parts) < 2:
        await message.reply("Укажите пользователя по @юзернейму или ID. Пример: разбан 123456789")
        return
    target = parts[1].strip()
    # resolve target id
    target_id = None
    if target.startswith("@"):
        try:
            chat = await message.bot.get_chat(target)
            target_id = chat.id
        except Exception:
            target_id = None
    else:
        # try parse int
        try:
            target_id = int(target)
        except Exception:
            # fallback: maybe a username without @
            try:
                chat = await message.bot.get_chat("@" + target)
                target_id = chat.id
            except Exception:
                target_id = None
    if target_id is None:
        await message.reply("Не удалось определить пользователя. Укажите корректный @юзернейм или числовой ID.")
        return
    # perform unban in DB
    try:
        await _set_banned_until_db(target_id, 0)
    except Exception:
        await message.reply("Ошибка при записи в базу. Попробуйте позже.")
        return
    # notify in group
    await message.reply(f"Пользователь {target} (ID {target_id}) разбанен в предложке.")
    # try to notify user
    try:
        await message.bot.send_message(target_id, "Вас разбанили в системе предложений постов. Вы снова можете предлагать посты.")
    except Exception:
        pass
