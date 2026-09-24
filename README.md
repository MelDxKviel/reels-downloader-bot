# 🎬 Reels Downloader Bot

> A Telegram bot for downloading videos from YouTube, Instagram Reels, TikTok, and X (Twitter).  
> Built with **aiogram 3.x** + **yt-dlp**, PostgreSQL statistics, and an access control system.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?logo=telegram&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-red)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-336791?logo=postgresql&logoColor=white)
[![CI](https://github.com/meldxkviel/reels-downloader-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/meldxkviel/reels-downloader-bot/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/meldxkviel/reels-downloader-bot/branch/main/graph/badge.svg)](https://codecov.io/gh/meldxkviel/reels-downloader-bot)

[🇷🇺 Русская версия](./README_RU.md)

---

## 📋 Table of Contents

- [Features](#-features)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [Local Setup](#-local-setup-via-uv)
- [Docker Compose](#-docker-compose)
- [Bot Commands](#-bot-commands)
- [Cookies (YouTube & Instagram)](#-cookies-youtube--instagram)

---

## ✨ Features

| Feature | Description |
|---|---|
| 📥 **Multi-platform** | YouTube, Instagram Reels, TikTok, X (Twitter) |
| ⚡ **Download cache** | Repeated links are served instantly from local cache |
| 🔐 **Access control** | Whitelist system: only added users can use the bot |
| 🪄 **Inline mode** | Send videos from any chat: `@bot_name <url>` |
| 🎥 **Video notes** | `/round` command — converts video to Telegram video note format |
| 📊 **Statistics** | Per-platform and per-user download stats stored in PostgreSQL |
| 🌐 **Localization** | Interface in Russian and English; each user picks their language via `/language` |
| 🐳 **Docker Compose** | Bot + PostgreSQL launched with a single command |
| 🍪 **Cookies** | YouTube (age-restricted) and Instagram (private accounts) — via Netscape cookies |

---

## 🚀 Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/meldxkviel/reels-downloader-bot.git
cd reels-downloader-bot

# 2. Create .env file
cp .env.example .env  # or create manually (see Configuration section)

# 3. Launch via Docker Compose
docker compose up -d

# 4. Check logs
docker compose logs -f bot
```

---

## ⚙️ Configuration

Create a `.env` file in the project root:

```env
# Required
BOT_TOKEN=your_bot_token_from_botfather
ADMIN_USERS=123456789,987654321   # Comma-separated Telegram user IDs

# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/downloader_bot
POSTGRES_PASSWORD=postgres        # Docker Compose only

# Optional
DEFAULT_LANGUAGE=ru                      # Interface language: ru or en (default: ru)
DOWNLOAD_DIR=downloads                   # Directory for downloaded files
YT_COOKIES_FILE=./cookies.txt            # Cookies for age-restricted YouTube
INSTA_COOKIES_FILE=./instagram-cookies.txt  # Cookies for Instagram (private accounts)
VIDEO_STORAGE_CHAT_ID=-1001234567890     # Chat for inline pre-upload (fallback: first ADMIN_USERS)
DOWNLOAD_TIMEOUT=300                    # Download timeout, seconds
MAX_CONCURRENT_JOBS=3                   # Active downloads, conversions and searches
MAX_QUEUED_JOBS=12                      # Additional jobs allowed to wait
MAX_USER_JOBS=2                         # Active + queued jobs per user

# Docker Compose only
YT_COOKIES_FILE_HOST_PATH=./cookies.txt               # Host path to YouTube cookies
INSTA_COOKIES_FILE_HOST_PATH=./instagram-cookies.txt  # Host path to Instagram cookies
```

### Environment Variables

| Variable | Required | Description |
|---|:---:|---|
| `BOT_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_USERS` | ✅ | Telegram user IDs of admins (comma-separated) |
| `DATABASE_URL` | ✅ | PostgreSQL connection string (async SQLAlchemy) |
| `POSTGRES_PASSWORD` | Docker | PostgreSQL password for Docker Compose |
| `DEFAULT_LANGUAGE` | ❌ | Default UI language (`ru` or `en`, default: `ru`) |
| `DOWNLOAD_DIR` | ❌ | File storage directory (default: `downloads`) |
| `YT_COOKIES_FILE` | ❌ | Path to Netscape cookies file for YouTube |
| `INSTA_COOKIES_FILE` | ❌ | Path to Netscape cookies file for Instagram |
| `VIDEO_STORAGE_CHAT_ID` | ❌ | Chat for temporary video upload in inline mode to obtain `file_id`. If not set, falls back to the first admin in `ADMIN_USERS` |
| `DOWNLOAD_TIMEOUT` | ❌ | Download timeout in seconds (default: `300`) |
| `MAX_CONCURRENT_JOBS` | ❌ | Maximum simultaneous downloads, conversions and searches (default: `3`) |
| `MAX_QUEUED_JOBS` | ❌ | Maximum waiting jobs beyond the active workers (default: `12`) |
| `MAX_USER_JOBS` | ❌ | Maximum active and queued jobs per user (default: `2`) |
| `GIF_FPS` | ❌ | Animation frame rate (default: `30`) |
| `GIF_MAX_DURATION` | ❌ | Maximum animation duration in seconds (default: `15`) |
| `GIF_MAX_SIZE` | ❌ | Maximum long-side animation resolution in pixels (default: `640`) |
| `GIF_CRF` | ❌ | H.264 quality; lower means better quality and larger files (default: `28`) |
| `CACHE_AUTOCLEAN_DEFAULT` | ❌ | Enable automatic cache cleanup until overridden in `/cache` (default: `false`) |
| `CACHE_MAX_AGE_HOURS` | ❌ | Cache retention until overridden in `/cache` (default: `168`, seven days) |
| `CACHE_CLEANUP_INTERVAL` | ❌ | Cleanup check interval in seconds (default: `3600`, minimum: `60`) |
| `YT_COOKIES_FILE_HOST_PATH` | Docker | Host path to YouTube cookies (mounted into container) |
| `INSTA_COOKIES_FILE_HOST_PATH` | Docker | Host path to Instagram cookies (mounted into container) |

> ⚠️ If `ADMIN_USERS` is empty, all admin commands will be unavailable.  
> ⚠️ Regular users must be added by an admin via `/adduser`.

---

## 💻 Local Setup (via uv)

```bash
# 1. Install uv (package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
uv sync

# 3. Start the bot (schema is created automatically on first run)
uv run python -m src.main
```

> 💡 Some video formats require **FFmpeg**. Without it, some downloads may fail (especially when audio and video streams need to be merged).  
> Install: `sudo apt install ffmpeg` (Linux) or `brew install ffmpeg` (macOS).

---

## 🐳 Docker Compose

```bash
# Start the bot and database
docker compose up -d

# Stream logs
docker compose logs -f bot

# Stop
docker compose down
```

**Under the hood:**
- `bot` — bot container (image from GitHub Container Registry)
- `db` — PostgreSQL 17 Alpine with health check
- Database data is stored in the `postgres_data` volume
- Downloaded files are mounted at `./downloads/`

Both Compose files pass `DEFAULT_LANGUAGE`, `VIDEO_STORAGE_CHAT_ID`, `GIF_*`, `CACHE_*`,
`DOWNLOAD_TIMEOUT` and the three `MAX_*_JOBS` limits from `.env` into the bot. After editing
these values, run `docker compose up -d bot` (add `-f docker-compose.local.yml` for a local
build). Cache preferences already saved through `/cache` take precedence over the initial
cleanup defaults.

---

## 📱 Bot Commands

### User Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and brief instructions |
| `/help` | Command reference |
| `/id` | Show your Telegram user ID |
| `/download [url]` | Download video by URL (immediate or FSM waiting state) |
| `/mp3 [url]` | Download audio and send as an MP3 file |
| `/voice [url]` | Download audio and send as a Telegram voice message |
| `/gif [url]` | Download video and convert to a GIF |
| `/round [url]` | Download video and send as a video note (circle) |
| `/language` | Choose interface language (🇷🇺 Russian / 🇬🇧 English) |
| `/cache` | Show local cache info |
| `/clearcache` | Clear the local cache |

> 💡 You can also just send a URL in the chat — the bot will download it automatically.

### Inline Mode

In any chat (even where the bot isn't a member), type:

```
@bot_name https://www.youtube.com/shorts/XXXXXXXXXXX
```

Telegram will show a result card — select it and the media will be sent to the current chat under your name. Instagram photo carousels are delivered as native swipeable rich-message slideshows, including inline mode. Cached videos are delivered instantly. New URLs first show a "⏳ Loading…" placeholder that is replaced once downloaded.

> ⚙️ For the deferred scenario to work:
> - Enable inline mode in BotFather (`/setinline`)
> - Enable inline feedback (`/setinlinefeedback → 100%`)
> - Set `VIDEO_STORAGE_CHAT_ID` (any chat/channel where the bot can send and delete messages). Telegram doesn't allow uploading new files directly during an inline edit, so the bot first publishes media to the storage chat, retrieves reusable `file_id` values, inserts them into the inline video/photo or rich carousel, and deletes the intermediate messages. If `VIDEO_STORAGE_CHAT_ID` is not set, the fallback is the first admin's DM (`ADMIN_USERS[0]`).
>
> The whitelist also applies to inline queries: users not on the list will receive an empty response.

### Admin Commands

> Available only to users in `ADMIN_USERS`. Full reference: `/adminhelp`.

| Command | Description |
|---|---|
| `/adduser USER_ID` | Add a user (grant access) |
| `/removeuser USER_ID` | Remove a user (revoke access) |
| `/users` | List all allowed users |
| `/stats` | Overall bot statistics by platform |
| `/userstats USER_ID` | Statistics for a specific user |
| `/adminhelp` | Admin command reference |

---

## 🍪 Cookies (YouTube & Instagram)

Cookies are needed for:
- **YouTube** — age-restricted videos, private videos, members-only content
- **Instagram** — private accounts, bypassing `login_required` errors

For a detailed step-by-step guide (exporting from Chrome/Firefox, Docker setup, troubleshooting):

**[→ COOKIES_GUIDE.md](./COOKIES_GUIDE.md)**

Production Instagram cookies can be refreshed without direct SSH access:

```bash
uv run python scripts/publish_instagram_cookies.py --browser firefox
```

**Quick setup:**

```bash
# copy exported files
cp ~/Downloads/youtube.com_cookies.txt cookies.txt
cp ~/Downloads/instagram.com_cookies.txt instagram-cookies.txt
```

```env
# .env (local run)
YT_COOKIES_FILE=./cookies.txt
INSTA_COOKIES_FILE=./instagram-cookies.txt
```

```yaml
# docker-compose.yml
services:
  bot:
    environment:
      YT_COOKIES_FILE: /app/cookies.txt
      INSTA_COOKIES_FILE: /app/instagram-cookies.txt
    volumes:
      - ${YT_COOKIES_FILE_HOST_PATH:-./cookies.txt}:/app/cookies.txt:ro
      - ${INSTA_COOKIES_FILE_HOST_PATH:-./instagram-cookies.txt}:/app/instagram-cookies.txt:ro
```

> ⚠️ Cookies are tied to a browser session and may expire — re-export them if you encounter auth errors.

