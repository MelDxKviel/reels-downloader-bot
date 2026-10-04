"""Check the built image against a disposable PostgreSQL database without polling Telegram."""

import asyncio
import os
import subprocess
import sys

import src.main  # noqa: F401 -- import all handlers and their runtime dependencies
from src.services.database import DatabaseService


async def main() -> None:
    assert sys.version_info[:3] == (3, 14, 8), sys.version
    for executable in ("ffmpeg", "ffprobe"):
        subprocess.run([executable, "-version"], check=True, capture_output=True, timeout=10)

    db = DatabaseService(os.environ["SMOKE_DATABASE_URL"])
    try:
        await db.init_db()
        assert await db.add_user(314)
        assert await db.is_user_allowed(314)
        await db.set_user_language(314, "en")
        assert await db.get_user_language(314) == "en"
        assert await db.remove_user(314)
        assert not await db.is_user_allowed(314)
    finally:
        await db.close()
    print("Python 3.14.8, application imports, FFmpeg and PostgreSQL: OK")


if __name__ == "__main__":
    asyncio.run(main())
