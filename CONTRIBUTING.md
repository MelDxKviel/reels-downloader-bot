# Contributing

Thanks for your interest in contributing to Reels Downloader Bot.

## Development setup

**Requirements:** Python 3.11+, [uv](https://docs.astral.sh/uv/), PostgreSQL, FFmpeg.

```bash
git clone https://github.com/meldxkviel/reels-downloader-bot.git
cd reels-downloader-bot
uv sync
cp .env.example .env  # fill in BOT_TOKEN, ADMIN_USERS, DATABASE_URL
uv run alembic upgrade head
uv run python -m src.main
```

Alternatively, spin up the full stack with Docker:

```bash
docker compose -f docker-compose.local.yml up -d
```

## Code style

The project uses **ruff** (100-char line length, rules E/F/I/W):

```bash
uv run ruff check src/   # lint
uv run ruff format src/  # format
```

Run both before committing. PRs with lint errors will not be merged.

## Project conventions

- **HTML parse mode** is set globally on the Bot instance — use `<code>`, `<b>` tags directly in message strings.
- **Emoji prefixes** on user-facing messages follow the pattern `✅ ❌ ⏳ 📤`. Match existing patterns when adding handlers.
- **Database sessions** — always use `async with session:` context managers; never reuse a session across calls.
- **Access checks** are enforced in `UserAccessMiddleware` before any handler runs — handlers must not re-check access.
- **Tests** live in `tests/unit/`. Run them with `uv run pytest tests/ -v --tb=short`. CI runs the same command on every PR — make sure tests pass locally before pushing.

## Database migrations

When you change a SQLAlchemy model, generate and include a migration:

```bash
uv run alembic revision --autogenerate -m "short description"
uv run alembic upgrade head
```

Commit the generated file in `alembic/versions/` alongside the model change.

## Submitting changes

1. Fork the repository and create a feature branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```
2. Keep commits focused — one logical change per commit.
3. Open a pull request against `main` with a clear description of what changed and why.
4. Make sure `ruff check` and `ruff format --check` both pass.

## Adding a new platform

1. Add URL pattern detection in `src/bot/handlers/download.py`.
2. Add platform-specific yt-dlp options (cookies, format selection, etc.) in `VideoDownloader._get_ydl_opts()` inside `src/services/downloader.py`.
3. Ensure the platform name returned by `_detect_platform()` is consistent with what gets recorded in `DownloadStats`.

## Reporting issues

Open a GitHub issue with:
- Bot version / commit hash
- Steps to reproduce
- Relevant log output (`docker compose logs bot` or terminal output)
- The URL that caused the problem (you can redact personal tokens/params)
