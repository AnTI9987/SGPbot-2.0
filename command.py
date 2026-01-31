# command.py
# Delegated handlers for /info (and text variants) and for разбан.
# Also: /info toggle behaviour, background sync of "Репутация: N" titles,
# and helper to update a single user's title if present.
#
# This module expects to be imported at runtime by bot.py handlers.
# It imports `bot` (your main module) at runtime; that is safe because bot.py
# imports this module dynamically inside handlers.

import os
import asyncio
from typing import Optional

from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

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
    if lang == "uk":
        txt = "👀 Сховати репутацію" if has_title else "👀 Відобразити репутацію"
    else:
        txt = "👀 Скрыть репутацию" if has_title else "👀 Отобразить репутацию"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=txt, callback_data=f"cmd_toggle_rep:{user_id}")]
    ])
    return kb

# ---------------- Main exported function ----------------
async def handle_info(message: Message):
    """
    Handle /info and text variants.
    Allowed in private chats and groups (per requirement).
    Sends info card and attaches toggle button (callback_data 'cmd_toggle_rep:<id>').
    """
    # import host module (bot.py) at runtime
    try:
        import bot as main_mod
    except Exception:
        # fallback: minimal reply so caller sees something
        try:
            await message.reply("Инфо временно недоступно.")
        except Exception:
            pass
        return

    user = message.from_user
    if not user:
        return

    # ensure user in DB
    try:
        await main_mod.ensure_user_row(user.id)
    except Exception:
        pass

    # fetch row
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

    # Prefer to use main_mod.info_card_text if available for consistent wording
    try:
        info_text = main_mod.info_card_text(lang, user, rep, accepted, has_title)
    except Exception:
        # fallback: build a simple HTML text
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

    try:
        await message.answer(info_text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        try:
            await message.reply(info_text, parse_mode="HTML")
        except Exception:
            pass

# ---------------- Callback handler for toggle ----------------
async def _cb_toggle_rep(call: CallbackQuery):
    """
    Callback data: cmd_toggle_rep:<user_id>
    Only the owner of the info card can press. If not owner -> show "🦶 Жулик, не нажимай."
    """
    data = call.data or ""
    parts = data.split(":", 1)
    if len(parts) < 2:
        await call.answer("Ошибка", show_alert=True)
        return
    try:
        target_id = int(parts[1])
    except Exception:
        await call.answer("Ошибка", show_alert=True)
        return

    # If not the author -> show requested message
    if call.from_user.id != target_id:
        try:
            await call.answer("🦶 Жулик, не нажимай.", show_alert=True)
        except Exception:
            pass
        return

    # import main module
    try:
        import bot as main_mod
    except Exception:
        await call.answer("Внутренняя ошибка", show_alert=True)
        return

    # get user DB row
    try:
        row = await main_mod.get_user(target_id)
    except Exception:
        row = None

    lang = (row["lang"] if row and "lang" in row and row["lang"] else "ru")
    rep = (row["reputation"] if row and "reputation" in row else 0)

    # CHAT_ID
    chat_id_env = os.getenv("CHAT_ID")
    try:
        chat_id = int(chat_id_env) if chat_id_env is not None else None
    except Exception:
        chat_id = None

    # check if user currently has title (use helper)
    has_title = False
    try:
        has_title = await main_mod.has_rep_title(main_mod.bot, target_id)
    except Exception:
        # fallback to direct check
        if chat_id is not None:
            try:
                member = await call.bot.get_chat_member(chat_id, target_id)
                ct = getattr(member, "custom_title", None)
                if ct and isinstance(ct, str) and ct.startswith("Репутация:"):
                    has_title = True
            except Exception:
                has_title = False

    # Toggle behaviour
    if not has_title:
        # show (add)
        if rep < 25:
            if lang == "uk":
                await call.answer("❌ Ви не можете відобразити свою репутацію, якщо у Вас менше 25 балів репутації", show_alert=True)
            else:
                await call.answer("❌ Вы не можете отобразить свою репутацию если у Вас меньше 25-ти балов репутации", show_alert=True)
            return
        if chat_id is None:
            await call.answer("Ошибка: CHAT_ID не настроен.", show_alert=True)
            return
        try:
            # try to reuse main_mod helper if exists
            try:
                ok = await main_mod.grant_rep_title_bot_admin(main_mod.bot, target_id, rep)
                if ok:
                    if lang == "uk":
                        await call.answer("➕ Ви встановили відображення репутації поруч зі своїм нікнеймом.", show_alert=True)
                    else:
                        await call.answer("➕ Вы установили отображение репутации рядом со своим никнеймом.", show_alert=True)
                    return
            except Exception:
                # fallback to direct promote + set custom title
                pass

            # direct fallback
            await call.bot.promote_chat_member(
                chat_id=chat_id,
                user_id=target_id,
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
            await call.bot.set_chat_administrator_custom_title(chat_id=chat_id, user_id=target_id, custom_title=f"Репутация: {rep}")
            if lang == "uk":
                await call.answer("➕ Ви встановили відображення репутації поруч зі своїм нікнейком.", show_alert=True)
            else:
                await call.answer("➕ Вы установили отображение репутации рядом со своим никнеймом.", show_alert=True)
        except Exception:
            if lang == "uk":
                await call.answer("Ошибка при установке отображения репутации. Проверьте права бота.", show_alert=True)
            else:
                await call.answer("Ошибка при установке отображения репутации. Проверьте права бота.", show_alert=True)
        return
    else:
        # remove title
        if chat_id is None:
            await call.answer("Ошибка: CHAT_ID не настроен.", show_alert=True)
            return
        try:
            # try to reuse main_mod helper if exists
            try:
                ok = await main_mod.remove_rep_title_and_demote(main_mod.bot, target_id)
                if ok:
                    if lang == "uk":
                        await call.answer("➖ Приписка з вашою репутацією була видалена з відображення поруч із вашим нікнейком.", show_alert=True)
                    else:
                        await call.answer("➖ Преписка с вашей репутацией была убрана из отображения рядом с вашим никнеймом.", show_alert=True)
                    return
            except Exception:
                # fallback to direct attempts
                pass

            # direct fallback: try to clear custom title, else promote with no rights
            try:
                await call.bot.set_chat_administrator_custom_title(chat_id=chat_id, user_id=target_id, custom_title="")
            except Exception:
                await call.bot.promote_chat_member(
                    chat_id=chat_id,
                    user_id=target_id,
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
            if lang == "uk":
                await call.answer("➖ Приписка з вашою репутацією була видалена з відображення поруч із вашим нікнейком.", show_alert=True)
            else:
                await call.answer("➖ Преписка с вашей репутацией была убрана из отображения рядом с вашим никнеймом.", show_alert=True)
        except Exception:
            if lang == "uk":
                await call.answer("Не вдалося прибрати позначку. Перевірте права бота.", show_alert=True)
            else:
                await call.answer("Не удалось убрать приписку. Проверьте права бота.", show_alert=True)
        return

# ---------------- Unban handler (callable) ----------------
async def handle_razban(message: Message):
    """
    Handle 'разбан' text (разбан, /разбан, razban).
    Must only work in group with id PREDLOJKA_ID (enforced here).
    This function is intended to be called from bot.py's handler.
    """
    # import main module
    try:
        import bot as main_mod
    except Exception:
        # fallback minimal processing: reply error
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

# ---------------- Background sync: update "Репутация: N" titles ----------------
async def _rep_title_sync_loop():
    """
    Periodically (every 60s) scan admins in CHAT_ID and update custom_title if it starts with "Репутация:"
    This makes titles reflect the current reputation automatically.
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

    while True:
        try:
            try:
                admins = await BOT.get_chat_administrators(chat_id)
            except Exception:
                admins = []
            for member in admins:
                try:
                    ct = getattr(member, "custom_title", None)
                    if ct and isinstance(ct, str) and ct.startswith("Репутация:"):
                        uid = member.user.id
                        try:
                            row = await main_mod.get_user(uid)
                        except Exception:
                            row = None
                        rep = (row["reputation"] if row and "reputation" in row else 0)
                        desired = f"Репутация: {rep}"
                        if ct != desired:
                            try:
                                await BOT.set_chat_administrator_custom_title(chat_id=chat_id, user_id=uid, custom_title=desired)
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(60)

# ---------------- Utility export: update single user title ----------------
async def update_rep_title_if_present(user_id: int):
    """
    If user has custom_title 'Репутация: ...' in CHAT_ID, update it to current reputation.
    Can be called from bot immediately after changing reputation.
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

# ---------------- Registration and background startup ----------------
# Try to register callback for cmd_toggle_rep and start background loop if main bot is available.
try:
    import bot as main_mod  # type: ignore
    dp = getattr(main_mod, "dp", None)
    if dp is not None:
        try:
            # register callback: aiogram v3 style
            dp.callback_query.register(_cb_toggle_rep, lambda c: c.data and c.data.startswith("cmd_toggle_rep:"))
        except Exception:
            # fallback: ignore registration problems
            pass

    # Try to start background loop: schedule a task if event loop is running
    BOT = getattr(main_mod, "bot", None)
    if BOT is not None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_rep_title_sync_loop())
            else:
                # if loop not running yet, schedule call_soon to start sync when loop starts
                def _schedule():
                    try:
                        asyncio.create_task(_rep_title_sync_loop())
                    except Exception:
                        pass
                try:
                    loop.call_soon(_schedule)
                except Exception:
                    pass
        except Exception:
            pass
except Exception:
    # cannot import the main module at import time; bot.py will import this module dynamically inside handlers.
    pass

# Exports
__all__ = ("handle_info", "handle_razban", "update_rep_title_if_present")
