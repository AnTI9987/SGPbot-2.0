# command.py
# Handlers for /info (and text variants), toggle rep display and unban command.
# Additional feature: background sync of "Репутация: N" titles for admins in CHAT_ID.
# If non-author presses the info-card button -> show alert "🦶 Жулик, не нажимай."

import os
import asyncio
from typing import Optional

from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
async def info_cmd(message: types.Message):
    """
    Send info card for the user who invoked the command.
    Called from bot_new: await command.info_cmd(message)
    """
    # import host module to use DB helpers (bot_new)
    try:
        import bot_new as main_mod
    except Exception:
        try:
            await message.reply("инфо")
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

    try:
        row = await main_mod.get_user(user.id)
    except Exception:
        row = None

    lang = (row["lang"] if row and "lang" in row and row["lang"] else "ru")
    rep = (row["reputation"] if row and "reputation" in row else 0)
    accepted = (row["accepted_count"] if row and "accepted_count" in row else 0)

    # detect presence of title in CHAT_ID
    CHAT_ID_ENV = os.getenv("CHAT_ID")
    chat_id = None
    try:
        chat_id = int(CHAT_ID_ENV) if CHAT_ID_ENV is not None else None
    except Exception:
        chat_id = None

    has_title = False
    if chat_id is not None:
        try:
            member = await message.bot.get_chat_member(chat_id, user.id)
            custom_title = getattr(member, "custom_title", None)
            if custom_title and isinstance(custom_title, str) and custom_title.startswith("Репутация:"):
                has_title = True
        except Exception:
            has_title = False

    # prepare text (ru/uk) and keyboard
    if lang == "uk":
        if has_title:
            header = f"**📊 Інформація по постам {user_openmessage_link(user)}**"
            body = f"\n\n🆙 Ваша репутація: {rep}\n✅ Прийнятих постів: {accepted}\n\nНатисніть кнопку нижче, щоб сховати відображення своєї репутації поруч з нікнеймом"
        else:
            header = f"**📊 Інформація по постам {user_openmessage_link(user)}**"
            body = f"\n\n🆙 Ваша репутація: {rep}\n✅ Прийнятих постів: {accepted}\n\nНатисніть кнопку нижче, щоб встановити відображення своєї репутації поруч з нікнеймом"
    else:
        if has_title:
            header = f"**📊 Информация по постам {user_openmessage_link(user)}**"
            body = f"\n\n🆙 Ваша репутация: {rep}\n✅ Принятых постов: {accepted}\n\nНажмите кнопку ниже, чтобы скрыть отображение своей репутации рядом с никнеймом"
        else:
            header = f"**📊 Информация по постам {user_openmessage_link(user)}**"
            body = f"\n\n🆙 Ваша репутация: {rep}\n✅ Принятых постов: {accepted}\n\nНажмите кнопку ниже, чтобы установить отображение своей репутации рядом с никнейком"

    text = header + "\n" + body
    kb = build_info_kb(lang, user.id, has_title)

    try:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        try:
            await message.reply(text, parse_mode="HTML")
        except Exception:
            pass

# ---------------- Callback handler for toggle ----------------
async def _cb_toggle_rep(call: types.CallbackQuery):
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
        import bot_new as main_mod
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
    CHAT_ID_ENV = os.getenv("CHAT_ID")
    try:
        chat_id = int(CHAT_ID_ENV) if CHAT_ID_ENV is not None else None
    except Exception:
        chat_id = None

    # check if user currently has title
    has_title = False
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
            # promote with zero rights
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
            # set custom title
            await call.bot.set_chat_administrator_custom_title(chat_id=chat_id, user_id=target_id, custom_title=f"Репутация: {rep}")
            if lang == "uk":
                await call.answer("➕ Ви встановили відображення репутації поруч зі своїм нікнеймом.", show_alert=True)
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
            # try to clear custom_title
            try:
                await call.bot.set_chat_administrator_custom_title(chat_id=chat_id, user_id=target_id, custom_title="")
            except Exception:
                # fallback: re-promote without rights (some clients keep title until demoted, but try)
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

