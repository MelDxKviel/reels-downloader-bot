"""Cookies implementation for the downloader."""

import http.cookiejar
import logging
import os
import shutil
import tempfile
from typing import Optional

from src.config import INSTA_COOKIES_FILE, YT_COOKIES_FILE

logger = logging.getLogger(__name__)


class CookieMixin:
    def _looks_like_netscape_cookies_file(self, path: str) -> bool:
        """
        Быстрая проверка, что файл похож на cookies в Netscape формате (требуется yt-dlp).
        """
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(15):
                    line = f.readline()
                    if not line:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    lower = stripped.lower()
                    if lower.startswith("# netscape"):
                        return True
                    if stripped.startswith("#"):
                        continue
                    if "\t" in stripped:
                        parts = stripped.split("\t")
                        if len(parts) >= 7:
                            return True
                    break
        except OSError:
            return False
        return False

    def _get_youtube_cookiefile(self) -> Optional[str]:
        if not YT_COOKIES_FILE:
            return None
        if not os.path.exists(YT_COOKIES_FILE):
            return None
        if not self._looks_like_netscape_cookies_file(YT_COOKIES_FILE):
            logger.warning(
                "YT_COOKIES_FILE задан, но файл не похож на Netscape cookies формат — игнорирую: %s",
                YT_COOKIES_FILE,
            )
            return None
        return YT_COOKIES_FILE

    def _get_instagram_cookiefile(self) -> Optional[str]:
        if not INSTA_COOKIES_FILE:
            return None
        if not os.path.exists(INSTA_COOKIES_FILE):
            return None
        if not self._looks_like_netscape_cookies_file(INSTA_COOKIES_FILE):
            logger.warning(
                "INSTA_COOKIES_FILE задан, но файл не похож на Netscape cookies формат — игнорирую: %s",
                INSTA_COOKIES_FILE,
            )
            return None
        return INSTA_COOKIES_FILE

    def _load_instagram_cookie_jar(self) -> Optional[http.cookiejar.MozillaCookieJar]:
        cookiefile = self._get_instagram_cookiefile()
        if not cookiefile:
            return None
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(cookiefile, ignore_discard=True, ignore_expires=True)
            return jar
        except Exception as e:
            logger.warning("Не удалось загрузить Instagram cookies для HTML-скрапинга: %s", e)
            return None

    def _prepare_ydl_cookie_snapshot(self, ydl_opts: dict) -> tuple[dict, Optional[str]]:
        """Give yt-dlp a private writable copy of a configured cookie jar.

        yt-dlp saves its cookie jar when ``YoutubeDL`` closes, even when the
        caller only intended to read an existing file. Production cookie files
        are deliberately mounted read-only, so every download gets a unique
        snapshot in the job directory, so hard timeouts also clean it up. Keeping the snapshot local to
        ``_download_sync`` also prevents concurrent downloads from rewriting the
        same jar.
        """

        prepared_opts = ydl_opts.copy()
        cookiefile = prepared_opts.get("cookiefile")
        if not isinstance(cookiefile, (str, os.PathLike)):
            return prepared_opts, None

        source_path = os.fspath(cookiefile)
        if not os.path.isfile(source_path):
            # Preserve the old behaviour for a file that disappeared between
            # option construction and execution: yt-dlp will report/retry it.
            return prepared_opts, None

        fd, snapshot_path = tempfile.mkstemp(
            prefix=".reels-downloader-cookie-",
            suffix=".txt",
            dir=self.download_dir,
        )
        os.close(fd)
        try:
            # copyfile copies bytes, not the source's read-only mode.
            shutil.copyfile(source_path, snapshot_path)
            os.chmod(snapshot_path, 0o600)
        except Exception:
            try:
                os.remove(snapshot_path)
            except OSError:
                pass
            raise

        prepared_opts["cookiefile"] = snapshot_path
        return prepared_opts, snapshot_path
