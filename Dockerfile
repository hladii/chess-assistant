FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5050 \
    OPEN_BROWSER=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish \
    && ln -sf /usr/games/stockfish /usr/local/bin/stockfish \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY assistant.py app.py auth.py db.py i18n.py ./
COPY templates ./templates
COPY static ./static
COPY translations ./translations
COPY scripts ./scripts

EXPOSE 5050

CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
