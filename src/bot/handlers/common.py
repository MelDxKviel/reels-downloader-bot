"""
Общие команды бота: /start, /help, /id, /cache, /clearcache
"""
import os

from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from src.services.downloader import downloader

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start."""
    user = message.from_user
    await message.answer(
        f"👋 Привет, <b>{user.full_name}</b>!\n\n"
        "🎬 Я помогу скачать видео с популярных платформ:\n"
        "• YouTube\n"
        "• Instagram Reels\n"
        "• TikTok\n"
        "• X (Twitter)\n\n"
        "📎 Просто отправь мне ссылку на видео, и я пришлю его тебе!\n\n"
        "⚠️ <i>Ограничение: максимальный размер видео — 50 МБ</i>"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Обработчик команды /help."""
    await message.answer(
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Скопируй ссылку на видео\n"
        "2️⃣ Отправь её мне в чат\n"
        "3️⃣ Подожди, пока я скачаю и отправлю видео\n\n"
        "📱 <b>Поддерживаемые платформы:</b>\n"
        "• <b>YouTube</b> — youtube.com, youtu.be\n"
        "• <b>Instagram</b> — instagram.com/reel/, instagram.com/p/\n"
        "• <b>TikTok</b> — tiktok.com, vm.tiktok.com\n"
        "• <b>X/Twitter</b> — twitter.com, x.com\n\n"
        "⚠️ <b>Ограничения:</b>\n"
        "• Максимальный размер видео: 50 МБ\n"
        "• Приватные видео не поддерживаются\n\n"
        "💡 <b>Совет:</b> Если видео слишком большое, попробуй найти его в более низком качестве"
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """Показывает ID пользователя."""
    user = message.from_user
    await message.answer(
        f"👤 <b>Информация о вас:</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📛 Имя: {user.full_name}\n"
        f"🔗 Username: @{user.username or 'не указан'}"
    )


@router.message(Command("cache"))
async def cmd_cache(message: Message) -> None:
    """Показывает информацию о кэше."""
    cache_size = len(downloader.cache)
    total_size = 0
    for data in downloader.cache.values():
        file_path = data.get('file_path')
        if file_path and os.path.exists(file_path):
            total_size += os.path.getsize(file_path)
    
    size_mb = total_size / (1024 * 1024)
    await message.answer(
        f"💾 <b>Информация о кэше:</b>\n\n"
        f"📁 Видео в кэше: {cache_size}\n"
        f"📊 Размер: {size_mb:.1f} МБ\n\n"
        f"<i>Используйте /clearcache для очистки</i>"
    )


@router.message(Command("clearcache"))
async def cmd_clearcache(message: Message) -> None:
    """Очищает кэш видео."""
    count = downloader.clear_cache()
    await message.answer(
        f"🗑 Кэш очищен!\n"
        f"Удалено файлов: {count}"
    )

