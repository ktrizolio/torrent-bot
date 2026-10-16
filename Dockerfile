# Uma única imagem com o scraper e o bot. O docker-compose.yml sobe dois containers a partir dela.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo

WORKDIR /app

COPY scraper/requirements.txt requirements-scraper.txt
COPY bot/requirements.txt requirements-bot.txt
RUN pip install --no-cache-dir -r requirements-scraper.txt -r requirements-bot.txt

COPY scraper/*.py scraper/
COPY bot/*.py bot/