# ---------------- Unban command (callable from bot_new) ----------------
async def unban_cmd(message: types.Message, bot, set_banned_until_fn):
    """
    Unban command invoked from group (bot_new delegates).
    Usage: /разбан <user_or_id>
    """
    if message.chat is None:
        return

    PREDLOJKA_ID_ENV = os.getenv("PREDLOJKA_ID")
    try:
        pred_id = int(PREDLOJKA_ID_ENV) if PREDLOJKA_ID_ENV is not None else None
    except Exception:
        pred_id = None
    if pred_id is not None and message.chat.id != pred_id:
        try:
            await message.reply("Команда доступна только в группе предложки.")
        except Exception:
            pass
        return

    if not message.text:
        return
    parts = message.text.strip().split(None, 1)
    if len(parts) < 2:
        await message.reply("Укажите пользователя по @юзернейму или ID. Пример: разбан 123456789")
        return
    target = parts[1].strip()
    target_id = None
    if target.startswith("@"):
        try:
            ch = await bot.get_chat(target)
            target_id = ch.id
        except Exception:
            target_id = None
    else:
        try:
            target_id = int(target)
        except Exception:
            try:
                ch = await bot.get_chat("@" + target)
                target_id = ch.id
            except Exception:
                target_id = None
    if target_id is None:
        await message.reply("Не удалось определить пользователя. Укажите корректный @юзернейм или числовой ID.")
        return
    try:
        await set_banned_until_fn(target_id, 0)
    except Exception:
        await message.reply("Ошибка при записи в базу. Попробуйте позже.")
        return
    await message.reply(f"Пользователь {target} (ID {target_id}) разбанен в предложке.")
    try:
        await bot.send_message(target_id, "Вас разбанили в системе предложений постов. Вы снова можете предлагать посты.")
    except Exception:
        pass

# ---------------- Background sync: update "Репутация: N" titles ----------------
async def _rep_title_sync_loop():
    """
    Periodically (every 60s) scan admins in CHAT_ID and update custom_title if it starts with "Репутация:"
    This makes titles reflect the current reputation automatically without modifying bot_new.
    """
    try:
        import bot_new as main_mod
    except Exception:
        # can't import host module => nothing to do
        return

    BOT = getattr(main_mod, "bot", None)
    if BOT is None:
        # nothing to do now; we will not start
        return

    CHAT_ID_ENV = os.getenv("CHAT_ID")
    try:
        chat_id = int(CHAT_ID_ENV) if CHAT_ID_ENV is not None else None
    except Exception:
        chat_id = None

    if chat_id is None:
        return

    while True:
        try:
            # get current admins
            try:
                admins = await BOT.get_chat_administrators(chat_id)
            except Exception:
                admins = []
            for member in admins:
                try:
                    ct = getattr(member, "custom_title", None)
                    if ct and isinstance(ct, str) and ct.startswith("Репутация:"):
                        uid = member.user.id
                        # fetch current rep from DB via main_mod
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
                                # ignore; maybe lacks permission
                                pass
                except Exception:
                    pass
        except Exception:
            # swallow and continue loop
            pass
        # sleep then repeat
        await asyncio.sleep(60)

# ---------------- Registration and startup attempts ----------------
# Try to register callback and start background loop if bot_new.dp / bot exist.
try:
    import bot_new as main_mod  # type: ignore
    dp = getattr(main_mod, "dp", None)
    if dp is not None:
        try:
            dp.callback_query.register(_cb_toggle_rep, lambda c: c.data and c.data.startswith("cmd_toggle_rep:"))
        except Exception:
            # older aiogram versions or other issues -> ignore here
            pass
    # Start background sync loop if bot exists and event loop is running
    BOT = getattr(main_mod, "bot", None)
    if BOT is not None:
        try:
            # safe scheduling: create task if loop running
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # schedule in running loop
                asyncio.create_task(_rep_title_sync_loop())
            else:
                # if loop not running yet, schedule after small delay to allow main to start it
                def _schedule():
                    try:
                        asyncio.create_task(_rep_title_sync_loop())
                    except Exception:
                        pass
                # try to call _schedule later when loop starts
                try:
                    loop.call_soon(_schedule)
                except Exception:
                    # fallback: create background thread? skip
                    pass
        except Exception:
            pass
except Exception:
    # cannot import main module now; bot_new will import command later inside handlers.
    pass

# ---------------- Utility export: update single user title ----------------
async def update_rep_title_if_present(user_id: int):
    """
    If user has custom_title 'Репутация: ...' in CHAT_ID, update it to current reputation.
    Can be called from bot_new immediately after changing reputation.
    """
    try:
        import bot_new as main_mod
    except Exception:
        return
    BOT = getattr(main_mod, "bot", None)
    if BOT is None:
        return
    CHAT_ID_ENV = os.getenv("CHAT_ID")
    try:
        chat_id = int(CHAT_ID_ENV) if CHAT_ID_ENV is not None else None
    except Exception:
        chat_id = None
    if chat_id is None:
        return
    try:
        member = await BOT.get_chat_member(chat_id, user_id)
        ct = getattr(member, "custom_title", None)
        if ct and isinstance(ct, str) and ct.startswith("Репутация:"):
            row = await main_mod.get_user(user_id)
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
__all__ = ("info_cmd", "unban_cmd", "update_rep_title_if_present")
