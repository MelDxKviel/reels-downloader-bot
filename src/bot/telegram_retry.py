"""Small Telegram API helpers shared by regular and inline delivery paths."""

import asyncio
import logging
from typing import Awaitable, Callable, Optional, TypeVar, cast

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError

_RETRY_DELAYS = (1.0, 2.0)
TELEGRAM_UPLOAD_TIMEOUT = 180

T = TypeVar("T")

logger = logging.getLogger(__name__)


def telegram_duration(value: Optional[float]) -> Optional[int]:
    """Convert extractor duration to the positive integer expected by Telegram."""

    if value is None or value <= 0:
        return None
    return max(1, int(round(value)))


async def retry_transient_telegram(operation: Callable[[], Awaitable[T]], operation_name: str) -> T:
    """Retry Telegram network/5xx failures (including 504) with short backoff."""

    had_transient_error = False
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return await operation()
        except (TelegramNetworkError, TelegramServerError) as e:
            had_transient_error = True
            if attempt >= len(_RETRY_DELAYS):
                raise
            delay = _RETRY_DELAYS[attempt]
            logger.warning(
                "%s: временная ошибка Telegram (%s), повтор через %.1f с",
                operation_name,
                e,
                delay,
            )
            await asyncio.sleep(delay)
        except TelegramBadRequest as e:
            # A 504 response can arrive after Telegram already applied an edit.
            # Retrying then yields "message is not modified", which is success.
            if had_transient_error and "message is not modified" in str(e).lower():
                return cast(T, None)
            raise

    raise RuntimeError("unreachable")
