"""
Конфигурация бота для скачивания видео.
"""

import os
from typing import List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# Поддерживаемые языки интерфейса. Должны совпадать с ключами в src/locales/.
SUPPORTED_LANGUAGES: Tuple[str, ...] = ("ru", "en")

# Язык интерфейса по умолчанию (для пользователей без сохранённого выбора).
# Если в .env указан неподдерживаемый код, откатываемся на "ru".
_default_lang_env = os.getenv("DEFAULT_LANGUAGE", "ru").strip().lower()
DEFAULT_LANGUAGE: str = _default_lang_env if _default_lang_env in SUPPORTED_LANGUAGES else "ru"

# Токен бота из BotFather
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

# Список администраторов (Telegram user IDs)
# Администраторы могут добавлять/удалять пользователей и видеть статистику
ADMIN_USERS: List[int] = []

_admin_users_env = os.getenv("ADMIN_USERS", "")
if _admin_users_env:
    ADMIN_USERS = [int(uid.strip()) for uid in _admin_users_env.split(",") if uid.strip()]

# Настройки базы данных
DATABASE_URL: str = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/downloader_bot"
)

# Директория для временного хранения скачанных видео
DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "downloads")

# Максимальный размер файла для отправки через Telegram (50MB)
MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50MB в байтах

# Максимальное время ожидания скачивания (секунды)
DOWNLOAD_TIMEOUT: int = 300  # 5 минут

# Путь к файлу cookies для YouTube (для видео 18+)
# Экспортируйте cookies из браузера с помощью расширения "Get cookies.txt LOCALLY"
YT_COOKIES_FILE: Optional[str] = os.getenv("YT_COOKIES_FILE", None)

# Путь к файлу cookies для Instagram (для доступа к приватным аккаунтам и обхода ограничений)
# Экспортируйте cookies из браузера с помощью расширения "Get cookies.txt LOCALLY"
INSTA_COOKIES_FILE: Optional[str] = os.getenv("INSTA_COOKIES_FILE", None)

# Параметры сборки GIF.
#
# Telegram отображает беззвучный H.264 mp4 как зацикленную GIF-анимацию
# (sendAnimation), но такой файл в разы легче и плавнее настоящего .gif при
# сопоставимом качестве. Поэтому "GIF" собирается как компактный mp4, а эти
# параметры управляют его плавностью, размером и весом.

# Частота кадров GIF-анимации.
_gif_fps_env = os.getenv("GIF_FPS", "30").strip()
try:
    GIF_FPS: int = max(1, int(_gif_fps_env))
except ValueError:
    GIF_FPS = 30

# Максимальная длительность GIF-анимации (секунды); видео обрезается до неё.
_gif_max_duration_env = os.getenv("GIF_MAX_DURATION", "15").strip()
try:
    GIF_MAX_DURATION: int = max(1, int(_gif_max_duration_env))
except ValueError:
    GIF_MAX_DURATION = 15

# Максимальная длинная сторона кадра (пиксели); видео не апскейлится.
_gif_max_size_env = os.getenv("GIF_MAX_SIZE", "640").strip()
try:
    GIF_MAX_SIZE: int = max(16, int(_gif_max_size_env))
except ValueError:
    GIF_MAX_SIZE = 640

# Качество H.264 (CRF): меньше — лучше и тяжелее. Разумный диапазон ~18–32.
_gif_crf_env = os.getenv("GIF_CRF", "28").strip()
try:
    GIF_CRF: int = min(51, max(0, int(_gif_crf_env)))
except ValueError:
    GIF_CRF = 28

# ID чата для публикации видео в inline-режиме.
# Telegram запрещает загружать новые файлы в editMessageMedia с inline_message_id —
# принимаются только file_id или URL. Бот предварительно выгружает видео в этот чат
# (канал, супергруппу или личку админа), получает file_id и затем подменяет им заглушку.
# Если не задано — используется первый ID из ADMIN_USERS.
_video_storage_env = os.getenv("VIDEO_STORAGE_CHAT_ID", "").strip()
VIDEO_STORAGE_CHAT_ID: Optional[int] = int(_video_storage_env) if _video_storage_env else None

# === Автоочистка кэша ===
#
# Фоновая задача периодически удаляет из кэша записи старше заданного возраста
# (вместе с их файлами на диске), чтобы папка downloads не росла бесконечно.
# Включение и срок хранения настраиваются администратором в рантайме через
# /cache (хранятся в bot_settings); значения ниже — дефолты до первой настройки
# и параметры, которые меняются только через переменные окружения.

# Включена ли автоочистка по умолчанию (пока админ не переключил её в /cache).
CACHE_AUTOCLEAN_DEFAULT: bool = os.getenv("CACHE_AUTOCLEAN_DEFAULT", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Максимальный возраст записи кэша по умолчанию (часы): записи старше удаляются.
_cache_max_age_env = os.getenv("CACHE_MAX_AGE_HOURS", "168").strip()
try:
    CACHE_MAX_AGE_HOURS: int = max(1, int(_cache_max_age_env))
except ValueError:
    CACHE_MAX_AGE_HOURS = 168

# Как часто фоновая задача проверяет кэш (секунды). Минимум — минута.
_cache_interval_env = os.getenv("CACHE_CLEANUP_INTERVAL", "3600").strip()
try:
    CACHE_CLEANUP_INTERVAL: int = max(60, int(_cache_interval_env))
except ValueError:
    CACHE_CLEANUP_INTERVAL = 3600

# Пресеты срока хранения (часы) для переключения кнопкой в /cache:
# 6 ч · 12 ч · 1 день · 3 дня · 7 дней · 30 дней.
CACHE_MAX_AGE_PRESETS: Tuple[int, ...] = (6, 12, 24, 72, 168, 720)
