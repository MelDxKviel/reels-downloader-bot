FROM python:3.12-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Рабочая директория
WORKDIR /app

# Копируем зависимости (lockfile создаём внутри образа, т.к. в репозитории его может не быть)
COPY pyproject.toml ./

# Генерируем lock и устанавливаем зависимости
RUN uv lock && uv sync --frozen --no-dev --no-install-project

# Копируем исходный код
COPY src/ ./src/

# Создаём директорию для загрузок
RUN mkdir -p /app/downloads

# Используем venv, созданный uv sync
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Запуск бота
CMD ["python", "-m", "src.main"]

