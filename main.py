#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import re
import time
import sqlite3
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Optional
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMemberAdministrator, ChatMemberOwner, Update
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from groq import Groq

# ============================================================
# КОНФИГУРАЦИЯ — ЗАМЕНИТЬ
# ============================================================

BOT_TOKEN = "8887137957:AAHsh1OjO30sRdzVe7ljhsWc5ud8DXIFbeE"
GROQ_API_KEY = "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"
MODEL = "llama-3.3-70b-versatile"
MAX_HISTORY = 15
ADMIN_IDS = [1022249544]

# ============================================================
# НАСТРОЙКИ
# ============================================================

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "bot_data.db"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / 'bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# FSM СОСТОЯНИЯ
# ============================================================

class Form(StatesGroup):
    waiting_for_post_text = State()

# ============================================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# Groq с фиксом
try:
    groq_client = Groq(api_key=GROQ_API_KEY)
except TypeError:
    class PatchedGroq(Groq):
        def __init__(self, **kwargs):
            kwargs.pop('proxies', None)
            kwargs.pop('proxy', None)
            super().__init__(**kwargs)
    groq_client = PatchedGroq(api_key=GROQ_API_KEY)

# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT UNIQUE,
            chat_title TEXT,
            chat_username TEXT,
            added_by INTEGER,
            added_date TEXT,
            is_active BOOLEAN DEFAULT TRUE
        )
    ''')
    
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
            views INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS post_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            content TEXT,
            created_date TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS custom_prompts (
            user_id INTEGER PRIMARY KEY,
            prompt TEXT,
            updated_at TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            rating INTEGER,
            comment TEXT,
            date TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

init_db()

# ============================================================
# ФУНКЦИИ БАЗЫ ДАННЫХ
# ============================================================

def get_user_data(user_id: int) -> Optional[Dict]:
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET trust_score = trust_score + ? WHERE user_id = ?', (delta, user_id))
    conn.commit()
    conn.close()

def increment_requests(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET total_requests = total_requests + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def set_user_banned(user_id: int, banned: bool):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET banned = ? WHERE user_id = ?', (banned, user_id))
    conn.commit()
    conn.close()

def save_history_message(user_id: int, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO chat_history (user_id, role, content, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (user_id, role, content, datetime.now().isoformat()))
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM chat_history WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_custom_prompt(user_id: int) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT prompt FROM custom_prompts WHERE user_id = ?', (user_id,))
    data = cursor.fetchone()
    conn.close()
    return data[0] if data else None

def set_custom_prompt(user_id: int, prompt: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO custom_prompts (user_id, prompt, updated_at)
        VALUES (?, ?, ?)
    ''', (user_id, prompt, datetime.now().isoformat()))
    conn.commit()
    conn.close()

async def get_user_channels(user_id: int) -> List[Dict]:
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

async def add_channel_to_db(chat_id: str, chat_title: str, chat_username: str, added_by: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO channels (chat_id, chat_title, chat_username, added_by, added_date, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, chat_title, chat_username, added_by, datetime.now().isoformat(), True))
    conn.commit()
    conn.close()

