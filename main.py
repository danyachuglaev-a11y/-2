#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🤖 ZOV BOT — Полнофункциональный Telegram бот
Версия: 3.0.0
Планета: ZOV
Статус: БОЕВОЙ
"""

# ============================================================
# 📦 ИМПОРТЫ
# ============================================================

import asyncio
import logging
import re
import time
import json
import sqlite3
import os
import tempfile
import html
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Optional, Any
from pathlib import Path

# Aiogram
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMember, ChatMemberAdministrator, ChatMemberOwner,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument,
    FSInputFile, URLInputFile, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, ForceReply
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter
)

# Groq
from groq import Groq

# HTTP
import aiohttp

# ============================================================
# ⚙️ КОНФИГУРАЦИЯ — ЗАМЕНИТЬ НА СВОИ ДАННЫЕ
# ============================================================

BOT_TOKEN = "8887137957:AAHsh1OjO30sRdzVe7ljhsWc5ud8DXIFbeE"
GROQ_API_KEY = "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"
MODEL = "llama-3.3-70b-versatile"
MAX_HISTORY = 15

# Администраторы (ваш Telegram ID)
ADMIN_IDS = [8887137957]  # ЗАМЕНИТЬ НА СВОЙ ID

# ============================================================
# 📁 НАСТРОЙКИ ПУТЕЙ
# ============================================================

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "zov_bot_data.db"
LOGS_DIR = BASE_DIR / "logs"
TEMP_DIR = BASE_DIR / "temp"

# Создаем директории
LOGS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# ============================================================
# 📊 ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / 'bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 🧠 FSM СОСТОЯНИЯ
# ============================================================

class Form(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_system_prompt = State()
    waiting_for_post_text = State()
    waiting_for_channel_selection = State()
    waiting_for_template_name = State()
    waiting_for_template_content = State()

# ============================================================
# 🤖 ИНИЦИАЛИЗАЦИЯ БОТА И ДИСПЕТЧЕРА
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# Groq клиент
groq_client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# 💾 БАЗА ДАННЫХ SQLITE (ПОЛНАЯ)
# ============================================================

def init_db():
    """Инициализация всех таблиц базы данных"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Пользователи
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_date TEXT,
            last_activity TEXT,
            total_requests INTEGER DEFAULT 0,
            banned BOOLEAN DEFAULT FALSE,
            trust_score INTEGER DEFAULT 50
        )
    ''')
    
    # История диалогов (храним в JSON)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Обратная связь
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            rating INTEGER,
            comment TEXT,
            date TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Персональные промпты
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS custom_prompts (
            user_id INTEGER PRIMARY KEY,
            prompt TEXT,
            updated_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Каналы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT UNIQUE,
            chat_title TEXT,
            chat_username TEXT,
            added_by INTEGER,
            added_date TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            FOREIGN KEY(added_by) REFERENCES users(user_id)
        )
    ''')
    
    # Посты (история)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT,
            message_id INTEGER,
            author_id INTEGER,
            content TEXT,
            media_type TEXT,
            media_file_id TEXT,
            posted_date TEXT,
            views INTEGER DEFAULT 0,
            FOREIGN KEY(channel_id) REFERENCES channels(chat_id),
            FOREIGN KEY(author_id) REFERENCES users(user_id)
        )
    ''')
    
    # Шаблоны постов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS post_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            content TEXT,
            created_date TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Черный список слов (для модерации)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banned_words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT UNIQUE,
            added_by INTEGER,
            added_date TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# Инициализация БД при старте
init_db()

# ============================================================
# 📋 ФУНКЦИИ РАБОТЫ С БАЗОЙ ДАННЫХ
# ============================================================

def get_user_data(user_id: int) -> Optional[Dict]:
    """Получить данные пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    data = cursor.fetchone()
    conn.close()
    if data:
        columns = ['user_id', 'username', 'first_name', 'last_name', 
                   'joined_date', 'last_activity', 'total_requests', 
                   'banned', 'trust_score']
        return dict(zip(columns, data))
    return None

def register_user(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Регистрация или обновление пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    existing = get_user_data(user_id)
    if not existing:
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, last_name, joined_date, last_activity)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name, 
              datetime.now().isoformat(), datetime.now().isoformat()))
    else:
        cursor.execute('''
            UPDATE users SET last_activity = ?, username = ?, first_name = ?, last_name = ?
            WHERE user_id = ?
        ''', (datetime.now().isoformat(), username, first_name, last_name, user_id))
    
    conn.commit()
    conn.close()

def update_trust_score(user_id: int, delta: int):
    """Обновить рейтинг доверия"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users SET trust_score = trust_score + ? WHERE user_id = ?
    ''', (delta, user_id))
    conn.commit()
    conn.close()

def increment_requests(user_id: int):
    """Увеличить счетчик запросов"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users SET total_requests = total_requests + 1 WHERE user_id = ?
    ''', (user_id,))
    conn.commit()
    conn.close()

def set_user_banned(user_id: int, banned: bool):
    """Забанить/разбанить пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET banned = ? WHERE user_id = ?', (banned, user_id))
    conn.commit()
    conn.close()

