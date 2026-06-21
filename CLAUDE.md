# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Telegram bot that downloads videos from YouTube, Instagram Reels, TikTok, and X/Twitter using `yt-dlp`. Built with Python 3.11+, aiogram 3.x (async), SQLAlchemy async ORM, and PostgreSQL. Supports whitelist-based access control, per-user download statistics, format conversion (MP3, voice, GIF, video note), inline-mode downloads, and a bilingual interface (Russian / English) with per-user language preference.

## Commands

```bash
# Install dependencies
uv sync

# Run locally
uv run python -m src.main

# Lint (ruff, 100 char line length, rules E/F/I/W)
uv run ruff check src/
uv run ruff format src/

# Run tests
uv run pytest

# Docker (bot + PostgreSQL)
docker compose up -d
docker compose logs -f bot

# Local Docker build (from Dockerfile instead of GHCR image)
docker compose -f docker-compose.local.yml up -d
```

Schema is auto-created on startup via `Base.metadata.create_all()` — no migrations needed.

## Architecture

### Request Lifecycle

1. aiogram receives a Telegram update and passes it through three middlewares (in order):
   - `DatabaseMiddleware` — injects a `DatabaseService` instance into handler `data`
   - `UserAccessMiddleware` — silently drops messages from non-admin, non-whitelisted users
   - `LocaleMiddleware` — resolves the user's language (DB preference → Telegram `language_code` → `DEFAULT_LANGUAGE`) and injects `lang: str` and `t: Translator` into handler `data`
2. The update is routed through routers in priority order: `admin_router` → `language_router` → `common_router` → `download_cmd_router` → `mp3_router` → `voice_router` → `gif_router` → `round_router` → `download_router` → `inline_router`
3. Download handlers call `VideoDownloader.download(url)`, which checks an in-memory/JSON cache before spawning yt-dlp in a thread executor (`loop.run_in_executor`)
4. Results are uploaded to Telegram; both successes and failures are recorded via `DatabaseService.record_download()`

### Key Design Decisions

**Singleton downloader**: `VideoDownloader` is instantiated once at module level in `src/services/downloader.py` and shared across all handlers. Its in-memory cache dict is the single source of truth, backed by `downloads/cache.json` for persistence. The cache stores separate `video_file_id`, `photo_file_id`, and `mp3_file_id` entries for inline-mode reuse.

**Sync-to-async bridge**: `yt-dlp` and FFmpeg are synchronous/subprocess-based. yt-dlp calls go through `asyncio.get_event_loop().run_in_executor(None, ...)`. FFmpeg uses `asyncio.create_subprocess_exec()`. Neither blocks the event loop.

**Access tiers**: Admins are defined statically in `ADMIN_USERS` (env var). Regular users are rows in the `users` table with `is_active=True`. `UserAccessMiddleware` enforces both checks before any handler runs — handlers do not need to re-check access. Denied `CallbackQuery` events are acknowledged silently via `_terminate_callback()` to prevent the 30-second spinner.

**URL caching**: Cache keys are MD5 hashes of normalized URLs (UTM params, `si`, `feature`, `ref` stripped), implemented in `src/services/url_utils.py`. Cache entries are validated on startup and on miss (checks file existence). Instagram auth failures fall back to `kkinstagram.com` mirror.

**Platform cookies**: `YT_COOKIES_FILE` and `INSTA_COOKIES_FILE` env vars point to Netscape-format cookie files for YouTube and Instagram respectively. Both are validated on use via `_looks_like_netscape_cookies_file()`. Cookies are applied per-platform in `_get_ydl_opts()`. Invalid or missing files are silently skipped with a warning; if yt-dlp rejects the cookiefile at runtime, the download is retried without it. See `COOKIES_GUIDE.md` for export instructions.

**FSM command pattern**: All conversion commands (`/download`, `/round`, `/mp3`, `/voice`, `/gif`) use aiogram FSM. If a URL is passed inline, the download starts immediately. Otherwise the handler enters a waiting state and shows an inline cancel button. Cancel callbacks embed the initiating `user_id` in `callback_data` so only the original user can dismiss the prompt in group chats. `download_cmd_router` is registered before conversion routers so `/download` is matched as a command even while a user is in another FSM state.

**Instagram photo handling**: yt-dlp downloads Instagram photo posts as 0-second videos. The bot detects this, extracts the first frame via FFmpeg, and sends it as a photo. Carousels are limited to 10 items (`MAX_CAROUSEL_ITEMS`). Auth failures trigger a retry via the `kkinstagram.com` mirror.

