FROM python:3.14.8-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Рабочая директория
WORKDIR /app

# Копируем проверенные версии зависимостей
COPY pyproject.toml uv.lock ./

# Проверяем lockfile и используем Python базового образа без загрузки другого интерпретатора
RUN uv sync --locked --no-dev --no-install-project --no-python-downloads

# Копируем исходный код
COPY src/ ./src/

# Создаём директорию для загрузок
RUN mkdir -p /app/downloads

# Используем venv, созданный uv sync
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Запуск бота
CMD ["python", "-m", "src.main"]

