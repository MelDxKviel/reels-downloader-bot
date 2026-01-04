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

# Копируем файлы зависимостей
COPY pyproject.toml uv.lock* ./

# Устанавливаем зависимости
RUN uv sync --frozen --no-dev --no-install-project

# Копируем исходный код
COPY src/ ./src/

# Устанавливаем проект
RUN uv sync --frozen --no-dev

# Создаём директорию для загрузок
RUN mkdir -p /app/downloads

# Запуск бота
CMD ["uv", "run", "python", "-m", "src.main"]

