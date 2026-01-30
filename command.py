# command.py
# Contains handlers for textual commands: /info variants and разбан command logic (DB-unban).
# Functions are called from bot_new.py (delegated).

from typing import Optional
from aiogram import types, Bot

# command.info_cmd(message) -> answers "инфо"
async def info_cmd(message: types.Message):
    """
    Respond to /info and textual equivalents:
    /info, индо variants -> simple reply "инфо"
    """
    try:
        await message.reply("инфо")
    except Exception:
        try:
            # fallback to send_message
            await message.answer("инфо")
        except Exception:
            pass

# command.unban_cmd(message, bot, set_banned_until)
async def unban_cmd(message: types.Message, bot: Bot, set_banned_until_callable):
    """
    Unban a user from the propose system. This function is intended to be called
    only when message.chat.id == PREDLOJKA_ID (group chat).
    Accepts:
      - "разбан 123456" or "/разбан 123456"
      - "разбан @username" or "/разбан @username"
      - latin "razban" variants too
    Uses set_banned_until_callable(target_id, 0) to clear ban in DB.
    """
    if not message.text:
        return
    text = message.text.strip()
    # strip leading command slashes and split
    # accept form: "/разбан <target>" or "разбан <target>"
    parts = text.split(None, 1)
    if len(parts) < 2:
        try:
            await message.reply("Укажите пользователя по @юзернейму или ID. Пример: разбан 123456789")
        except Exception:
            pass
        return
    target = parts[1].strip()
    target_id: Optional[int] = None

    # Try to resolve numeric id
    if target.startswith("@"):
        # username provided; attempt to get chat
        try:
            chat = await bot.get_chat(target)
            target_id = chat.id
        except Exception:
            target_id = None
    else:
        # try parse int id
        try:
            target_id = int(target)
        except Exception:
            # maybe username without @
            try:
                chat = await bot.get_chat("@" + target)
                target_id = chat.id
            except Exception:
                target_id = None

    if target_id is None:
        try:
            await message.reply("Не удалось определить пользователя. Укажите корректный @юзернейм или числовой ID.")
        except Exception:
            pass
        return

    # call DB helper to clear ban
    try:
        # set_banned_until_callable is expected to be async; call it with await
        await set_banned_until_callable(target_id, 0)
    except Exception:
        try:
            await message.reply("Ошибка при записи в базу. Попробуйте позже.")
        except Exception:
            pass
        return

    # notify in group and try notify user
    try:
        await message.reply(f"Пользователь {target} (ID {target_id}) разбанен в предложке.")
    except Exception:
        pass

    try:
        await bot.send_message(target_id, "Вас разбанили в системе предложений постов. Вы снова можете предлагать посты.")
    except Exception:
        pass
