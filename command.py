# command.py
# Delegated handlers for /info (and text variants) and for разбан.
# Designed to be imported dynamically by bot.py handlers (no top-level import of bot).
# Uses runtime imports of bot.py inside functions to avoid circular imports.

import os
import asyncio
from typing import Optional

from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery

# ---------------- Helpers ----------------
def escape_html(text: str) -> str:
    if text is None:
        return ""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def user_openmessage_link(user: types.User) -> str:
    """Return HTML anchor with user's full name linking to tg://openmessage?user_id=..."""
    name = user.full_name or str(user.id)
    return f'<a href="tg://openmessage?user_id={user.id}">{escape_html(name)}</a>'

def build_info_kb(lang: str, user_id: int, has_title: bool) -> InlineKeyboardMarkup:
    # Use callback_data 'toggle_rep:<id>' so bot.py's toggle handler catches it
    if lang == "uk":
        txt = "👀 Сховати репутацію" if has_title else "👀 Відобразити репутацію"
    else:
        txt = "👀 Скрыть репутацию" if has_title else "👀 Отобразить репутацию"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=txt, callback_data=f"toggle_rep:{user_id}")]
    ])
    return kb

# ---------------- Main exported function ----------------
async def handle_info(message: Message):
    """
    Handle /info and its text variants.
    Allowed in private chats and groups (per requirement).
    Sends info card and attaches toggle button (callback_data 'toggle_rep:<id>').
    """
    # import host module (bot.py) at runtime to avoid circular import
    try:
        import bot as main_mod
    except Exception:
        try:
            await message.reply("Инфо временно недоступно.")
        except Exception:
            pass
        return

    user = message.from_user
    if not user:
        return

    # ensure user row exists
    try:
        await main_mod.ensure_user_row(user.id)
    except Exception:
        pass

    # fetch user row if possible
    try:
        row = await main_mod.get_user(user.id)
    except Exception:
        row = None

    lang = (row["lang"] if row and "lang" in row and row["lang"] else "ru")
    rep = (row["reputation"] if row and "reputation" in row else 0)
    accepted = (row["accepted_count"] if row and "accepted_count" in row else 0)

    # Determine whether user currently has "Репутация:" custom title in CHAT_ID
    chat_id_env = os.getenv("CHAT_ID")
    try:
        chat_id = int(chat_id_env) if chat_id_env is not None else None
    except Exception:
        chat_id = None

    has_title = False
    if chat_id is not None:
        try:
            member = await main_mod.bot.get_chat_member(chat_id, user.id)
            custom_title = getattr(member, "custom_title", None)
            if custom_title and isinstance(custom_title, str) and custom_title.startswith("Репутация:"):
                has_title = True
        except Exception:
            has_title = False

    # Prefer using main_mod.info_card_text / info_card_kb if present for consistent wording
    info_text = None
    kb = None
    try:
        # if bot.py exposes these helpers, use them
        info_text = main_mod.info_card_text(lang, user, rep, accepted, has_title)
        kb = main_mod.info_card_kb(lang, user.id, has_title)
    except Exception:
        # fallback: build simple text + keyboard
        if lang == "uk":
            header = f"📊 Статистика по постам {user_openmessage_link(user)}"
            body = f"\n\n🆙 Ваша репутація: {rep}\n✅ Прийнятих постів: {accepted}\n\n"
            body += ("Натисніть кнопку нижче, щоб сховати відображення своєї репутації поруч з нікнейком"
                     if has_title else "Натисніть кнопку нижче, щоб встановити відображення своєї репутації поруч з нікнейком")
        else:
            header = f"📊 Статистика по постам {user_openmessage_link(user)}"
            body = f"\n\n🆙 Ваша репутация: {rep}\n✅ Принятых постов: {accepted}\n\n"
            body += ("Нажмите кнопку ниже, чтобы скрыть отображение своей репутации рядом с никнеймом"
                     if has_title else "Нажмите кнопку ниже, чтобы установить отображение своей репутации рядом с никнейком")
        info_text = header + "\n" + body
        kb = build_info_kb(lang, user.id, has_title)

    # send response
    try:
        await message.answer(info_text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        try:
            await message.reply(info_text, parse_mode="HTML")
        except Exception:
            pass

# ---------------- Unban handler (callable) ----------------
async def handle_razban(message: Message):
    """
    Handle 'разбан' text (разбан, /разбан, razban).
    Must only work in group with id PREDLOJKA_ID (enforced here).
    Intended to be called from bot.py's handler.
    """
    try:
        import bot as main_mod
    except Exception:
        try:
            await message.reply("Внутренняя ошибка: модуль бота не доступен.")
        except Exception:
            pass
        return

    chat = getattr(message, "chat", None)
    if chat is None:
        return

    pred_id = main_mod.PREDLOJKA_ID
    if pred_id is None:
        try:
            await message.reply("PREDLOJKA_ID не настроен на сервере. Операция невозможна.")
        except Exception:
            pass
        return

    if chat.id != pred_id:
        try:
            await message.reply("Команда разбан доступна только в группе предложки.")
        except Exception:
            pass
        return

    if not message.text:
        return

    text = message.text.strip()
    parts = text.split(None, 1)
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
            chatinfo = await main_mod.bot.get_chat(target)
            target_id = chatinfo.id
        except Exception:
            target_id = None
    else:
        try:
            target_id = int(target)
        except Exception:
            try:
                chatinfo = await main_mod.bot.get_chat("@" + target)
                target_id = chatinfo.id
            except Exception:
                target_id = None

    if target_id is None:
        try:
            await message.reply("Не удалось определить пользователя. Укажите корректный @юзернейм или числовой ID.")
        except Exception:
            pass
        return

    try:
        await main_mod.set_banned_until(target_id, 0)
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
        await main_mod.bot.send_message(target_id, "Вас разбанили в системе предложений постов. Вы снова можете предлагать посты.")
    except Exception:
        pass

# ---------------- Utility export: update single user title ----------------
async def update_rep_title_if_present(user_id: int):
    """
    If user has custom_title 'Репутация: ...' in CHAT_ID, update it to current reputation.
    Can be called from bot.py immediately after changing reputation.
    """
    try:
        import bot as main_mod
    except Exception:
        return
    BOT = getattr(main_mod, "bot", None)
    if BOT is None:
        return
    chat_id_env = os.getenv("CHAT_ID")
    try:
        chat_id = int(chat_id_env) if chat_id_env is not None else None
    except Exception:
        chat_id = None
    if chat_id is None:
        return
    try:
        member = await BOT.get_chat_member(chat_id, user_id)
        ct = getattr(member, "custom_title", None)
        if ct and isinstance(ct, str) and ct.startswith("Репутация:"):
            try:
                row = await main_mod.get_user(user_id)
            except Exception:
                row = None
            rep = (row["reputation"] if row and "reputation" in row else 0)
            desired = f"Репутация: {rep}"
            if ct != desired:
                try:
                    await BOT.set_chat_administrator_custom_title(chat_id=chat_id, user_id=user_id, custom_title=desired)
                except Exception:
                    pass
    except Exception:
        pass

# Exports
__all__ = ("handle_info", "handle_razban", "update_rep_title_if_present")
