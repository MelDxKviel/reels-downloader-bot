"""Translation message dictionaries per locale.

Сами переводы лежат рядом в ``<lang>.json``. Файлы читаются один раз при
импорте модуля и кэшируются в ``LOCALE_MESSAGES`` как обычный словарь —
обращения к переводу из хендлеров не дёргают диск.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Mapping

_LOCALES_DIR = Path(__file__).resolve().parent


def _load_locales() -> Dict[str, Mapping[str, str]]:
    locales: Dict[str, Mapping[str, str]] = {}
    for path in sorted(_LOCALES_DIR.glob("*.json")):
        with open(path, "r", encoding="utf-8") as f:
            locales[path.stem] = json.load(f)
    return locales


LOCALE_MESSAGES: Dict[str, Mapping[str, str]] = _load_locales()

__all__ = ["LOCALE_MESSAGES"]
