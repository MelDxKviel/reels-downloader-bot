# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Telegram bot that downloads videos from YouTube, Instagram Reels, TikTok, and X/Twitter using `yt-dlp`. Built with Python 3.11+, aiogram 3.x (async), SQLAlchemy async ORM, and PostgreSQL. Supports whitelist-based access control and per-user download statistics.

## Commands

```bash
# Install dependencies
uv sync

# Run locally
uv run python -m src.main

# Lint (ruff, 100 char line length, rules E/F/I/W)
uv run ruff check src/
uv run ruff format src/

# Docker (bot + PostgreSQL)
docker compose up -d
docker compose logs -f bot

# Database migrations (Alembic)
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
```

There is no test suite in this repository.

## Architecture

### Request Lifecycle

1. aiogram receives a Telegram update and passes it through two middlewares (in order):
   - `DatabaseMiddleware` — injects a `DatabaseService` instance into handler `data`
   - `UserAccessMiddleware` — silently drops messages from non-admin, non-whitelisted users
2. The update is routed to one of five routers registered in priority order: `admin_router` → `common_router` → `download_cmd_router` → `round_router` → `download_router`
3. Download handlers call `VideoDownloader.download(url)`, which checks an in-memory/JSON cache before spawning yt-dlp in a thread executor (`loop.run_in_executor`)
4. Results are uploaded to Telegram; both successes and failures are recorded via `DatabaseService.record_download()`

### Key Design Decisions

**Singleton downloader**: `VideoDownloader` is instantiated once at module level in `src/services/downloader.py` and shared across all handlers. Its in-memory cache dict is the single source of truth, backed by `downloads/cache.json` for persistence.

**Sync-to-async bridge**: `yt-dlp` is synchronous. All calls go through `asyncio.get_event_loop().run_in_executor(None, ...)` so the bot's event loop is never blocked.

**Access tiers**: Admins are defined statically in `ADMIN_USERS` (env var). Regular users are rows in the `users` table with `is_active=True`. `UserAccessMiddleware` enforces both checks before any handler runs — handlers do not need to re-check access.

**URL caching**: Cache keys are MD5 hashes of normalized URLs (UTM params, `si`, `feature`, `ref` stripped). Cache entries are validated on startup and on miss (checks file existence). Instagram failures fall back to `kkinstagram.com` mirror.

**YouTube cookies**: If `YT_COOKIES_FILE` is set, the file is validated for Netscape format before being passed to yt-dlp. Cookies are only applied to YouTube URLs, not other platforms. Invalid cookies trigger a retry without them.

**FSM command pattern**: `/download` and `/round` both use aiogram FSM. If a URL is passed inline (`/download <url>`), the download starts immediately. Otherwise the handler enters a waiting state and shows an inline cancel button. The cancel callback embeds the initiating `user_id` in its `callback_data` (`cancel_download:<id>`, `cancel_round`) so only the original user can dismiss the prompt in group chats. `download_cmd_router` is registered before `round_router` so `/download` is matched as a command even while a user is in `RoundStates.waiting_for_input`.

### Module Map

```
src/
├── main.py           — Bot startup, middleware/router registration
├── config.py         — Env vars, constants (MAX_FILE_SIZE=50MB, DOWNLOAD_TIMEOUT=300s)
├── bot/
│   ├── handlers/
│   │   ├── common.py        — /start, /help, /id, /cache, /clearcache
│   │   ├── download.py      — URL detection (regex), catch-all download flow
│   │   ├── download_cmd.py  — /download command with FSM waiting state
│   │   ├── round.py         — /round command, FFmpeg conversion to video note
│   │   └── admin.py         — /adduser, /removeuser, /users, /stats, /userstats, /adminhelp
│   └── middlewares/
│       └── access.py   — DatabaseMiddleware, UserAccessMiddleware
└── services/
    ├── downloader.py   — VideoDownloader class, yt-dlp wrapper, cache logic
    └── database.py     — DatabaseService class, User/DownloadStats ORM models
```

### Database Schema

Two SQLAlchemy models defined in `src/services/database.py`:
- `User`: `user_id` (BigInteger, unique), `is_active` (soft delete), `created_at`
- `DownloadStats`: `user_id`, `platform`, `url`, `success`, `created_at`

### Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `BOT_TOKEN` | Yes | From BotFather |
| `ADMIN_USERS` | Yes | Comma-separated Telegram user IDs |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...` |
| `DOWNLOAD_DIR` | No | Default: `downloads` |
| `YT_COOKIES_FILE` | No | Netscape-format cookies for age-restricted YouTube |
| `POSTGRES_PASSWORD` | Docker only | Default: `postgres` |

## Conventions

- HTML parse mode is set globally on the Bot instance; handlers use `<code>`, `<b>` tags directly in strings
- User-facing messages use emoji prefixes (✅ ❌ ⏳ 📤) — match existing patterns when adding handlers
- All database operations use `async with session:` context managers; never reuse sessions across calls
- Handlers receive `db: DatabaseService` from middleware data, not from direct import
- Admin commands all call `is_admin(message.from_user.id)` as first guard
