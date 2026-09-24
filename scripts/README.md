# Синхронизация куки → сервер (`sync_cookies.py`)

Скрипт автоматически:

1. вытаскивает куки **YouTube** и **Instagram** из твоего браузера (`yt-dlp --cookies-from-browser`);
2. фильтрует их по доменам (на сервер уезжают только нужные куки, а не весь браузерный сеанс);
3. заливает по SSH на сервер с ботом, перезаписывая файл **на месте** — бот читает куки на каждое скачивание, так что новые подхватываются на лету;
4. перезапускает сервис бота, если что-то изменилось.

Вся конфигурация — через переменные окружения. Запускается с твоей машины (Windows/macOS/Linux), сервер — Linux с Docker Compose.

## Требования

- **Python 3.11+** и **yt-dlp** на твоей машине. Проще всего из корня проекта: `uv sync` (yt-dlp уже в зависимостях) и запускать через `uv run`.
- Доступ к серверу по SSH (те же координаты, что у `DEPLOY_*` в [`.github/workflows/cd.yml`](../.github/workflows/cd.yml)):
  - **по ключу** (по умолчанию) — нужен **OpenSSH client** (`ssh`) в `PATH`. На Windows 11: *Settings → Apps → Optional Features → OpenSSH Client*.
  - **по паролю** — нужен пакет **paramiko** (см. [SSH по паролю](#ssh-по-паролю)).
- Ты залогинен в YouTube/Instagram в выбранном браузере.

## Настройка

```bash
cp scripts/sync-cookies.env.example .env.sync
# заполни DEPLOY_HOST / DEPLOY_USER / DEPLOY_PATH и при необходимости остальное
```

Полный список переменных — в [`sync-cookies.env.example`](sync-cookies.env.example).

`DEPLOY_PATH` должен быть абсолютным Linux-путём к каталогу проекта. Пути файлов куки
допускаются относительные либо абсолютные **внутри этого каталога**; `..`, симлинки,
специальные файлы и непустые каталоги отклоняются. Это защищает файлы сервера от
ошибочного значения `YT_COOKIES_FILE_HOST_PATH` или `INSTA_COOKIES_FILE_HOST_PATH`.

## Запуск

```bash
# сухой прогон — собрать куки и показать, что уедет (в ./cookie-sync-preview/), ничего не заливая
uv run python scripts/sync_cookies.py --dry-run

# боевой прогон
uv run python scripts/sync_cookies.py

# только одна платформа
uv run python scripts/sync_cookies.py --platforms youtube

# другой env-файл / форс-рестарт
uv run python scripts/sync_cookies.py --env-file prod.env --restart always
```

`--help` покажет все флаги. Без `uv` — просто `python scripts/sync_cookies.py`.

## SSH по паролю

Если на сервер заходишь по паролю, а не по ключу:

1. Поставь `paramiko` (один раз) — любой вариант:
   ```bash
   uv add paramiko                                       # добавить в проект
   uv run --with paramiko python scripts/sync_cookies.py # или разово на запуск
   pip install paramiko                                  # или в системный python
   ```
2. Задай пароль в `.env.sync`:
   ```env
   DEPLOY_PASSWORD=твой_пароль
   ```

Как только `DEPLOY_PASSWORD` задан, скрипт переключается на paramiko: одно соединение
на весь прогон (пароль используется один раз), системный `ssh` уже не нужен. Если ключ
зашифрован — задай `DEPLOY_KEY_PASSPHRASE`.

> Надёжнее один раз настроить вход по ключу (`ssh-copy-id user@host`): пароль не хранится
> в файле и paramiko не нужен. Но парольный режим тоже рабочий — `.env.sync` уже в
> `.gitignore` и в коммит не попадёт.

## Первое включение Instagram-куки на сервере

Раньше `docker-compose.yml` монтировал только YouTube-куки. Теперь добавлен и Instagram
(`/app/instagram-cookies.txt`). Чтобы включить его на сервере **один раз**:

1. Обнови репозиторий на сервере (этот коммит) или скопируй новый `docker-compose.yml`.
2. В серверном `.env` включи переменную (как уже сделано для YouTube):
   ```env
   INSTA_COOKIES_FILE=/app/instagram-cookies.txt
   ```
3. Прогони скрипт — он создаст файл `instagram-cookies.txt` на сервере и зальёт куки:
   ```bash
   uv run python scripts/sync_cookies.py
   ```
   > Если запускаешь `docker compose up -d` **до** первого синка и файла ещё нет, Docker
   > создаст на его месте пустую папку. Скрипт заменяет только пустую папку файлом;
   > непустую папку он оставляет нетронутой и останавливается с ошибкой,
   > но чтобы избежать лишнего пересоздания контейнера — запусти синк до первого `up -d`,
   > либо заранее `touch cookies.txt instagram-cookies.txt` в `DEPLOY_PATH`.

YouTube-куки включается так же: `YT_COOKIES_FILE=/app/cookies.txt` в серверном `.env`.

## Автоматизация (куки протухают)

Сессии живут недолго — гоняй скрипт периодически.

**Windows (Task Scheduler):**
```powershell
$action  = New-ScheduledTaskAction -Execute "python" `
  -Argument "scripts\sync_cookies.py" -WorkingDirectory "C:\Users\Igor\Documents\projects\reels-downloader-bot"
$trigger = New-ScheduledTaskTrigger -Daily -At 9am
Register-ScheduledTask -TaskName "sync-bot-cookies" -Action $action -Trigger $trigger
```

**Linux/macOS (cron, ежедневно в 9:00):**
```cron
0 9 * * * cd /path/to/reels-downloader-bot && uv run python scripts/sync_cookies.py >> sync-cookies.log 2>&1
```

## Как это устроено / нюансы

- **Один дамп на все платформы.** Браузер хранит куки всех сайтов вместе, поэтому `yt-dlp`
  вызывается один раз, а дальше jar фильтруется по доменам каждой платформы.
- **Перезапись на месте.** Контент уходит на сервер через stdin (без проблем с длиной
  командной строки на Windows) и пишется поверх существующего файла (тот же inode) —
  смонтированный в контейнер файл обновляется без рестарта.
  Перед записью весь новый файл принимается во временный файл с правами `600`;
  при ошибке записи скрипт пытается восстановить прежнее содержимое в том же inode.
- **`RESTART_BOT=auto`** перезапускает бота (`docker compose up -d <service>`) только при
  изменении куки. `up -d` заодно применяет изменения compose (например, новый маунт инсты).
- **Приватность.** На сервер уезжают только куки доменов `youtube.com`/`google.com`
  (для YouTube) и `instagram.com` (для Instagram). YouTube-авторизация требует и
  `google.com`-куки — это весь твой Google-сеанс, иначе видео 18+/приватные не откроются.
- **Chrome 127+ (App-Bound Encryption).** Если из Chrome/Edge/Brave куки не вытаскиваются —
  поставь `COOKIE_BROWSER=firefox`, закрой браузер на время выгрузки или укажи отдельный профиль.
