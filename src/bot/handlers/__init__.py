"""Bot handlers."""

from aiogram import Router

from .admin import router as admin_router
from .common import router as common_router
from .download import router as download_router
from .download_cmd import router as download_cmd_router
from .gif import router as gif_router
from .round import router as round_router


def get_main_router() -> Router:
    """Создает и возвращает главный роутер со всеми обработчиками."""
    main_router = Router()

    # Подключаем роутеры в порядке приоритета
    main_router.include_router(admin_router)  # Админ-команды первые
    main_router.include_router(common_router)  # Общие команды
    main_router.include_router(download_cmd_router)  # /download — до FSM-обработчиков round
    main_router.include_router(round_router)  # /round — до общего обработчика URL
    main_router.include_router(gif_router)  # /gif — до общего обработчика URL
    main_router.include_router(download_router)  # Обработка URL последняя

    return main_router