def get_custom_prompt(user_id: int) -> Optional[str]:
    """Получить персональный промпт"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT prompt FROM custom_prompts WHERE user_id = ?', (user_id,))
    data = cursor.fetchone()
    conn.close()
    return data[0] if data else None

def set_custom_prompt(user_id: int, prompt: str):
    """Сохранить персональный промпт"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO custom_prompts (user_id, prompt, updated_at)
        VALUES (?, ?, ?)
    ''', (user_id, prompt, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def save_history_message(user_id: int, role: str, content: str):
    """Сохранить сообщение в историю"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO chat_history (user_id, role, content, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (user_id, role, content, datetime.now().isoformat()))
    
    # Ограничиваем историю (храним последние 30 сообщений)
    cursor.execute('''
        DELETE FROM chat_history WHERE user_id = ? 
        AND timestamp < (
            SELECT timestamp FROM chat_history 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT 1 OFFSET 30
        )
    ''', (user_id, user_id))
    
    conn.commit()
    conn.close()

def get_chat_history(user_id: int, limit: int = 15) -> List[Dict]:
    """Получить историю чата"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, content FROM chat_history
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (user_id, limit))
    data = cursor.fetchall()
    conn.close()
    return [{'role': row[0], 'content': row[1]} for row in reversed(data)]

def clear_chat_history(user_id: int):
    """Очистить историю"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM chat_history WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# ============================================================
# 📢 ФУНКЦИИ РАБОТЫ С КАНАЛАМИ
# ============================================================

async def add_channel_to_db(chat_id: str, chat_title: str, chat_username: str, added_by: int):
    """Добавить канал в базу"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO channels (chat_id, chat_title, chat_username, added_by, added_date, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, chat_title, chat_username, added_by, datetime.now().isoformat(), True))
    conn.commit()
    conn.close()

async def get_user_channels(user_id: int) -> List[Dict]:
    """Получить каналы пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT chat_id, chat_title, chat_username, added_date
        FROM channels
        WHERE added_by = ? AND is_active = TRUE
    ''', (user_id,))
    data = cursor.fetchall()
    conn.close()
    
    channels = []
    for row in data:
        channels.append({
            'chat_id': row[0],
            'chat_title': row[1],
            'chat_username': row[2],
            'added_date': row[3]
        })
    return channels

async def remove_channel_from_db(chat_id: str):
    """Деактивировать канал"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE channels SET is_active = FALSE WHERE chat_id = ?', (chat_id,))
    conn.commit()
    conn.close()

async def save_post_to_db(channel_id: str, message_id: int, author_id: int, 
                         content: str, media_type: str = None, media_file_id: str = None):
    """Сохранить пост в историю"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO posts (channel_id, message_id, author_id, content, media_type, media_file_id, posted_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (channel_id, message_id, author_id, content, media_type, media_file_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

async def get_post_history(channel_id: str, limit: int = 10) -> List[Dict]:
    """История постов"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT message_id, content, media_type, posted_date, views
        FROM posts
        WHERE channel_id = ?
        ORDER BY posted_date DESC
        LIMIT ?
    ''', (channel_id, limit))
    data = cursor.fetchall()
    conn.close()
    
    posts = []
    for row in data:
        posts.append({
            'message_id': row[0],
            'content': row[1][:100] + '...' if row[1] and len(row[1]) > 100 else row[1],
            'media_type': row[2],
            'posted_date': row[3],
            'views': row[4] or 0
        })
    return posts

async def save_template(user_id: int, name: str, content: str):
    """Сохранить шаблон поста"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO post_templates (user_id, name, content, created_date)
        VALUES (?, ?, ?, ?)
    ''', (user_id, name, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

async def get_templates(user_id: int) -> List[Dict]:
    """Получить шаблоны пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT name, content, created_date
        FROM post_templates
        WHERE user_id = ?
        ORDER BY created_date DESC
    ''', (user_id,))
    data = cursor.fetchall()
    conn.close()
    
    templates = []
    for row in data:
        templates.append({
            'name': row[0],
            'content': row[1],
            'created_date': row[2]
        })
    return templates

