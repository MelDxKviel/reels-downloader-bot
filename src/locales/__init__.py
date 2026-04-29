"""Translation message dictionaries per locale."""

from .en import MESSAGES as EN_MESSAGES
from .ru import MESSAGES as RU_MESSAGES

LOCALE_MESSAGES = {
    "en": EN_MESSAGES,
    "ru": RU_MESSAGES,
}

__all__ = ["LOCALE_MESSAGES"]
