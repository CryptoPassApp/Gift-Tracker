# GiftTracker — production image (Python 3.11)
# Сервис api:  CMD по умолчанию (uvicorn)
# Сервис bot:  command: python -m bot.main (см. docker-compose.yml)

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала только зависимости — слой кэшируется отдельно от кода.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Код проекта.
COPY bot ./bot
COPY api ./api
COPY static ./static

# Каталог SQLite (примонтирован как volume в docker-compose).
RUN mkdir -p /app/data

EXPOSE 8000

# По умолчанию поднимаем API + витрину Mini App.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