**Instagram carousel as a native carousel (Bot API 10.1)**: A multi-item Instagram post (photos and/or videos) is sent as a real swipeable Telegram carousel — the `<tg-slideshow>` rich-message block (`sendRichMessage`), not a media-group album. Rich-message media accepts **only public http(s) URLs** (no file_id/multipart — `InputRichMessage` carries just an HTML/markdown string), so each slide references the original Instagram CDN URL — the same kind of URL Telegram already fetches for link previews. Ordered slides (`DownloadResult.carousel_slides`, a list of `CarouselSlide(url, is_video)`) are produced on two paths: photo-only carousels record the CDN URL of each image `_try_instagram_photo` downloads; carousels containing video are harvested from the yt-dlp playlist `info["entries"]` inside `_download_sync` (`_entry_to_slide` / `_best_entry_media_url` pick a progressive MP4 for video slides, the largest image for photo slides), with no extra network calls. The platforms eligible for the `info["entries"]` harvest are gated by `_supports_carousel` — Instagram (incl. the kkinstagram mirror) and X/Twitter. **X/Twitter photos need a separate source**: yt-dlp's `TwitterIE` builds playlist entries only from non-photo media, so a photo/mixed tweet would lose its photos. For X/Twitter the bot first calls `_try_twitter_carousel`, which pulls the complete, ordered media list (photos **and** videos) from the public **fxtwitter** API (`_fetch_twitter_media` → `api.fxtwitter.com/<path>`, `tweet.media.all`, no cookies), downloads the photo slides for the album fallback, and returns those as `carousel_slides`; video-only tweets fall through to the yt-dlp `info["entries"]` harvest (which covers all their videos and yields real local files). Twitter media URLs (`pbs.twimg.com` / `video.twimg.com`) are public, so Telegram fetches them directly. The download handler builds the `<tg-slideshow>` HTML (`_build_slideshow_html`) and sends it via `_send_rich_carousel`. If the rich send fails (Bot API server without rich support, a URL Telegram can't fetch), the handler falls back to the downloaded local media — album/single-photo for photo posts, single video otherwise — so worst case equals the previous behavior. `carousel_slides` is cached/restored alongside `file_path`/`photo_paths`. Note: for video carousels the yt-dlp path still downloads the post's media for the fallback (the post caption/title is preserved from the playlist title, captured before the playlist collapses to its first entry); inline mode still sends only the first slide.

**GIF output as silent mp4**: `/gif` does not emit a real palette-based `.gif`. Telegram renders a soundless H.264 mp4 as an autoplaying looped animation (`sendAnimation`), and that mp4 is far lighter and smoother than an equivalent GIF — sidestepping the GIF format's frames-vs-size tradeoff. `_convert_to_gif` caps the long side at `GIF_MAX_SIZE` (no upscale, even dimensions for yuv420p), the duration at `GIF_MAX_DURATION` and the frame rate at `GIF_FPS`, encoding with libx264 + CRF `GIF_CRF`. If a pass still exceeds `MAX_FILE_SIZE`, it re-encodes progressively smaller until it fits. The result is sent via `answer_animation`.

**Inline mode**: The bot supports inline queries (`@bot <url>`). Results are returned as cached file_ids when available, or as placeholder Article results with a loading keyboard. On selection (`chosen_inline_result`), the video/photo/MP3 is downloaded, uploaded to `VIDEO_STORAGE_CHAT_ID` (falls back to `ADMIN_USERS[0]`), and the inline message is edited with the real media. The reply_markup (keyboard) must be present on the placeholder for Telegram to send `inline_message_id`. BotFather inline feedback must be set to 100% for `chosen_inline_result` to fire.

**i18n / localization**: The bot supports Russian and English. Translation strings live in `src/locales/ru.json` and `src/locales/en.json` and are loaded once at import time by `src/locales/__init__.py` into the `LOCALE_MESSAGES` dict. `src/services/i18n.py` exposes `get_text(key, lang)` and the `Translator` class (a callable bound to one language). `LocaleMiddleware` injects a `Translator` instance as `t` into every handler's `data`, so handlers call `t("key")` without threading language state themselves. Users select their language via `/language`; the preference is stored in `UserPreference` and takes priority over the Telegram client's `language_code`. Supported languages are declared in `SUPPORTED_LANGUAGES` (`config.py`); the default is `DEFAULT_LANGUAGE` (env var, falls back to `"ru"`). Adding a new language requires a new JSON file and an entry in `SUPPORTED_LANGUAGES`. Command menus are registered per-language at startup via `BotCommandScopeAllPrivateChats(language_code=...)` and updated per-admin chat when they switch language.

### Module Map

