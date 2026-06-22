"""
Сервис для работы с базой данных.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import DATABASE_URL, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

# Ключи bot_settings для автоочистки кэша (key/value-настройки бота).
SETTING_CACHE_AUTOCLEAN = "cache.autoclean.enabled"
SETTING_CACHE_MAX_AGE_HOURS = "cache.autoclean.max_age_hours"


def _utcnow() -> datetime:
    """Return a naive UTC datetime, replacing deprecated ``datetime.utcnow()``.

    Existing rows in the DB use naive UTC timestamps, so we drop tzinfo to
    keep the column values comparable without a migration.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Базовый класс для моделей."""

    pass


class User(Base):
    """Модель пользователя."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class DownloadStats(Base):
    """Модель статистики скачиваний."""

    __tablename__ = "download_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    platform: Mapped[str] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class UserPreference(Base):
    """Пользовательские настройки (язык и т.п.).

    Хранится отдельно от ``users``, чтобы админы (которых нет в whitelist)
    тоже могли сохранить язык, не получая побочного эффекта в виде записи в
    таблице доступа.
    """

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    language: Mapped[str] = mapped_column(String(8), default=DEFAULT_LANGUAGE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class BotSetting(Base):
    """Глобальные настройки бота (фича-флаги и т.п.) в виде key/value.

    Используется для динамического включения/отключения экспериментальных фич
    из админки без рестарта.
    """

    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class DatabaseService:
    """Сервис для работы с базой данных."""

    def __init__(self, database_url: str = DATABASE_URL):
        self.engine = create_async_engine(database_url, echo=False)
        self.async_session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init_db(self) -> None:
        """Инициализирует базу данных (создает таблицы)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ База данных инициализирована")

    async def close(self) -> None:
        """Закрывает соединение с базой данных."""
        await self.engine.dispose()

    # === Управление пользователями ===

    async def add_user(self, user_id: int) -> bool:
        """
        Добавляет пользователя в базу данных.

        Returns:
            True если пользователь добавлен, False если уже существует
        """
        async with self.async_session() as session:
            # Проверяем, существует ли пользователь
            result = await session.execute(select(User).where(User.user_id == user_id))
            existing = result.scalar_one_or_none()

            if existing:
                if not existing.is_active:
                    existing.is_active = True
                    await session.commit()
                    return True
                return False

            # Добавляем нового пользователя
            user = User(user_id=user_id)
            session.add(user)
            await session.commit()
            return True

    async def remove_user(self, user_id: int) -> bool:
        """
        Удаляет пользователя (деактивирует).

        Returns:
            True если пользователь удален, False если не найден
        """
        async with self.async_session() as session:
            result = await session.execute(select(User).where(User.user_id == user_id))
            user = result.scalar_one_or_none()

            if user:
                user.is_active = False
                await session.commit()
                return True
            return False

    async def get_user(self, user_id: int) -> Optional[User]:
        """Получает пользователя по ID."""
        async with self.async_session() as session:
            result = await session.execute(select(User).where(User.user_id == user_id))
            return result.scalar_one_or_none()

    async def get_all_users(self) -> List[User]:
        """Получает всех пользователей."""
        async with self.async_session() as session:
            result = await session.execute(select(User).order_by(User.created_at.desc()))
            return list(result.scalars().all())

    async def is_user_allowed(self, user_id: int) -> bool:
        """Проверяет, разрешен ли пользователь."""
        user = await self.get_user(user_id)
        return user is not None and user.is_active

    # === Языковые предпочтения ===

    async def get_user_language(self, user_id: int) -> Optional[str]:
        """Возвращает сохранённый язык пользователя или None, если не задан."""
        async with self.async_session() as session:
            result = await session.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            )
            pref = result.scalar_one_or_none()
            return pref.language if pref else None

    async def set_user_language(self, user_id: int, language: str) -> bool:
        """Сохраняет язык пользователя.

        Возвращает True при успехе, False если язык не из списка поддерживаемых.
        """
        if language not in SUPPORTED_LANGUAGES:
            return False
        async with self.async_session() as session:
            result = await session.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            )
            pref = result.scalar_one_or_none()
            if pref is None:
                pref = UserPreference(user_id=user_id, language=language)
                session.add(pref)
            else:
                pref.language = language
            await session.commit()
            return True

    # === Глобальные настройки / feature flags ===

    async def get_setting(self, key: str) -> Optional[str]:
        """Возвращает значение глобальной настройки или None, если ключа нет."""
        async with self.async_session() as session:
            result = await session.execute(select(BotSetting).where(BotSetting.key == key))
            row = result.scalar_one_or_none()
            return row.value if row else None

    async def set_setting(self, key: str, value: str) -> None:
        """Сохраняет (или обновляет) значение глобальной настройки.

        ``bot_settings.key`` имеет UNIQUE-индекс, поэтому при гонке двух
        одновременных INSERT (например, два быстрых клика по /features или
        одновременные тогглы от разных админов) один из коммитов получит
        ``IntegrityError``. Ловим его, откатываем сессию и завершаем
        операцию обновлением — после rollback строка уже точно есть.
        """
        async with self.async_session() as session:
            result = await session.execute(select(BotSetting).where(BotSetting.key == key))
            row = result.scalar_one_or_none()
            if row is None:
                session.add(BotSetting(key=key, value=value))
                try:
                    await session.commit()
                    return
                except IntegrityError:
                    await session.rollback()
                    result = await session.execute(select(BotSetting).where(BotSetting.key == key))
                    row = result.scalar_one_or_none()
                    if row is None:
                        raise
            row.value = value
            await session.commit()

    async def is_feature_enabled(self, name: str, default: bool = False) -> bool:
        """Возвращает True, если в bot_settings ключ feature.<name> = "1"."""
        stored = await self.get_setting(f"feature.{name}")
        if stored is None:
            return default
        return stored == "1"

    async def set_feature_enabled(self, name: str, enabled: bool) -> None:
        """Сохраняет включение/отключение фичи."""
        await self.set_setting(f"feature.{name}", "1" if enabled else "0")

    # === Автоочистка кэша ===

    async def get_cache_autoclean(self, default: bool = False) -> bool:
        """Включена ли автоочистка кэша (значение из bot_settings)."""
        stored = await self.get_setting(SETTING_CACHE_AUTOCLEAN)
        if stored is None:
            return default
        return stored == "1"

    async def set_cache_autoclean(self, enabled: bool) -> None:
        """Сохраняет включение/отключение автоочистки кэша."""
        await self.set_setting(SETTING_CACHE_AUTOCLEAN, "1" if enabled else "0")

    async def get_cache_max_age_hours(self, default: int) -> int:
        """Срок хранения записи кэша (часы) из bot_settings или ``default``."""
        stored = await self.get_setting(SETTING_CACHE_MAX_AGE_HOURS)
        if stored is None:
            return default
        try:
            value = int(stored)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    async def set_cache_max_age_hours(self, hours: int) -> None:
        """Сохраняет срок хранения записи кэша (часы)."""
        await self.set_setting(SETTING_CACHE_MAX_AGE_HOURS, str(hours))

    # === Статистика ===

    async def record_download(
        self, user_id: int, platform: str, url: str, success: bool = True
    ) -> None:
        """Записывает статистику скачивания."""
        async with self.async_session() as session:
            stat = DownloadStats(user_id=user_id, platform=platform, url=url, success=success)
            session.add(stat)
            await session.commit()

    async def get_global_stats(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """Получает общую статистику."""
        async with self.async_session() as session:
            # Базовый фильтр
            filters = []
            if since:
                filters.append(DownloadStats.created_at >= since)

            # Общее количество загрузок
            query = select(func.count(DownloadStats.id))
            if filters:
                query = query.where(and_(*filters))
            result = await session.execute(query)
            total_downloads = result.scalar() or 0

            # Успешные загрузки
            query = select(func.count(DownloadStats.id)).where(DownloadStats.success.is_(True))
            if filters:
                query = query.where(and_(*filters))
            result = await session.execute(query)
            successful_downloads = result.scalar() or 0

            # Уникальные пользователи
            query = select(func.count(func.distinct(DownloadStats.user_id)))
            if filters:
                query = query.where(and_(*filters))
            result = await session.execute(query)
            active_users = result.scalar() or 0

            # По платформам
            query = select(DownloadStats.platform, func.count(DownloadStats.id)).group_by(
                DownloadStats.platform
            )
            if filters:
                query = query.where(and_(*filters))
            result = await session.execute(query)
            by_platform = {row[0]: row[1] for row in result.all()}

            return {
                "total_downloads": total_downloads,
                "successful_downloads": successful_downloads,
                "failed_downloads": total_downloads - successful_downloads,
                "active_users": active_users,
                "by_platform": by_platform,
            }

    async def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Получает статистику по пользователю."""
        async with self.async_session() as session:
            filters = [DownloadStats.user_id == user_id]

            # Общее количество загрузок
            query = select(func.count(DownloadStats.id)).where(and_(*filters))
            result = await session.execute(query)
            total_downloads = result.scalar() or 0

            # Успешные загрузки
            query = select(func.count(DownloadStats.id)).where(
                and_(*filters, DownloadStats.success.is_(True))
            )
            result = await session.execute(query)
            successful_downloads = result.scalar() or 0

            # По платформам
            query = (
                select(DownloadStats.platform, func.count(DownloadStats.id))
                .where(and_(*filters))
                .group_by(DownloadStats.platform)
            )
            result = await session.execute(query)
            by_platform = {row[0]: row[1] for row in result.all()}

            # Последняя активность
            query = select(func.max(DownloadStats.created_at)).where(and_(*filters))
            result = await session.execute(query)
            last_activity = result.scalar()

            return {
                "total_downloads": total_downloads,
                "successful_downloads": successful_downloads,
                "failed_downloads": total_downloads - successful_downloads,
                "by_platform": by_platform,
                "last_activity": last_activity,
            }
