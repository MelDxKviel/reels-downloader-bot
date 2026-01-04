"""
Конфигурация бота для скачивания видео.
"""
import os
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

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
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/downloader_bot"
)

# Директория для временного хранения скачанных видео
DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "downloads")

# Максимальный размер файла для отправки через Telegram (50MB)
MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50MB в байтах

# Максимальное время ожидания скачивания (секунды)
DOWNLOAD_TIMEOUT: int = 300  # 5 минут

# Путь к файлу cookies для YouTube (для видео 18+)
# Экспортируйте cookies из браузера с помощью расширения "Get cookies.txt"
YT_COOKIES_FILE: Optional[str] = os.getenv("YT_COOKIES_FILE", None)