```
src/
├── main.py                — Bot startup, middleware/router registration, graceful shutdown
├── config.py              — Env vars, constants (MAX_FILE_SIZE=50MB, DOWNLOAD_TIMEOUT=300s, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE)
├── bot/
│   ├── commands.py            — Localized BotCommand list builders: user_commands(t), admin_commands(t)
│   ├── handlers/
│   │   ├── __init__.py        — get_main_router(), aggregates all sub-routers in priority order
│   │   ├── common.py          — /start, /help, /id, /cache, /clearcache
│   │   ├── download.py        — URL catch-all handler (regex detection, photo/video upload)
│   │   ├── download_cmd.py    — /download command with FSM waiting state
│   │   ├── round.py           — /round: FFmpeg crop+scale to 512×512 video note (max 60s)
│   │   ├── gif.py             — /gif: FFmpeg → compact silent H.264 mp4 sent as a Telegram animation (configurable duration/size/fps, size-guard re-encode)
│   │   ├── mp3.py             — /mp3: FFmpeg libmp3lame audio extraction (VBR q=2)
│   │   ├── voice.py           — /voice: FFmpeg libopus OGG voice message (64kbps, 48kHz, mono)
│   │   ├── inline.py          — Inline-mode handler: cached results, storage-chat upload flow
│   │   ├── language.py        — /language command + set_lang callback; updates admin command scope on change
│   │   └── admin.py           — /adduser, /removeuser, /users, /stats, /userstats, /adminhelp
│   └── middlewares/
│       └── access.py          — DatabaseMiddleware, UserAccessMiddleware, LocaleMiddleware
├── locales/
│   ├── __init__.py            — Loads *.json locale files into LOCALE_MESSAGES dict at import time
│   ├── en.json                — English translation strings
│   └── ru.json                — Russian translation strings
└── services/
    ├── downloader.py          — VideoDownloader class, yt-dlp wrapper, cache, Instagram logic
    ├── database.py            — DatabaseService class, User/DownloadStats/UserPreference ORM models
    ├── i18n.py                — get_text(), Translator class, normalize_language(), translate_download_error()
    └── url_utils.py           — URL normalization, platform detection, kkinstagram fallback
```

### Database Schema

Three SQLAlchemy models defined in `src/services/database.py`:
- `User`: `user_id` (BigInteger, unique), `is_active` (soft delete), `created_at`
- `DownloadStats`: `user_id`, `platform`, `url`, `success`, `created_at`
- `UserPreference`: `user_id` (BigInteger, unique), `language` (String(8)), `created_at`, `updated_at` — stored separately from `User` so admins (who are not in the whitelist) can also save a language preference

Schema is created automatically on startup; Alembic is installed but not configured.

### Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `BOT_TOKEN` | Yes | From BotFather |
| `ADMIN_USERS` | Yes | Comma-separated Telegram user IDs |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...` |
| `DOWNLOAD_DIR` | No | Default: `downloads` |
| `DEFAULT_LANGUAGE` | No | Default UI language code (`ru` or `en`); falls back to `"ru"` if unsupported |
| `YT_COOKIES_FILE` | No | Netscape-format cookies for age-restricted YouTube |
| `INSTA_COOKIES_FILE` | No | Netscape-format cookies for Instagram (private accounts, auth errors) |
| `VIDEO_STORAGE_CHAT_ID` | No | Chat for inline-mode file uploads; fallback: `ADMIN_USERS[0]` |
| `GIF_FPS` | No | `/gif` frame rate (default `30`) |
| `GIF_MAX_DURATION` | No | `/gif` max duration in seconds (default `15`) |
| `GIF_MAX_SIZE` | No | `/gif` max long-side resolution in px (default `640`) |
| `GIF_CRF` | No | `/gif` H.264 quality; lower = better/heavier (default `28`) |
| `POSTGRES_PASSWORD` | Docker only | Default: `postgres` |
| `YT_COOKIES_FILE_HOST_PATH` | Docker only | Host path mounted into container |
| `INSTA_COOKIES_FILE_HOST_PATH` | Docker only | Host path mounted into container |

### Testing

Tests live in `tests/unit/` and use `pytest-asyncio` with an in-memory SQLite database:

```bash
uv run pytest
```

- `test_config.py` — env var loading
- `test_database.py` — user/stats CRUD operations
- `test_downloader.py` — URL hashing, cache, platform detection, Instagram HTML parsing
- `test_i18n.py` — translation key lookup, fallback, Translator class, normalize_language
- `test_middleware.py` — access control enforcement
- `test_url_utils.py` — URL extraction, normalization, platform detection

## Conventions

- HTML parse mode is set globally on the Bot instance; handlers use `<code>`, `<b>` tags directly in strings
- User-facing messages use emoji prefixes (✅ ❌ ⏳ 📤) — match existing patterns when adding handlers
- All database operations use `async with session:` context managers; never reuse sessions across calls
- Handlers receive `db: DatabaseService` and `t: Translator` from middleware data, not from direct import
- Admin commands all call `is_admin(message.from_user.id)` as first guard
- All conversion handlers (`/round`, `/gif`, `/mp3`, `/voice`) accept both a URL argument and a direct file upload (video/audio/document)
- URL utilities belong in `src/services/url_utils.py`, not inline in handlers or downloader
- All user-facing strings must use translation keys via `t("key")` — never hardcode text in handlers
- New translation keys must be added to **both** `src/locales/ru.json` and `src/locales/en.json`
