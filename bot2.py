# bot2.py
# Minimal support helper module.
# Exposes send_support(bot, chat_id, lang) to be called by bot_new.

from typing import Optional
from aiogram import Bot

async def send_support(bot: Bot, chat_id: int, lang: Optional[str] = "ru"):
    """
    Send a support message to chat_id. Bot instance must be passed (same Bot used in bot_new).
    Language-aware simple message.
    """
    if lang == "uk":
        text = "Підтримка"
    else:
        text = "Поддержка"
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        # swallow; caller should handle fallback if needed
        pass
