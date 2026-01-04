"""
Сервис для работы с базой данных.
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, Boolean, DateTime, Text

from src.config import DATABASE_URL

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Базовый класс для моделей."""
    pass


class User(Base):
    """Модель пользователя."""
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    

class DownloadStats(Base):
    """Модель статистики скачиваний."""
    __tablename__ = "download_stats"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    platform: Mapped[str] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
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
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if user:
                user.is_active = False
                await session.commit()
                return True
            return False
    
    async def get_user(self, user_id: int) -> Optional[User]:
        """Получает пользователя по ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            return result.scalar_one_or_none()
    
    async def get_all_users(self) -> List[User]:
        """Получает всех пользователей."""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).order_by(User.created_at.desc())
            )
            return list(result.scalars().all())
    
    async def is_user_allowed(self, user_id: int) -> bool:
        """Проверяет, разрешен ли пользователь."""
        user = await self.get_user(user_id)
        return user is not None and user.is_active
    
    # === Статистика ===
    
    async def record_download(
        self,
        user_id: int,
        platform: str,
        url: str,
        success: bool = True
    ) -> None:
        """Записывает статистику скачивания."""
        async with self.async_session() as session:
            stat = DownloadStats(
                user_id=user_id,
                platform=platform,
                url=url,
                success=success
            )
            session.add(stat)
            await session.commit()
    
    async def get_global_stats(
        self,
        since: Optional[datetime] = None
    ) -> Dict[str, Any]:
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
            query = select(func.count(DownloadStats.id)).where(
                DownloadStats.success == True
            )
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
            query = select(
                DownloadStats.platform,
                func.count(DownloadStats.id)
            ).group_by(DownloadStats.platform)
            if filters:
                query = query.where(and_(*filters))
            result = await session.execute(query)
            by_platform = {row[0]: row[1] for row in result.all()}
            
            return {
                "total_downloads": total_downloads,
                "successful_downloads": successful_downloads,
                "failed_downloads": total_downloads - successful_downloads,
                "active_users": active_users,
                "by_platform": by_platform
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
                and_(*filters, DownloadStats.success == True)
            )
            result = await session.execute(query)
            successful_downloads = result.scalar() or 0
            
            # По платформам
            query = select(
                DownloadStats.platform,
                func.count(DownloadStats.id)
            ).where(and_(*filters)).group_by(DownloadStats.platform)
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
                "last_activity": last_activity
            }

