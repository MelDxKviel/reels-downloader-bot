# 🎬 Reels Downloader Bot

> Telegram-бот для скачивания видео с YouTube, Instagram Reels, TikTok и X (Twitter).  
> Построен на **aiogram 3.x** + **yt-dlp**, с PostgreSQL-статистикой и системой управления доступом.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?logo=telegram&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-red)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-336791?logo=postgresql&logoColor=white)
[![CI](https://github.com/meldxkviel/reels-downloader-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/meldxkviel/reels-downloader-bot/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/meldxkviel/reels-downloader-bot/branch/main/graph/badge.svg)](https://codecov.io/gh/meldxkviel/reels-downloader-bot)

---

## 📋 Содержание

- [Возможности](#-возможности)
- [Быстрый старт](#-быстрый-старт)
- [Конфигурация](#-конфигурация)
- [Запуск локально](#-запуск-локально-через-uv)
- [Запуск через Docker](#-запуск-через-docker-compose)
- [Команды бота](#-команды-бота)
- [Cookies (YouTube и Instagram)](#-cookies-youtube-и-instagram)

---

## ✨ Возможности

| Функция | Описание |
|---|---|
| 📥 **Мультиплатформенность** | YouTube, Instagram Reels, TikTok, X (Twitter) |
| ⚡ **Кэш скачиваний** | Повторные ссылки отдаются мгновенно из локального кэша |
| 🔐 **Контроль доступа** | Whitelist-система: только добавленные пользователи могут использовать бота |
| 🪄 **Inline-режим** | Отправка видео прямо из любого чата: `@имя_бота <ссылка>` |
| 🎥 **Видео-кружки** | Команда `/round` — конвертация видео в формат Telegram video note |
| 📊 **Статистика** | Сбор статистики по платформам и пользователям в PostgreSQL |
| 🌐 **Локализация** | Интерфейс на русском и английском; язык выбирается через `/language` |
| 🐳 **Docker Compose** | Бот + PostgreSQL поднимаются одной командой |
| 🍪 **Cookies** | YouTube (видео 18+) и Instagram (закрытые аккаунты) — через Netscape cookies |

---

## 🚀 Быстрый старт

```bash
# 1. Клонируйте репозиторий
git clone https://github.com/meldxkviel/reels-downloader-bot.git
cd reels-downloader-bot

# 2. Создайте .env файл
cp .env.example .env  # или создайте вручную (см. раздел Конфигурация)

# 3. Запустите через Docker Compose
docker compose up -d

# 4. Проверьте логи
docker compose logs -f bot
```

---

## ⚙️ Конфигурация

Создайте файл `.env` в корне проекта:

```env
# Обязательные
BOT_TOKEN=your_bot_token_from_botfather
ADMIN_USERS=123456789,987654321   # Telegram user_id через запятую

# База данных
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/downloader_bot
POSTGRES_PASSWORD=postgres        # Только для Docker Compose

# Опциональные
DEFAULT_LANGUAGE=ru                      # Язык интерфейса: ru или en (по умолчанию: ru)
DOWNLOAD_DIR=downloads                   # Директория для загруженных файлов
YT_COOKIES_FILE=./cookies/youtube.txt    # Cookies для YouTube 18+
INSTA_COOKIES_FILE=./cookies/instagram.txt  # Cookies для Instagram (закрытые аккаунты)
VIDEO_STORAGE_CHAT_ID=-1001234567890     # Чат для inline-pre-upload видео (fallback: первый ADMIN_USERS)
DOWNLOAD_TIMEOUT=300                    # Таймаут загрузки, секунды
MAX_CONCURRENT_JOBS=3                   # Одновременные загрузки, конвертации и поиск
MAX_QUEUED_JOBS=12                      # Дополнительные задачи в очереди
MAX_USER_JOBS=2                         # Выполняемые + ожидающие задачи одного пользователя

# Только для Docker Compose
YT_COOKIES_FILE_HOST_PATH=./cookies/youtube.txt     # Путь к YouTube cookies на хосте
INSTA_COOKIES_FILE_HOST_PATH=./cookies/instagram.txt  # Путь к Instagram cookies на хосте
```

### Описание переменных

| Переменная | Обязательна | Описание |
|---|:---:|---|
| `BOT_TOKEN` | ✅ | Токен бота из [@BotFather](https://t.me/BotFather) |
| `ADMIN_USERS` | ✅ | Telegram user_id администраторов (через запятую) |
| `DATABASE_URL` | ✅ | Строка подключения к PostgreSQL (async SQLAlchemy) |
| `POSTGRES_PASSWORD` | Docker | Пароль PostgreSQL для Docker Compose |
| `DEFAULT_LANGUAGE` | ❌ | Язык интерфейса по умолчанию (`ru` или `en`, по умолчанию `ru`) |
| `DOWNLOAD_DIR` | ❌ | Директория для файлов (по умолчанию `downloads`) |
| `YT_COOKIES_FILE` | ❌ | Путь к Netscape cookies-файлу для YouTube |
| `INSTA_COOKIES_FILE` | ❌ | Путь к Netscape cookies-файлу для Instagram |
| `VIDEO_STORAGE_CHAT_ID` | ❌ | Чат для промежуточной выгрузки видео в inline-режиме ради получения `file_id`. Если не задан — используется первый ID из `ADMIN_USERS` |
| `DOWNLOAD_TIMEOUT` | ❌ | Таймаут загрузки в секундах (по умолчанию `300`) |
| `MAX_CONCURRENT_JOBS` | ❌ | Максимум одновременных загрузок, конвертаций и поисковых запросов (по умолчанию `3`) |
| `MAX_QUEUED_JOBS` | ❌ | Максимум ожидающих задач сверх выполняемых (по умолчанию `12`) |
| `MAX_USER_JOBS` | ❌ | Максимум выполняемых и ожидающих задач одного пользователя (по умолчанию `2`) |
| `GIF_FPS` | ❌ | Частота кадров анимации (по умолчанию `30`) |
| `GIF_MAX_DURATION` | ❌ | Максимальная длительность анимации в секундах (по умолчанию `15`) |
| `GIF_MAX_SIZE` | ❌ | Максимальная длинная сторона анимации в пикселях (по умолчанию `640`) |
| `GIF_CRF` | ❌ | Качество H.264: ниже — качественнее и больше файл (по умолчанию `28`) |
| `CACHE_AUTOCLEAN_DEFAULT` | ❌ | Автоочистка кэша до изменения настройки через `/cache` (по умолчанию `false`) |
| `CACHE_MAX_AGE_HOURS` | ❌ | Срок хранения кэша до изменения через `/cache`, часы (по умолчанию `168`, семь дней) |
| `CACHE_CLEANUP_INTERVAL` | ❌ | Интервал проверки кэша в секундах (по умолчанию `3600`, минимум `60`) |
| `YT_COOKIES_FILE_HOST_PATH` | Docker | Путь к YouTube cookies на хосте (монтируется в контейнер) |
| `INSTA_COOKIES_FILE_HOST_PATH` | Docker | Путь к Instagram cookies на хосте (монтируется в контейнер) |

> ⚠️ Если `ADMIN_USERS` пустой — все команды администратора будут недоступны.  
> ⚠️ Обычные пользователи должны быть добавлены администратором через `/adduser`.

---

## 💻 Запуск локально (через uv)

```bash
# 1. Установите uv (менеджер пакетов)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Установите зависимости
uv sync

# 3. Запустите бота (схема БД создаётся автоматически при первом запуске)
uv run python -m src.main
```

> 💡 Для работы с некоторыми форматами требуется **FFmpeg**. Без него часть видео может не скачиваться (особенно когда нужна склейка аудио + видео).  
> Установка: `sudo apt install ffmpeg` (Linux) или `brew install ffmpeg` (macOS).

---

## 🐳 Запуск через Docker Compose

```bash
# Запуск бота и базы данных
docker compose up -d

# Просмотр логов в реальном времени
docker compose logs -f bot

# Остановка
docker compose down
```

**Что происходит под капотом:**
- `bot` — контейнер с ботом (образ из GitHub Container Registry)
- `db` — PostgreSQL 17 Alpine с health check
- Данные БД сохраняются в volume `postgres_data`
- Скачанные файлы монтируются в `./downloads/`

Оба Compose-файла передают в контейнер значения `DEFAULT_LANGUAGE`, `VIDEO_STORAGE_CHAT_ID`,
`GIF_*`, `CACHE_*`, `DOWNLOAD_TIMEOUT` и трёх лимитов `MAX_*_JOBS` из `.env`. После изменения
выполни `docker compose up -d bot` (для локальной сборки добавь `-f docker-compose.local.yml`).
Параметры автоочистки, уже сохранённые через `/cache`, имеют приоритет над начальными
значениями из окружения.

---

## 📱 Команды бота

### Пользовательские команды

| Команда | Описание |
|---|---|
| `/start` | Приветствие и краткая инструкция |
| `/help` | Справка по командам |
| `/id` | Показать ваш Telegram user_id |
| `/download [url]` | Скачать видео по ссылке (с FSM-ожиданием или сразу) |
| `/mp3 [url]` | Скачать аудио и отправить как MP3-файл |
| `/voice [url]` | Скачать аудио и отправить как голосовое сообщение |
| `/gif [url]` | Скачать видео и конвертировать в GIF |
| `/round [url]` | Скачать видео и отправить как кружок (video note) |
| `/language` | Выбрать язык интерфейса (🇷🇺 Русский / 🇬🇧 English) |
| `/cache` | Информация о локальном кэше |
| `/clearcache` | Очистить локальный кэш |

> 💡 Также можно просто отправить ссылку в чат — бот скачает её автоматически.

### Inline-режим

В любом чате (в том числе где бота нет) наберите:

```
@имя_бота https://www.youtube.com/shorts/XXXXXXXXXXX
```

Telegram покажет карточку — выберите её, и медиа отправится прямо в текущий
чат от имени вызвавшего пользователя. Фотокарусели Instagram отправляются
нативным свайпаемым rich-сообщением, в том числе в inline-режиме. Видео,
которое уже есть в кэше, уходит моментально. Новые ссылки сначала показываются
плашкой «⏳ Загружаю…», которая подменяется готовым медиа после скачивания.

> ⚙️ Для работы второго сценария нужно:
> - включить inline-режим у бота в BotFather (`/setinline`),
> - включить inline feedback (`/setinlinefeedback → 100%`),
> - задать `VIDEO_STORAGE_CHAT_ID` (любой чат/канал, где бот имеет право
>   отправлять и удалять сообщения). Telegram не позволяет загружать новые
>   файлы прямо во время inline-редактирования — бот сперва публикует медиа в
>   storage-чат, получает `file_id` каждого файла, подставляет их в обычное
>   inline-медиа или rich-карусель и удаляет промежуточные сообщения. Если
>   `VIDEO_STORAGE_CHAT_ID` не задан, fallback — личка первого администратора
>   (`ADMIN_USERS[0]`).
>
> Whitelist действует и для inline-запросов: пользователи не из списка получат
> пустой ответ.

### Команды администратора

> Доступны только пользователям из `ADMIN_USERS`. Полная справка: `/adminhelp`.

| Команда | Описание |
|---|---|
| `/adduser USER_ID` | Добавить пользователя (разрешить доступ) |
| `/removeuser USER_ID` | Удалить пользователя (запретить доступ) |
| `/users` | Список всех разрешённых пользователей |
| `/stats` | Общая статистика бота по платформам |
| `/userstats USER_ID` | Статистика по конкретному пользователю |
| `/adminhelp` | Справка по всем админ-командам |

---

## 🍪 Cookies (YouTube и Instagram)

Cookies нужны для:
- **YouTube** — видео 18+, приватные видео, материалы для участников канала
- **Instagram** — закрытые аккаунты, обход ошибок `login_required`

Подробный пошаговый гайд (экспорт из Chrome/Firefox, Docker, устранение проблем):

**[→ COOKIES_GUIDE.md](./COOKIES_GUIDE.md)**

**Быстрая настройка:**

```bash
mkdir -p cookies
# скопируйте экспортированные файлы
cp ~/Downloads/youtube.com_cookies.txt cookies/youtube.txt
cp ~/Downloads/instagram.com_cookies.txt cookies/instagram.txt
echo "cookies/" >> .gitignore
```

```env
# .env (локальный запуск)
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

> ⚠️ Cookies привязаны к сеансу браузера и могут истекать — при ошибках авторизации повторите экспорт.