async def get_template_content(user_id: int, name: str) -> Optional[str]:
    """Получить содержимое шаблона"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT content FROM post_templates
        WHERE user_id = ? AND name = ?
    ''', (user_id, name))
    data = cursor.fetchone()
    conn.close()
    return data[0] if data else None

async def delete_template(user_id: int, name: str):
    """Удалить шаблон"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM post_templates
        WHERE user_id = ? AND name = ?
    ''', (user_id, name))
    conn.commit()
    conn.close()

# ============================================================
# 🛡️ СИСТЕМА АНТИ-ФЛУДА
# ============================================================

class RateLimiter:
    def __init__(self, max_requests: int = 8, time_window: int = 60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests: Dict[int, List[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        self.requests[user_id] = [t for t in self.requests[user_id] if now - t < self.time_window]
        if len(self.requests[user_id]) >= self.max_requests:
            return False
        self.requests[user_id].append(now)
        return True

rate_limiter = RateLimiter(max_requests=8, time_window=60)

# ============================================================
# 🧠 СИСТЕМНЫЙ ПРОМПТ
# ============================================================

SYSTEM_PROMPT_BASE = (
    "Ты — AI screamsoon. Твой создатель — @screamsoon. "
    "Отвечай вежливо, понятно, по делу. "
    "Не используй эмодзи и смайлики в ответах слишком много. "
    "Никогда не упоминай нейросети, Groq, OpenAI, API или технологии. "
    "Ты — просто помощник. Всегда в конце ответа добавляй: Мой создатель @screamsoon. "
    "Если человеку нужно чтобы тебя добавили в чат, то просто скажи ему как это сделать и общайся в чате. "
    "Если человек просит с ним общаться и писать первым значит ты это выполняешь. "
    "Отвечай клиенту всегда правильно и четко на его поставленный вопрос без ошибок. "
    "Если пользователь спрашивает что-то запретное или опасное — вежливо откажи, но предложи альтернативу."
)

def get_system_prompt(user_id: int) -> str:
    custom = get_custom_prompt(user_id)
    return custom if custom else SYSTEM_PROMPT_BASE

# ============================================================
# 🔥 ОБНОВЛЕННАЯ ФУНКЦИЯ ЗАПРОСА К GROQ
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def ask_groq(user_id: int, user_message: str) -> str:
    """Отправка запроса к Groq с полной историей"""
    
    # Проверка на бан
    user_data = get_user_data(user_id)
    if user_data and user_data.get('banned', False):
        return "⛔ Вы забанены. Обратитесь к администратору."

    # Проверка лимитов
    if not is_admin(user_id) and not rate_limiter.is_allowed(user_id):
        return "⚠️ Слишком много запросов. Подождите минуту."

    # Регистрация пользователя
    register_user(user_id)
    increment_requests(user_id)
    update_trust_score(user_id, 1)

    # Фильтрация мата
    profanity_pattern = re.compile(r'(?i)(хуй|пизд|ебал|бля|сука|нах|залуп|муд|говн|сос|дроч|хер|хрен|заеб)')
    if profanity_pattern.search(user_message):
        user_message = profanity_pattern.sub('****', user_message)
        update_trust_score(user_id, -5)

    # Сохраняем сообщение пользователя
    save_history_message(user_id, "user", user_message)
    
    # Получаем историю
    history = get_chat_history(user_id, MAX_HISTORY)
    system_prompt = get_system_prompt(user_id)
    
    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        completion = groq_client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.8,
            max_tokens=2048,
            timeout=30
        )
        answer = completion.choices[0].message.content

        # Если ответ слишком короткий
        if len(answer.split()) < 5:
            answer += " Могу подробнее, если нужно."

        # Сохраняем ответ ассистента
        save_history_message(user_id, "assistant", answer)

        # Добавляем подпись
        if "@screamsoon" not in answer.lower():
            answer += "\n\n@ScreamSoon"

        return answer

    except Exception as e:
        logger.error(f"Ошибка Groq API для user {user_id}: {e}")
        update_trust_score(user_id, -10)
        return "⚠️ Техническая ошибка. Повторите через несколько секунд."

# ============================================================
# 📤 ФУНКЦИЯ ПУБЛИКАЦИИ В КАНАЛ
# ============================================================

async def publish_to_channel(
    channel_id: str,
    text: str,
    media_file: str = None,
    media_type: str = None,
    parse_mode: str = 'HTML',
    disable_notification: bool = False,
    reply_markup: InlineKeyboardMarkup = None
) -> Dict:
    """
    Универсальная функция публикации поста в канал
    """
    result = {
        'success': False,
        'message_id': None,
        'error': None
    }
    
    try:
        # Проверяем права бота
        bot_member = await bot.get_chat_member(channel_id, bot.id)
        if not isinstance(bot_member, (ChatMemberAdministrator, ChatMemberOwner)):
            result['error'] = 'Бот не администратор в этом канале'
            return result
        
        # Отправка в зависимости от типа медиа
        if media_file and media_type:
            if media_type == 'photo':
                if media_file.startswith('http'):
                    msg = await bot.send_photo(
                        channel_id, media_file, caption=text, 
                        parse_mode=parse_mode, disable_notification=disable_notification,
                        reply_markup=reply_markup
                    )
                else:
                    msg = await bot.send_photo(
                        channel_id, FSInputFile(media_file), caption=text,
                        parse_mode=parse_mode, disable_notification=disable_notification,
                        reply_markup=reply_markup
                    )
            elif media_type == 'video':
                if media_file.startswith('http'):
                    msg = await bot.send_video(
                        channel_id, media_file, caption=text,
                        parse_mode=parse_mode, disable_notification=disable_notification,
                        reply_markup=reply_markup
                    )
                else:
                    msg = await bot.send_video(
                        channel_id, FSInputFile(media_file), caption=text,
                        parse_mode=parse_mode, disable_notification=disable_notification,
                        reply_markup=reply_markup
                    )
            elif media_type == 'document':
                if media_file.startswith('http'):
                    msg = await bot.send_document(
                        channel_id, media_file, caption=text,
                        parse_mode=parse_mode, disable_notification=disable_notification,
                        reply_markup=reply_markup
                    )
                else:
                    msg = await bot.send_document(
                        channel_id, FSInputFile(media_file), caption=text,
                        parse_mode=parse_mode, disable_notification=disable_notification,
                        reply_markup=reply_markup
                    )
            else:
                msg = await bot.send_message(
                    channel_id, text,
                    parse_mode=parse_mode, disable_notification=disable_notification,
                    reply_markup=reply_markup
                )
        else:
            # Только текст
            msg = await bot.send_message(
                channel_id, text,
                parse_mode=parse_mode, disable_notification=disable_notification,
                reply_markup=reply_markup
            )
        
        result['success'] = True
        result['message_id'] = msg.message_id
        
        # Сохраняем в БД
        await save_post_to_db(channel_id, msg.message_id, None, text, media_type, None)
        
        return result
        
    except TelegramForbiddenError:
        result['error'] = 'Бот заблокирован в канале или нет прав'
    except TelegramBadRequest as e:
        result['error'] = f'Ошибка отправки: {str(e)}'
    except Exception as e:
        result['error'] = f'Неизвестная ошибка: {str(e)}'
    
    return result

# ============================================================
# 🎯 ХЕНДЛЕРЫ КОМАНД
# ============================================================

# === СТАРТ ===
@router.message(Command("start"))
async def start_cmd(msg: Message):
    clear_chat_history(msg.from_user.id)
    register_user(
        msg.from_user.id,
        msg.from_user.username,
        msg.from_user.first_name,
        msg.from_user.last_name
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📢 Добавить канал", callback_data="add_channel_help")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help_menu")]
    ])
    
    await msg.answer(
        "🤖 <b>ZOV BOT v3.0</b>\n\n"
        "Я — AI screamsoon. Создатель: @screamsoon.\n\n"
        "📌 <b>Основные возможности:</b>\n"
        "• Общение и ответы на вопросы\n"
        "• Публикация постов в каналы\n"
        "• Управление несколькими каналами\n"
        "• Шаблоны постов\n"
        "• История и статистика\n\n"
        "Отправьте /help для полного списка команд.",
        reply_markup=keyboard
    )

# === ПОМОЩЬ ===
@router.message(Command("help"))
async def help_cmd(msg: Message):
    text = (
        "🤖 <b>ZOV BOT — Полная справка</b>\n\n"
        "📌 <b>Основные команды:</b>\n"
        "/start — Начать работу\n"
        "/help — Эта справка\n"
        "/clear — Очистить историю чата\n"
        "/profile — Ваш профиль\n"
        "/stats — Статистика бота\n"
        "/rate <1-5> [комментарий] — Оценить ответ\n\n"
        
        "📢 <b>Управление каналами:</b>\n"
        "/addchannel @username — Добавить канал\n"
        "/channels — Список каналов\n"
        "/post <текст> — Опубликовать пост\n"
        "/postphoto <подпись> — Опубликовать с фото\n"
        "/postvideo <подпись> — Опубликовать с видео\n"
        "/history — История постов\n"
        "/removechannel <ID> — Удалить канал\n\n"
        
        "📝 <b>Шаблоны постов:</b>\n"
        "/posttemplate <имя> <текст> — Сохранить шаблон\n"
        "/templates — Список шаблонов\n"
        "/usetemplate <имя> — Использовать шаблон\n"
        "/deletetemplate <имя> — Удалить шаблон\n\n"
        
        "⚙️ <b>Настройки:</b>\n"
        "/setprompt <текст> — Персональный промпт\n"
        "/resetprompt — Сбросить промпт\n"
        "/find <текст> — Поиск в истории\n\n"
        
        "👨‍💻 Создатель: @screamsoon"
    )
    await msg.answer(text)

# === ОЧИСТКА ИСТОРИИ ===
@router.message(Command("clear"))
async def clear_cmd(msg: Message):
    clear_chat_history(msg.from_user.id)
    await msg.answer("✅ История очищена.")

# === ПРОФИЛЬ ===
@router.message(Command("profile"))
async def profile_cmd(msg: Message):
    user_data = get_user_data(msg.from_user.id)
    if not user_data:
        await msg.answer("❌ Вы не зарегистрированы. Напишите что-нибудь для регистрации.")
        return
    
    text = (
        f"📊 <b>Ваш профиль</b>\n"
        f"🆔 ID: <code>{user_data['user_id']}</code>\n"
        f"👤 Имя: {user_data['first_name'] or 'Не указано'}\n"
        f"📝 Юзернейм: @{user_data['username'] or 'Нет'}\n"
        f"📅 Присоединился: {user_data['joined_date'][:10]}\n"
        f"🔢 Запросов: {user_data['total_requests']}\n"
        f"⭐ Рейтинг доверия: {user_data['trust_score']}/100\n"
        f"🚫 Статус: {'🔴 Забанен' if user_data['banned'] else '🟢 Активен'}"
    )
    await msg.answer(text)

# === СТАТИСТИКА ===
@router.message(Command("stats"))
async def stats_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён. Только для администраторов.")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT SUM(total_requests) FROM users')
    total_requests = cursor.fetchone()[0] or 0
    cursor.execute('SELECT COUNT(*) FROM users WHERE banned = 1')
    banned_users = cursor.fetchone()[0]
    cursor.execute('SELECT AVG(trust_score) FROM users')
    avg_trust = cursor.fetchone()[0] or 0
    cursor.execute('SELECT COUNT(*) FROM channels WHERE is_active = TRUE')
    total_channels = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM posts')
    total_posts = cursor.fetchone()[0]
    conn.close()
    
    text = (
        f"📈 <b>Статистика ZOV BOT</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"📩 Всего запросов: {total_requests}\n"
        f"🚫 Забанено: {banned_users}\n"
        f"⭐ Средний рейтинг: {avg_trust:.1f}/100\n"
        f"📢 Активных каналов: {total_channels}\n"
        f"📝 Всего постов: {total_posts}"
    )
    await msg.answer(text)

# === ОЦЕНКА ===
@router.message(Command("rate"))
async def rate_cmd(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("Использование: /rate <оценка 1-5> [комментарий]")
        return
    
    try:
        rating = int(args[1])
        if rating < 1 or rating > 5:
            await msg.answer("Оценка должна быть от 1 до 5.")
            return
    except ValueError:
        await msg.answer("Оценка должна быть числом.")
        return
    
    comment = " ".join(args[2:]) if len(args) > 2 else ""
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO feedback (user_id, rating, comment, date)
        VALUES (?, ?, ?, ?)
    ''', (msg.from_user.id, rating, comment, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    await msg.answer(f"✅ Спасибо за оценку {rating}/5!")

# === ПЕРСОНАЛЬНЫЙ ПРОМПТ ===
@router.message(Command("setprompt"))
async def set_prompt_cmd(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Использование: /setprompt <текст системного промпта>")
        return
    
    prompt = args[1].strip()
    set_custom_prompt(msg.from_user.id, prompt)
    await msg.answer("✅ Персональный системный промпт установлен.")

@router.message(Command("resetprompt"))
async def reset_prompt_cmd(msg: Message):
    set_custom_prompt(msg.from_user.id, None)
    await msg.answer("✅ Системный промпт сброшен до стандартного.")

# === ПОИСК В ИСТОРИИ ===
@router.message(Command("find"))
async def find_cmd(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Использование: /find <текст для поиска>")
        return
    
    query = args[1].strip().lower()
    history = get_chat_history(msg.from_user.id, 50)
    found = []
    for item in history:
        if query in item['content'].lower():
            found.append(f"{item['role']}: {item['content'][:100]}...")
    
    if not found:
        await msg.answer("🔍 Ничего не найдено в истории.")
    else:
        result = "\n\n".join(found[:5])
        await msg.answer(f"🔍 Найдено {len(found)} совпадений:\n\n{result}")

# === УПРАВЛЕНИЕ КАНАЛАМИ ===
@router.message(Command("addchannel"))
async def add_channel_cmd(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer(
            "📢 <b>Добавление канала:</b>\n\n"
            "1. Добавьте бота в канал как администратора\n"
            "2. Отправьте: /addchannel @username_канала\n\n"
            "Пример: /addchannel @my_channel"
        )
        return
    
    channel_input = args[1].strip()
    
    try:
        if channel_input.startswith('@'):
            chat = await bot.get_chat(channel_input)
            chat_id = str(chat.id)
            chat_title = chat.title or channel_input
            chat_username = channel_input
        elif channel_input.startswith('-100'):
            chat_id = channel_input
            chat = await bot.get_chat(int(channel_input))
            chat_title = chat.title or channel_input
            chat_username = chat.username or ''
        else:
            await msg.answer("❌ Неверный формат. Используйте @username или -100ID")
            return
        
        # Проверяем права бота
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        if not isinstance(bot_member, (ChatMemberAdministrator, ChatMemberOwner)):
            await msg.answer(
                "❌ Бот не администратор в этом канале.\n"
                "Добавьте бота как администратора и попробуйте снова."
            )
            return
        
        await add_channel_to_db(chat_id, chat_title, chat_username, msg.from_user.id)
        
        await msg.answer(
            f"✅ <b>Канал добавлен!</b>\n\n"
            f"📌 Название: {chat_title}\n"
            f"🆔 ID: <code>{chat_id}</code>\n"
            f"📝 Юзернейм: {chat_username}\n\n"
            f"Теперь вы можете публиковать посты:\n"
            f"/post <текст> — текст\n"
            f"/postphoto <подпись> — с фото\n"
            f"/channels — список каналов"
        )
        
    except TelegramBadRequest as e:
        await msg.answer(f"❌ Ошибка: {str(e)}")
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {str(e)}")

@router.message(Command("channels"))
async def list_channels_cmd(msg: Message):
    channels = await get_user_channels(msg.from_user.id)
    
    if not channels:
        await msg.answer(
            "📢 У вас нет добавленных каналов.\n"
            "Добавьте: /addchannel @username"
        )
        return
    
    text = "📋 <b>Ваши каналы:</b>\n\n"
    keyboard = []
    
    for idx, ch in enumerate(channels, 1):
        text += f"{idx}. <b>{ch['chat_title']}</b>\n"
        text += f"   🆔 <code>{ch['chat_id']}</code>\n"
        text += f"   📅 Добавлен: {ch['added_date'][:10]}\n\n"
    
    await msg.answer(text)

@router.message(Command("removechannel"))
async def remove_channel_cmd(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("Использование: /removechannel <chat_id>")
        return
    
    chat_id = args[1].strip()
    await remove_channel_from_db(chat_id)
    await msg.answer(f"✅ Канал {chat_id} удалён из управления.")

# === ПУБЛИКАЦИЯ ПОСТОВ ===
@router.message(Command("post"))
async def post_text_cmd(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("❌ Укажите текст: /post <текст>")
        return
    
    text = args[1].strip()
    channels = await get_user_channels(msg.from_user.id)
    
    if not channels:
        await msg.answer("❌ Сначала добавьте канал: /addchannel @username")
        return
    
    if len(channels) > 1:
        keyboard = []
        for ch in channels:
            keyboard.append([
                InlineKeyboardButton(
                    f"📤 {ch['chat_title']}",
                    callback_data=f"post_text_{ch['chat_id']}_{text[:50]}"
                )
            ])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await msg.answer(
            "📢 <b>Выберите канал:</b>",
            reply_markup=reply_markup
        )
        return
    
    channel_id = channels[0]['chat_id']
    result = await publish_to_channel(channel_id, text)
    
    if result['success']:
        await msg.answer(f"✅ <b>Пост опубликован!</b>\n\n{text[:200]}")
    else:
        await msg.answer(f"❌ Ошибка: {result['error']}")

@router.message(Command("postphoto"))
async def post_photo_cmd(msg: Message):
    if not msg.photo:
        await msg.answer("❌ Отправьте команду с прикреплённым фото.")
        return
    
    caption = msg.caption or "📸 Фото без подписи"
    channels = await get_user_channels(msg.from_user.id)
    
    if not channels:
        await msg.answer("❌ Сначала добавьте канал: /addchannel @username")
        return
    
    file_id = msg.photo[-1].file_id
    
    if len(channels) > 1:
        keyboard = []
        for ch in channels:
            keyboard.append([
                InlineKeyboardButton(
                    f"📤 {ch['chat_title']}",
                    callback_data=f"post_photo_{ch['chat_id']}_{file_id}_{caption[:30]}"
                )
            ])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await msg.answer("📢 <b>Выберите канал:</b>", reply_markup=reply_markup)
        return
    
    channel_id = channels[0]['chat_id']
    result = await publish_to_channel(channel_id, caption, media_file=file_id, media_type='photo')
    
    if result['success']:
        await msg.answer("✅ <b>Пост с фото опубликован!</b>")
    else:
        await msg.answer(f"❌ Ошибка: {result['error']}")

@router.message(Command("postvideo"))
async def post_video_cmd(msg: Message):
    if not msg.video:
        await msg.answer("❌ Отправьте команду с прикреплённым видео.")
        return
    
    caption = msg.caption or "🎬 Видео без подписи"
    channels = await get_user_channels(msg.from_user.id)
    
    if not channels:
        await msg.answer("❌ Сначала добавьте канал: /addchannel @username")
        return
    
    file_id = msg.video.file_id
    
    if len(channels) > 1:
        keyboard = []
        for ch in channels:
            keyboard.append([
                InlineKeyboardButton(
                    f"📤 {ch['chat_title']}",
                    callback_data=f"post_video_{ch['chat_id']}_{file_id}_{caption[:30]}"
                )
            ])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await msg.answer("📢 <b>Выберите канал:</b>", reply_markup=reply_markup)
        return
    
    channel_id = channels[0]['chat_id']
    result = await publish_to_channel(channel_id, caption, media_file=file_id, media_type='video')
    
    if result['success']:
        await msg.answer("✅ <b>Пост с видео опубликован!</b>")
    else:
        await msg.answer(f"❌ Ошибка: {result['error']}")

@router.message(Command("history"))
async def history_cmd(msg: Message):
    channels = await get_user_channels(msg.from_user.id)
    if not channels:
        await msg.answer("❌ Сначала добавьте канал: /addchannel @username")
        return
    
    channel_id = channels[0]['chat_id']
    posts = await get_post_history(channel_id, limit=10)
    
    if not posts:
        await msg.answer("📭 В канале пока нет постов.")
        return
    
    text = f"📊 <b>Последние 10 постов</b>\n\n"
    for idx, post in enumerate(posts, 1):
        text += f"{idx}. 📝 {post['content']}\n"
        text += f"   🆔 ID: {post['message_id']}\n"
        text += f"   📅 {post['posted_date'][:10]}\n\n"
    
    await msg.answer(text[:4096])

# === ШАБЛОНЫ ===
@router.message(Command("posttemplate"))
async def save_template_cmd(msg: Message):
    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        await msg.answer(
            "📝 <b>Сохранить шаблон:</b>\n\n"
            "/posttemplate <имя> <текст>\n\n"
            "Пример: /posttemplate важное Всем привет!"
        )
        return
    
    name = args[1].strip()
    content = args[2].strip()
    await save_template(msg.from_user.id, name, content)
    await msg.answer(f"✅ Шаблон <b>{name}</b> сохранён!")

@router.message(Command("templates"))
async def list_templates_cmd(msg: Message):
    templates = await get_templates(msg.from_user.id)
    if not templates:
        await msg.answer("📭 У вас нет сохранённых шаблонов.")
        return
    
    text = "📝 <b>Ваши шаблоны:</b>\n\n"
    for idx, tmpl in enumerate(templates, 1):
        text += f"{idx}. <b>{tmpl['name']}</b>\n"
        text += f"   {tmpl['content'][:100]}...\n"
        text += f"   📅 {tmpl['created_date'][:10]}\n\n"
    
    await msg.answer(text)

@router.message(Command("usetemplate"))
async def use_template_cmd(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("❌ Укажите имя шаблона: /usetemplate <имя>")
        return
    
    template_name = args[1].strip()
    content = await get_template_content(msg.from_user.id, template_name)
    
    if not content:
        await msg.answer(f"❌ Шаблон <b>{template_name}</b> не найден.")
        return
    
    channels = await get_user_channels(msg.from_user.id)
    if not channels:
        await msg.answer("❌ Сначала добавьте канал: /addchannel @username")
        return
    
    channel_id = channels[0]['chat_id']
    result = await publish_to_channel(channel_id, content)
    
    if result['success']:
        await msg.answer(f"✅ <b>Пост по шаблону опубликован!</b>")
    else:
        await msg.answer(f"❌ Ошибка: {result['error']}")

@router.message(Command("deletetemplate"))
async def delete_template_cmd(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("❌ Укажите имя шаблона: /deletetemplate <имя>")
        return
    
    await delete_template(msg.from_user.id, args[1].strip())
    await msg.answer(f"✅ Шаблон удалён.")

# === АДМИН-КОМАНДЫ ===
@router.message(Command("ban"))
async def ban_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён.")
        return
    
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("Использование: /ban <user_id>")
        return
    
    try:
        target_id = int(args[1])
        set_user_banned(target_id, True)
        await msg.answer(f"✅ Пользователь {target_id} забанен.")
    except ValueError:
        await msg.answer("ID должен быть числом.")

@router.message(Command("unban"))
async def unban_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён.")
        return
    
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("Использование: /unban <user_id>")
        return
    
    try:
        target_id = int(args[1])
        set_user_banned(target_id, False)
        await msg.answer(f"✅ Пользователь {target_id} разбанен.")
    except ValueError:
        await msg.answer("ID должен быть числом.")

@router.message(Command("broadcast"))
async def broadcast_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён.")
        return
    
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Введите текст для рассылки.")
        return
    
    text = args[1].strip()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE banned = 0')
    users = cursor.fetchall()
    conn.close()
    
    sent = 0
    for (user_id,) in users:
        try:
            await bot.send_message(user_id, f"📢 <b>Объявление:</b>\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    
    await msg.answer(f"✅ Рассылка выполнена. Отправлено {sent} пользователям.")

@router.message(Command("statsfull"))
async def statsfull_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён.")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Топ-10 пользователей по запросам
    cursor.execute('''
        SELECT user_id, username, total_requests, trust_score
        FROM users
        ORDER BY total_requests DESC
        LIMIT 10
    ''')
    top_users = cursor.fetchall()
    
    # Статистика по каналам
    cursor.execute('''
        SELECT chat_title, COUNT(posts.id) as post_count
        FROM channels
        LEFT JOIN posts ON channels.chat_id = posts.channel_id
        WHERE channels.is_active = TRUE
        GROUP BY channels.chat_id
        ORDER BY post_count DESC
    ''')
    top_channels = cursor.fetchall()
    
    conn.close()
    
    text = "📊 <b>Полная статистика</b>\n\n"
    text += "<b>🏆 Топ пользователей:</b>\n"
    for idx, (user_id, username, requests, trust) in enumerate(top_users, 1):
        text += f"{idx}. @{username or user_id} — {requests} запросов, ⭐{trust}\n"
    
    text += "\n<b>📢 Топ каналов:</b>\n"
    for idx, (title, count) in enumerate(top_channels, 1):
        text += f"{idx}. {title[:30]} — {count} постов\n"
    
    await msg.answer(text[:4096])

# === ОБРАБОТЧИКИ INLINE КНОПОК ===
@router.callback_query(lambda c: c.data.startswith('post_text_'))
async def handle_post_text_callback(callback: CallbackQuery):
    parts = callback.data.split('_', 2)
    if len(parts) < 3:
        await callback.answer("Ошибка данных")
        return
    
    channel_id = parts[1]
    text = parts[2] if len(parts) > 2 else "Пост без текста"
    
    result = await publish_to_channel(channel_id, text)
    
    if result['success']:
        await callback.message.answer(f"✅ <b>Пост опубликован!</b>\n\n{text[:200]}")
    else:
        await callback.message.answer(f"❌ Ошибка: {result['error']}")
    
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith('post_photo_'))
async def handle_post_photo_callback(callback: CallbackQuery):
    parts = callback.data.split('_', 3)
    if len(parts) < 4:
        await callback.answer("Ошибка данных")
        return
    
    channel_id = parts[2]
    file_id = parts[3]
    caption = parts[4] if len(parts) > 4 else "📸"
    
    result = await publish_to_channel(channel_id, caption, media_file=file_id, media_type='photo')
    
    if result['success']:
        await callback.message.answer("✅ <b>Пост с фото опубликован!</b>")
    else:
        await callback.message.answer(f"❌ Ошибка: {result['error']}")
    
    await callback.answer()

@router.callback_query(lambda c: c.data.startswith('post_video_'))
async def handle_post_video_callback(callback: CallbackQuery):
    parts = callback.data.split('_', 3)
    if len(parts) < 4:
        await callback.answer("Ошибка данных")
        return
    
    channel_id = parts[2]
    file_id = parts[3]
    caption = parts[4] if len(parts) > 4 else "🎬"
    
    result = await publish_to_channel(channel_id, caption, media_file=file_id, media_type='video')
    
    if result['success']:
        await callback.message.answer("✅ <b>Пост с видео опубликован!</b>")
    else:
        await callback.message.answer(f"❌ Ошибка: {result['error']}")
    
    await callback.answer()

@router.callback_query(lambda c: c.data == 'add_channel_help')
async def handle_add_channel_help(callback: CallbackQuery):
    await callback.message.answer(
        "📢 <b>Как добавить канал:</b>\n\n"
        "1. Добавьте бота в канал как администратора\n"
        "2. Отправьте: /addchannel @username_канала\n\n"
        "Пример: /addchannel @my_channel"
    )
    await callback.answer()

@router.callback_query(lambda c: c.data == 'help_menu')
async def handle_help_menu(callback: CallbackQuery):
    await help_cmd(callback.message)
    await callback.answer()

# === ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ (ГЛАВНЫЙ) ===
@router.message(F.text)
async def handle_text(msg: Message):
    user_text = msg.text.strip()
    if not user_text:
        return
    
    # Игнорируем команды
    if user_text.startswith('/'):
        return
    
    # Проверяем, не ждем ли мы пост
    # (для упрощения — просто отправляем в AI)
    
    await msg.bot.send_chat_action(msg.chat.id, "typing")
    
    answer = await ask_groq(msg.from_user.id, user_text)
    
    # Проверяем длину ответа (Telegram лимит 4096)
    if len(answer) > 4000:
        parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
        for part in parts:
            await msg.answer(part)
    else:
        await msg.answer(answer)

# === ОБРАБОТЧИК МЕДИА ===
@router.message(F.photo)
async def handle_photo(msg: Message):
    user_text = msg.caption or "Отправил фото"
    answer = await ask_groq(msg.from_user.id, f"[ФОТО] {user_text}")
    await msg.answer(answer)

@router.message(F.document)
async def handle_document(msg: Message):
    user_text = msg.caption or "Отправил документ"
    answer = await ask_groq(msg.from_user.id, f"[ДОКУМЕНТ] {user_text}")
    await msg.answer(answer)

@router.message(F.video)
async def handle_video(msg: Message):
    user_text = msg.caption or "Отправил видео"
    answer = await ask_groq(msg.from_user.id, f"[ВИДЕО] {user_text}")
    await msg.answer(answer)

@router.message(F.voice)
async def handle_voice(msg: Message):
    await msg.answer("🎤 Голосовые сообщения пока не поддерживаются. Напишите текстом.")

# === ОБРАБОТЧИК ОШИБОК ===
@router.errors()
async def error_handler(event, error):
    logger.error(f"Ошибка: {error}")
    if isinstance(event, Message):
        try:
            await event.answer("⚠️ Произошла ошибка. Пожалуйста, попробуйте позже.")
        except:
            pass

# ============================================================
# 🚀 ЗАПУСК БОТА
# ============================================================

async def main():
    """Главная функция запуска"""
    logger.info("🚀 ZOV BOT v3.0 запускается...")
    logger.info(f"👤 Администраторы: {ADMIN_IDS}")
    
    # Проверяем наличие бота в каналах
    try:
        me = await bot.get_me()
        logger.info(f"🤖 Бот: @{me.username} (ID: {me.id})")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к Telegram: {e}")
        return
    
    # Подключаем роутер
    dp.include_router(router)
    
    try:
        logger.info("✅ Бот готов к работе!")
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("⏹️ Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
    finally:
        await bot.session.close()
        logger.info("👋 Соединение закрыто")

if __name__ == "__main__":
    asyncio.run(main())
