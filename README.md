# 🎬 Reels Downloader Bot

> Telegram-бот для скачивания видео с YouTube, Instagram Reels, TikTok и X (Twitter).  
> Построен на **aiogram 3.x** + **yt-dlp**, с PostgreSQL-статистикой и системой управления доступом.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?logo=telegram&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-red)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-336791?logo=postgresql&logoColor=white)

---

## 📋 Содержание

- [Возможности](#-возможности)
- [Быстрый старт](#-быстрый-старт)
- [Конфигурация](#-конфигурация)
- [Запуск локально](#-запуск-локально-через-uv)
- [Запуск через Docker](#-запуск-через-docker-compose)
- [Команды бота](#-команды-бота)
- [Cookies для YouTube 18+](#-cookies-для-youtube-18)
- [Архитектура](#-архитектура)

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
| 🐳 **Docker Compose** | Бот + PostgreSQL поднимаются одной командой |
| 🍪 **YouTube Cookies** | Поддержка скачивания видео с возрастными ограничениями |

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
DOWNLOAD_DIR=downloads            # Директория для загруженных файлов
YT_COOKIES_FILE=./cookies.txt     # Cookies для YouTube 18+
VIDEO_STORAGE_CHAT_ID=-1001234567890  # Чат для inline-pre-upload видео (fallback: первый ADMIN_USERS)

# Только для Docker Compose
YT_COOKIES_FILE_HOST_PATH=./cookies.txt  # Путь к cookies на хосте
```

### Описание переменных

| Переменная | Обязательна | Описание |
|---|:---:|---|
| `BOT_TOKEN` | ✅ | Токен бота из [@BotFather](https://t.me/BotFather) |
| `ADMIN_USERS` | ✅ | Telegram user_id администраторов (через запятую) |
| `DATABASE_URL` | ✅ | Строка подключения к PostgreSQL (async SQLAlchemy) |
| `POSTGRES_PASSWORD` | Docker | Пароль PostgreSQL для Docker Compose |
| `DOWNLOAD_DIR` | ❌ | Директория для файлов (по умолчанию `downloads`) |
| `YT_COOKIES_FILE` | ❌ | Путь к cookies-файлу для YouTube |
| `VIDEO_STORAGE_CHAT_ID` | ❌ | Чат для промежуточной выгрузки видео в inline-режиме ради получения `file_id`. Если не задан — используется первый ID из `ADMIN_USERS` |
| `YT_COOKIES_FILE_HOST_PATH` | Docker | Путь к cookies на хосте (монтируется в контейнер) |

> ⚠️ Если `ADMIN_USERS` пустой — все команды администратора будут недоступны.  
> ⚠️ Обычные пользователи должны быть добавлены администратором через `/adduser`.

---

## 💻 Запуск локально (через uv)

```bash
# 1. Установите uv (менеджер пакетов)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Установите зависимости
uv sync

# 3. Примените миграции базы данных
uv run alembic upgrade head

# 4. Запустите бота
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

---

## 📱 Команды бота

### Пользовательские команды

| Команда | Описание |
|---|---|
| `/start` | Приветствие и краткая инструкция |
| `/help` | Справка по командам |
| `/id` | Показать ваш Telegram user_id |
| `/download [url]` | Скачать видео по ссылке (с FSM-ожиданием или сразу) |
| `/round [url]` | Скачать видео и отправить как кружок (video note) |
| `/cache` | Информация о локальном кэше |
| `/clearcache` | Очистить локальный кэш |

> 💡 Также можно просто отправить ссылку в чат — бот скачает её автоматически.

### Inline-режим

В любом чате (в том числе где бота нет) наберите:

```
@имя_бота https://www.youtube.com/shorts/XXXXXXXXXXX
```

Telegram покажет карточку — выберите её, и ролик отправится прямо в текущий
чат от имени вызвавшего пользователя. Видео, которое уже есть в кэше
(ранее скачивалось этим ботом), уходит моментально. Новые ссылки сначала
показываются плашкой «⏳ Загружаю…», которая подменяется на видео после
скачивания.

> ⚙️ Для работы второго сценария нужно:
> - включить inline-режим у бота в BotFather (`/setinline`),
> - включить inline feedback (`/setinlinefeedback → 100%`),
> - задать `VIDEO_STORAGE_CHAT_ID` (любой чат/канал, где бот имеет право
>   отправлять и удалять сообщения). Telegram не позволяет загружать новые
>   файлы напрямую в inline-сообщения — бот сперва публикует видео в storage-чат,
>   получает оттуда `file_id`, подставляет его в inline-карточку и удаляет
>   промежуточное сообщение. Если `VIDEO_STORAGE_CHAT_ID` не задан, fallback —
>   личка первого администратора (`ADMIN_USERS[0]`).
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

## 🍪 Cookies для YouTube 18+

Для скачивания видео с возрастными ограничениями нужны cookies авторизованного аккаунта YouTube.

**Шаги:**

1. Установите расширение браузера [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome/Edge).
2. Войдите в YouTube-аккаунт.
3. Откройте любую страницу YouTube и экспортируйте cookies в файл `cookies.txt`.
4. Пропишите путь в `.env`:

**Локальный запуск:**
```env
YT_COOKIES_FILE=./cookies.txt
```

**Docker Compose:**
```env
YT_COOKIES_FILE=/app/cookies.txt
YT_COOKIES_FILE_HOST_PATH=./cookies.txt   # путь на хосте
```

**Примеры для Windows:**
```env
# Локально
YT_COOKIES_FILE=C:\Users\me\Downloads\cookies.txt

# Docker Compose
YT_COOKIES_FILE_HOST_PATH=C:\Users\me\Downloads\cookies.txt
```

> ⚠️ Cookies могут истекать. При ошибках авторизации — повторите экспорт.

---

## 🏗️ Архитектура

```
Telegram Update
      │
      ▼
DatabaseMiddleware          ← инжектит DatabaseService в хендлеры
      │
      ▼
UserAccessMiddleware        ← проверяет whitelist, дропает неразрешённых
      │
      ▼
Router (приоритет):
  admin_router              ← /adduser, /removeuser, /stats ...
  common_router             ← /start, /help, /id, /cache ...
  download_cmd_router       ← /download (FSM)
  round_router              ← /round (FSM + FFmpeg)
  download_router           ← авто-детект URL в сообщениях
  inline_router             ← inline_query + chosen_inline_result
      │
      ▼
VideoDownloader.download()  ← проверяет кэш → yt-dlp в thread executor
      │
      ▼
Telegram (отправка файла)   ← DatabaseService.record_download()
```

**Стек технологий:**

| Компонент | Технология |
|---|---|
| Bot framework | [aiogram 3.x](https://docs.aiogram.dev/) (async) |
| Загрузчик видео | [yt-dlp](https://github.com/yt-dlp/yt-dlp) (sync, через executor) |
| База данных | PostgreSQL 17 + SQLAlchemy async ORM |
| Миграции | Alembic |
| Контейнеризация | Docker Compose |
| Управление зависимостями | [uv](https://docs.astral.sh/uv/) |
| Линтер | [ruff](https://docs.astral.sh/ruff/) |