async def remove_channel_from_db(chat_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE channels SET is_active = FALSE WHERE chat_id = ?', (chat_id,))
    conn.commit()
    conn.close()

async def save_post_to_db(channel_id: str, message_id: int, author_id: int, 
                         content: str, media_type: str = None, media_file_id: str = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO posts (channel_id, message_id, author_id, content, media_type, media_file_id, posted_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (channel_id, message_id, author_id, content, media_type, media_file_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

async def get_post_history(channel_id: str, limit: int = 10) -> List[Dict]:
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO post_templates (user_id, name, content, created_date)
        VALUES (?, ?, ?, ?)
    ''', (user_id, name, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

async def get_templates(user_id: int) -> List[Dict]:
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT content FROM post_templates WHERE user_id = ? AND name = ?', (user_id, name))
    data = cursor.fetchone()
    conn.close()
    return data[0] if data else None

async def delete_template(user_id: int, name: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM post_templates WHERE user_id = ? AND name = ?', (user_id, name))
    conn.commit()
    conn.close()

# ============================================================
# АНТИ-ФЛУД
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
# СИСТЕМНЫЙ ПРОМПТ
# ============================================================

SYSTEM_PROMPT_BASE = (
    "Ты — AI помощник. Твой создатель — @screamsoon. "
    "Отвечай вежливо, понятно, по делу. "
    "Не используй эмодзи и смайлики в ответах слишком много. "
    "Никогда не упоминай нейросети, Groq, OpenAI, API или технологии. "
    "Ты — просто помощник. Всегда в конце ответа добавляй: Мой создатель @screamsoon. "
    "Отвечай клиенту всегда правильно и четко на его поставленный вопрос без ошибок."
)

def get_system_prompt(user_id: int) -> str:
    custom = get_custom_prompt(user_id)
    return custom if custom else SYSTEM_PROMPT_BASE

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ============================================================
# ЗАПРОС К GROQ
# ============================================================

async def ask_groq(user_id: int, user_message: str) -> str:
    user_data = get_user_data(user_id)
    if user_data and user_data.get('banned', False):
        return "⛔ Вы забанены."

    if not is_admin(user_id) and not rate_limiter.is_allowed(user_id):
        return "⚠️ Слишком много запросов. Подождите минуту."

    register_user(user_id)
    increment_requests(user_id)
    update_trust_score(user_id, 1)

    profanity_pattern = re.compile(r'(?i)(хуй|пизд|ебал|бля|сука|нах|залуп|муд|говн|сос|дроч|хер|хрен|заеб)')
    if profanity_pattern.search(user_message):
        user_message = profanity_pattern.sub('****', user_message)
        update_trust_score(user_id, -5)

    save_history_message(user_id, "user", user_message)
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

        if len(answer.split()) < 5:
            answer += " Могу подробнее, если нужно."

        save_history_message(user_id, "assistant", answer)

        if "@screamsoon" not in answer.lower():
            answer += "\n\n@ScreamSoon"

        return answer

    except Exception as e:
        logger.error(f"Ошибка Groq API: {e}")
        update_trust_score(user_id, -10)
        return "⚠️ Техническая ошибка. Повторите через несколько секунд."

# ============================================================
# ПУБЛИКАЦИЯ В КАНАЛ
# ============================================================

async def publish_to_channel(
    channel_id: str,
    text: str,
    media_file: str = None,
    media_type: str = None,
    parse_mode: str = 'HTML'
) -> Dict:
    result = {'success': False, 'message_id': None, 'error': None}
    
    try:
        bot_member = await bot.get_chat_member(channel_id, bot.id)
        if not isinstance(bot_member, (ChatMemberAdministrator, ChatMemberOwner)):
            result['error'] = 'Бот не администратор в этом канале'
            return result
        
        if media_file and media_type:
            if media_type == 'photo':
                msg = await bot.send_photo(channel_id, media_file, caption=text, parse_mode=parse_mode)
            elif media_type == 'video':
                msg = await bot.send_video(channel_id, media_file, caption=text, parse_mode=parse_mode)
            elif media_type == 'document':
                msg = await bot.send_document(channel_id, media_file, caption=text, parse_mode=parse_mode)
            else:
                msg = await bot.send_message(channel_id, text, parse_mode=parse_mode)
        else:
            msg = await bot.send_message(channel_id, text, parse_mode=parse_mode)
        
        result['success'] = True
        result['message_id'] = msg.message_id
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
# ХЕНДЛЕРЫ КОМАНД
# ============================================================

def escape_html(text: str) -> str:
    """Экранирует HTML-символы для безопасной отправки"""
    if not text:
        return text
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))

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
        [InlineKeyboardButton(text="📢 Добавить канал", callback_data="add_channel_help")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help_menu")]
    ])
    
    await msg.answer(
        "🤖 <b>AI Помощник</b>\n\n"
        "Я — AI помощник. Создатель: @screamsoon.\n\n"
        "📌 <b>Основные возможности:</b>\n"
        "• Общение и ответы на вопросы\n"
        "• Публикация постов в каналы\n"
        "• Управление каналами\n"
        "• Шаблоны постов\n\n"
        "Отправьте /help для полного списка команд.",
        reply_markup=keyboard
    )

@router.message(Command("help"))
async def help_cmd(msg: Message):
    text = (
        "🤖 <b>Помощь</b>\n\n"
        "📌 <b>Основные команды:</b>\n"
        "/start — Начать работу\n"
        "/help — Эта справка\n"
        "/clear — Очистить историю\n"
        "/profile — Ваш профиль\n\n"
        "📢 <b>Управление каналами:</b>\n"
        "/addchannel @username — Добавить канал\n"
        "/channels — Список каналов\n"
        "/post текст — Опубликовать пост\n"
        "/postphoto подпись — Опубликовать с фото\n"
        "/postvideo подпись — Опубликовать с видео\n"
        "/history — История постов\n\n"
        "📝 <b>Шаблоны:</b>\n"
        "/posttemplate имя текст — Сохранить шаблон\n"
        "/templates — Список шаблонов\n"
        "/usetemplate имя — Использовать шаблон\n\n"
        "⚙️ <b>Настройки:</b>\n"
        "/setprompt текст — Персональный промпт\n"
        "/resetprompt — Сбросить промпт\n\n"
        "👨‍💻 Создатель: @screamsoon"
    )
    await msg.answer(text)

@router.message(Command("clear"))
async def clear_cmd(msg: Message):
    clear_chat_history(msg.from_user.id)
    await msg.answer("✅ История очищена.")

@router.message(Command("profile"))
async def profile_cmd(msg: Message):
    user_data = get_user_data(msg.from_user.id)
    if not user_data:
        await msg.answer("❌ Вы не зарегистрированы.")
        return
    
    text = (
        f"📊 <b>Ваш профиль</b>\n"
        f"🆔 ID: <code>{user_data['user_id']}</code>\n"
        f"👤 Имя: {escape_html(user_data['first_name'] or 'Не указано')}\n"
        f"📝 Юзернейм: @{escape_html(user_data['username'] or 'Нет')}\n"
        f"📅 Присоединился: {user_data['joined_date'][:10]}\n"
        f"🔢 Запросов: {user_data['total_requests']}\n"
        f"⭐ Рейтинг: {user_data['trust_score']}/100\n"
        f"🚫 Статус: {'🔴 Забанен' if user_data['banned'] else '🟢 Активен'}"
    )
    await msg.answer(text)

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
        
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        if not isinstance(bot_member, (ChatMemberAdministrator, ChatMemberOwner)):
            await msg.answer("❌ Бот не администратор в этом канале.")
            return
        
        await add_channel_to_db(chat_id, chat_title, chat_username, msg.from_user.id)
        
        await msg.answer(
            f"✅ <b>Канал добавлен!</b>\n\n"
            f"📌 Название: {escape_html(chat_title)}\n"
            f"🆔 ID: <code>{chat_id}</code>\n\n"
            f"Теперь вы можете публиковать посты:\n"
            f"/post текст — текст\n"
            f"/postphoto подпись — с фото"
        )
        
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {escape_html(str(e))}")

@router.message(Command("channels"))
async def list_channels_cmd(msg: Message):
    channels = await get_user_channels(msg.from_user.id)
    
    if not channels:
        await msg.answer("📢 У вас нет добавленных каналов.\nДобавьте: /addchannel @username")
        return
    
    text = "📋 <b>Ваши каналы:</b>\n\n"
    for idx, ch in enumerate(channels, 1):
        text += f"{idx}. <b>{escape_html(ch['chat_title'])}</b>\n"
        text += f"   🆔 <code>{ch['chat_id']}</code>\n"
        text += f"   📅 Добавлен: {ch['added_date'][:10]}\n\n"
    
    await msg.answer(text)

@router.message(Command("post"))
async def post_text_cmd(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("❌ Укажите текст: /post текст")
        return
    
    text = args[1].strip()
    channels = await get_user_channels(msg.from_user.id)
    
    if not channels:
        await msg.answer("❌ Сначала добавьте канал: /addchannel @username")
        return
    
    channel_id = channels[0]['chat_id']
    result = await publish_to_channel(channel_id, text)
    
    if result['success']:
        await msg.answer(f"✅ <b>Пост опубликован!</b>")
    else:
        await msg.answer(f"❌ Ошибка: {escape_html(result['error'])}")

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
    channel_id = channels[0]['chat_id']
    result = await publish_to_channel(channel_id, caption, media_file=file_id, media_type='photo')
    
    if result['success']:
        await msg.answer("✅ <b>Пост с фото опубликован!</b>")
    else:
        await msg.answer(f"❌ Ошибка: {escape_html(result['error'])}")

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
    channel_id = channels[0]['chat_id']
    result = await publish_to_channel(channel_id, caption, media_file=file_id, media_type='video')
    
    if result['success']:
        await msg.answer("✅ <b>Пост с видео опубликован!</b>")
    else:
        await msg.answer(f"❌ Ошибка: {escape_html(result['error'])}")

@router.message(Command("history"))
async def history_cmd(msg: Message):
    channels = await get_user_channels(msg.from_user.id)
    if not channels:
        await msg.answer("❌ Сначала добавьте канал.")
        return
    
    channel_id = channels[0]['chat_id']
    posts = await get_post_history(channel_id, limit=10)
    
    if not posts:
        await msg.answer("📭 В канале пока нет постов.")
        return
    
    text = f"📊 <b>Последние 10 постов</b>\n\n"
    for idx, post in enumerate(posts, 1):
        text += f"{idx}. 📝 {escape_html(post['content'])}\n"
        text += f"   🆔 ID: {post['message_id']}\n"
        text += f"   📅 {post['posted_date'][:10]}\n\n"
    
    await msg.answer(text[:4096])

@router.message(Command("posttemplate"))
async def save_template_cmd(msg: Message):
    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        await msg.answer("📝 /posttemplate имя текст")
        return
    
    await save_template(msg.from_user.id, args[1].strip(), args[2].strip())
    await msg.answer(f"✅ Шаблон <b>{escape_html(args[1])}</b> сохранён!")

@router.message(Command("templates"))
async def list_templates_cmd(msg: Message):
    templates = await get_templates(msg.from_user.id)
    if not templates:
        await msg.answer("📭 У вас нет сохранённых шаблонов.")
        return
    
    text = "📝 <b>Ваши шаблоны:</b>\n\n"
    for idx, tmpl in enumerate(templates, 1):
        text += f"{idx}. <b>{escape_html(tmpl['name'])}</b>\n"
        text += f"   {escape_html(tmpl['content'][:100])}...\n"
        text += f"   📅 {tmpl['created_date'][:10]}\n\n"
    
    await msg.answer(text)

@router.message(Command("usetemplate"))
async def use_template_cmd(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("❌ /usetemplate имя")
        return
    
    content = await get_template_content(msg.from_user.id, args[1].strip())
    if not content:
        await msg.answer(f"❌ Шаблон не найден.")
        return
    
    channels = await get_user_channels(msg.from_user.id)
    if not channels:
        await msg.answer("❌ Сначала добавьте канал.")
        return
    
    result = await publish_to_channel(channels[0]['chat_id'], content)
    
    if result['success']:
        await msg.answer("✅ <b>Пост по шаблону опубликован!</b>")
    else:
        await msg.answer(f"❌ Ошибка: {escape_html(result['error'])}")

@router.message(Command("setprompt"))
async def set_prompt_cmd(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Использование: /setprompt текст")
        return
    
    set_custom_prompt(msg.from_user.id, args[1].strip())
    await msg.answer("✅ Персональный промпт установлен.")

@router.message(Command("resetprompt"))
async def reset_prompt_cmd(msg: Message):
    set_custom_prompt(msg.from_user.id, None)
    await msg.answer("✅ Промпт сброшен.")

@router.message(Command("ban"))
async def ban_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён.")
        return
    
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("Использование: /ban user_id")
        return
    
    try:
        set_user_banned(int(args[1]), True)
        await msg.answer(f"✅ Пользователь {args[1]} забанен.")
    except ValueError:
        await msg.answer("ID должен быть числом.")

@router.message(Command("unban"))
async def unban_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён.")
        return
    
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("Использование: /unban user_id")
        return
    
    try:
        set_user_banned(int(args[1]), False)
        await msg.answer(f"✅ Пользователь {args[1]} разбанен.")
    except ValueError:
        await msg.answer("ID должен быть числом.")

# ============================================================
# CALLBACK ОБРАБОТЧИКИ
# ============================================================

@router.callback_query(lambda c: c.data == "add_channel_help")
async def handle_add_channel_help(callback: CallbackQuery):
    await callback.message.answer(
        "📢 <b>Как добавить канал:</b>\n\n"
        "1. Добавьте бота в канал как администратора\n"
        "2. Отправьте: /addchannel @username_канала\n\n"
        "Пример: /addchannel @my_channel"
    )
    await callback.answer()

@router.callback_query(lambda c: c.data == "help_menu")
async def handle_help_menu(callback: CallbackQuery):
    await help_cmd(callback.message)
    await callback.answer()

# ============================================================
# ОБРАБОТЧИК СООБЩЕНИЙ
# ============================================================

@router.message(F.text)
async def handle_text(msg: Message):
    user_text = msg.text.strip()
    if not user_text or user_text.startswith('/'):
        return
    
    await msg.bot.send_chat_action(msg.chat.id, "typing")
    answer = await ask_groq(msg.from_user.id, user_text)
    
    if len(answer) > 4000:
        for part in [answer[i:i+4000] for i in range(0, len(answer), 4000)]:
            await msg.answer(part)
    else:
        await msg.answer(answer)

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

# ============================================================
# ОБРАБОТЧИК ОШИБОК
# ============================================================

@router.errors()
async def error_handler(event: Update, error: Exception):
    logger.error(f"Ошибка: {error}")
    if hasattr(event, 'message') and event.message:
        try:
            await event.message.answer("⚠️ Произошла ошибка. Попробуйте позже.")
        except:
            pass

# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    logger.info("🚀 Бот запускается...")
    
    try:
        me = await bot.get_me()
        logger.info(f"🤖 Бот: @{me.username}")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения: {e}")
        return
    
    dp.include_router(router)
    
    try:
        logger.info("✅ Бот готов!")
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("⏹️ Остановлен")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
