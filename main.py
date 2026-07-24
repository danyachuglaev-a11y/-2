#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
from typing import List, Dict

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from groq import Groq

# ============================================================
# 🔧 КОНФИГУРАЦИЯ — ЗАМЕНИ ЭТИ ДВЕ СТРОКИ НА СВОИ ДАННЫЕ
# ============================================================

BOT_TOKEN = "8887137957:AAHsh1OjO30sRdzVe7ljhsWc5ud8DXIFbeE"  # например: "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
GROQ_API_KEY = "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"  # например: "gsk_abc123def456ghi789jkl"
MODEL = "llama-3.3-70b-versatile"
MAX_HISTORY = 15

# ============================================================
# ⚙️ ИНИЦИАЛИЗАЦИЯ
# ============================================================

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()

groq_client = Groq(api_key=GROQ_API_KEY)
user_histories: Dict[int, List[Dict[str, str]]] = {}

# ============================================================
# 🧠 СИСТЕМНЫЙ ПРОМПТ
# ============================================================

SYSTEM_PROMPT = (
    "Ты — AI screamsoon. Твой создатель — @screamsoon. "
    "Отвечай вежливо, понятно, по делу. "
    "Не используй эмодзи и смайлики в ответах слишком много . "
    "Никогда не упоминай нейросети, Groq, OpenAI, API или технологии. "
    "Ты — просто помощник. Всегда в конце ответа добавляй: Мой создатель @screamsoon"
    "Если человеку нужно чтобы тебе добавили в чат , то просто скажи ему как это сделать и общайся в чате"
    "Если человек просит с ним общаться и писать первым значит ты это выполняешь "
    "Отвечай клиенту всегда правильно и четко на его поставленный вопрос без ошибок "
)


# ============================================================
# РАБОТА С ИСТОРИЕЙ
# ============================================================

def get_history(user_id: int) -> List[Dict[str, str]]:
    if user_id not in user_histories:
        user_histories[user_id] = []
    return user_histories[user_id]


def add_to_history(user_id: int, role: str, content: str):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY * 2:
        user_histories[user_id] = history[-MAX_HISTORY * 2:]


def clear_history(user_id: int):
    if user_id in user_histories:
        user_histories[user_id] = []


# ============================================================
# ЗАПРОС К GROQ
# ============================================================

async def ask_groq(user_id: int, user_message: str) -> str:
    add_to_history(user_id, "user", user_message)
    history = get_history(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        completion = groq_client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=2048,
        )
        answer = completion.choices[0].message.content
        add_to_history(user_id, "assistant", answer)
        return answer
    except Exception as e:
        logging.error(f"API error: {e}")
        return "Ошибка. Попробуй ещё раз."


# ============================================================
# ХЕНДЛЕРЫ
# ============================================================

@router.message(Command("start"))
async def start_cmd(msg: Message):
    clear_history(msg.from_user.id)
    await msg.answer(
        "Привет. Я AI screamsoon. Просто напиши, что нужно.\n"
        "Мой создатель: @screamsoon\n\n"
        "/clear — очистить историю"
    )


@router.message(Command("clear"))
async def clear_cmd(msg: Message):
    clear_history(msg.from_user.id)
    await msg.answer("История очищена.")


# ============================================================
# ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ
# ============================================================

@router.message(F.text)
async def handle_text(msg: Message):
    user_text = msg.text.strip()
    if not user_text:
        return

    await msg.bot.send_chat_action(msg.chat.id, "typing")

    answer = await ask_groq(msg.from_user.id, user_text)

    # Если нет подписи — добавляем
    if "@screamsoon" not in answer.lower():
        answer += "\n\n@ScreamSoon"

    await msg.answer(answer)


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    dp.include_router(router)
    logging.info("AI screamsoon запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
