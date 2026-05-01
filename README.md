# 🎬 Reels Downloader Bot

> A Telegram bot for downloading videos from YouTube, Instagram Reels, TikTok, and X (Twitter).  
> Built with **aiogram 3.x** + **yt-dlp**, PostgreSQL statistics, and an access control system.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?logo=telegram&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-red)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-336791?logo=postgresql&logoColor=white)

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
YT_COOKIES_FILE=./cookies/youtube.txt    # Cookies for age-restricted YouTube
INSTA_COOKIES_FILE=./cookies/instagram.txt  # Cookies for Instagram (private accounts)
VIDEO_STORAGE_CHAT_ID=-1001234567890     # Chat for inline pre-upload (fallback: first ADMIN_USERS)

# Docker Compose only
YT_COOKIES_FILE_HOST_PATH=./cookies/youtube.txt     # Host path to YouTube cookies
INSTA_COOKIES_FILE_HOST_PATH=./cookies/instagram.txt  # Host path to Instagram cookies
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

Telegram will show a result card — select it and the video will be sent to the current chat under your name. Videos already in the cache are delivered instantly. New URLs first show a "⏳ Loading…" placeholder that is replaced with the video once downloaded.

> ⚙️ For the deferred scenario to work:
> - Enable inline mode in BotFather (`/setinline`)
> - Enable inline feedback (`/setinlinefeedback → 100%`)
> - Set `VIDEO_STORAGE_CHAT_ID` (any chat/channel where the bot can send and delete messages). Telegram doesn't allow uploading new files directly into inline messages — the bot first publishes the video to the storage chat, retrieves the `file_id`, inserts it into the inline card, and deletes the intermediate message. If `VIDEO_STORAGE_CHAT_ID` is not set, the fallback is the first admin's DM (`ADMIN_USERS[0]`).
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

**Quick setup:**

```bash
mkdir -p cookies
# copy exported files
cp ~/Downloads/youtube.com_cookies.txt cookies/youtube.txt
cp ~/Downloads/instagram.com_cookies.txt cookies/instagram.txt
echo "cookies/" >> .gitignore
```

```env
# .env (local run)
YT_COOKIES_FILE=./cookies/youtube.txt
INSTA_COOKIES_FILE=./cookies/instagram.txt
```

```yaml
# docker-compose.yml
services:
  bot:
    environment:
      YT_COOKIES_FILE: /app/cookies/youtube.txt
      INSTA_COOKIES_FILE: /app/cookies/instagram.txt
    volumes:
      - ./cookies:/app/cookies:ro
```

> ⚠️ Cookies are tied to a browser session and may expire — re-export them if you encounter auth errors.

