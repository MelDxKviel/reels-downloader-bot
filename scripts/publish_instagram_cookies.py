#!/usr/bin/env python3
"""Publish a filtered Instagram cookie file through GitHub Actions.

The script never connects to the production server. It keeps only cookies for
``instagram.com``, validates an active ``sessionid``, stores a compressed value
in a GitHub Actions secret through ``gh``, and starts the cookie-sync workflow.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

SECRET_NAME = "INSTA_COOKIES_GZIP_B64"
WORKFLOW_NAME = "sync-cookies.yml"
WORKFLOW_REF = "main"
REPOSITORY = "MelDxKviel/reels-downloader-bot"
GITHUB_SECRET_LIMIT = 48 * 1024
COOKIE_CARRIER_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"Ошибка: {message}")


def _read_cookie_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        _fail(f"файл не найден: {path}")
    except (OSError, UnicodeError) as exc:
        _fail(f"не удалось прочитать {path}: {exc}")


def _extract_from_browser(browser: str) -> str:
    with tempfile.TemporaryDirectory(prefix="instagram-cookie-export-") as temp_dir:
        cookie_file = Path(temp_dir) / "browser-cookies.txt"
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--cookies-from-browser",
            browser,
            "--cookies",
            str(cookie_file),
            "--skip-download",
            "--no-playlist",
            "--ignore-errors",
            "--no-warnings",
            COOKIE_CARRIER_URL,
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            _fail("yt-dlp не успел прочитать cookies из браузера за 180 секунд")
        except OSError as exc:
            _fail(f"не удалось запустить yt-dlp: {exc}")

        if not cookie_file.is_file() or cookie_file.stat().st_size == 0:
            _fail(
                "yt-dlp не получил cookies. Закрой браузер на время чтения или "
                "экспортируй Netscape cookies в файл"
            )
        return _read_cookie_file(cookie_file)


def _is_instagram_domain(domain: str) -> bool:
    normalized = domain.lstrip(".").lower()
    return normalized == "instagram.com" or normalized.endswith(".instagram.com")


def _filter_and_validate(cookie_jar: str) -> tuple[bytes, int]:
    kept: list[str] = []
    valid_session = False
    now = int(time.time())

    for raw_line in cookie_jar.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line:
            continue

        probe = line
        if line.startswith("#HttpOnly_"):
            probe = line.removeprefix("#HttpOnly_")
        elif line.startswith("#"):
            continue

        fields = probe.split("\t")
        if len(fields) < 7 or not _is_instagram_domain(fields[0]):
            continue

        kept.append(line)
        if fields[5] != "sessionid" or not fields[6]:
            continue
        try:
            expires_at = int(fields[4])
        except ValueError:
            continue
        valid_session = valid_session or expires_at == 0 or expires_at > now

    if not kept:
        _fail("в источнике нет cookies домена instagram.com")
    if not valid_session:
        _fail("нет активной cookie sessionid; сначала войди в Instagram в браузере")

    header = "# Netscape HTTP Cookie File\n# Filtered for Instagram deployment\n"
    payload = (header + "\n".join(kept) + "\n").encode()
    return payload, len(kept)


def _gh_command(*arguments: str) -> list[str]:
    executable = shutil.which("gh")
    if not executable:
        _fail("GitHub CLI (gh) не найден")
    return [executable, *arguments, "--repo", REPOSITORY]


def _run_gh(command: list[str], *, input_text: str | None = None) -> None:
    try:
        result = subprocess.run(
            command, input=input_text, text=True, capture_output=True, check=False
        )
    except OSError as exc:
        _fail(f"не удалось запустить GitHub CLI: {exc}")
    if result.returncode != 0:
        _fail("GitHub CLI завершился с ошибкой")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Безопасно обновить Instagram cookies через GitHub Actions"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--cookie-file",
        type=Path,
        help="Netscape cookies.txt, экспортированный из браузера",
    )
    source.add_argument(
        "--browser",
        help="браузер для yt-dlp --cookies-from-browser, например firefox или brave",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только извлечь, отфильтровать и проверить cookies",
    )
    parser.add_argument(
        "--no-trigger",
        action="store_true",
        help="обновить secret, но не запускать workflow",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    raw_cookie_jar = (
        _read_cookie_file(args.cookie_file)
        if args.cookie_file is not None
        else _extract_from_browser(args.browser)
    )
    cookie_payload, cookie_count = _filter_and_validate(raw_cookie_jar)
    archive = gzip.compress(cookie_payload, compresslevel=9, mtime=0)
    encoded = base64.b64encode(archive).decode("ascii")

    if len(encoded.encode()) > GITHUB_SECRET_LIMIT:
        _fail("сжатый cookie secret превышает лимит GitHub в 48 KB")

    print(f"Проверено Instagram cookies: {cookie_count}; sessionid активен")
    if args.dry_run:
        print("Dry-run: GitHub secret и сервер не изменялись")
        return

    _run_gh(
        _gh_command("secret", "set", SECRET_NAME),
        input_text=encoded,
    )
    print("Instagram cookies опубликованы в GitHub Actions")

    if not args.no_trigger:
        _run_gh(
            _gh_command(
                "workflow",
                "run",
                WORKFLOW_NAME,
                "--ref",
                WORKFLOW_REF,
            )
        )
        print(f"Workflow {WORKFLOW_NAME} запущен")


if __name__ == "__main__":
    main()
